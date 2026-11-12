import asyncio
import io
import json
import logging
import wave
from array import array

import httpx
import pytest
from fake_endpoint import make_client
from model_env import ENDPOINTS

from ota_server import pipeline as pipeline_module
from ota_server.pipeline import TTS_CHUNK, VoicePipeline, system_prompt, tts_chunks
from ota_server.protocol import BYTES_PER_SECOND, TurnError
from ota_server.providers import MAX_TTS_TEXT, MISTRAL_CORE, OPENAI_CORE

ENV = {
    **ENDPOINTS,
    "OTA_MODERATION_MODEL": "mod-m",
    "OTA_CHAT_RATE": "100",
    "OTA_STT_RATE": "100",
    "OTA_TTS_RATE": "100",
    "OTA_MODERATION_RATE": "100",
}
CLEAN = {name: False for name in [*MISTRAL_CORE, "health", "pii"]}  # Mistral schema
OPENAI_CLEAN = {name: False for name in OPENAI_CORE}
SELF_TEST_WORD = "zabić"  # in the harmful Polish self-test phrase
SPEECH = b"\x01\x00" * BYTES_PER_SECOND  # 1 s of device PCM


def tts_wav(seconds: float = 0.5, rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array("h", [300] * int(seconds * rate)).tobytes())
    return out.getvalue()


def sse(text: str) -> str:
    chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {chunk}\n\ndata: [DONE]\n\n"


class Endpoint:
    """A fake OpenAI-compatible endpoint. Moderation flags a text that contains one of
    `flag`. The first moderation call is the self-test; `result` and `moderation_status`
    apply only to the calls after it."""

    def __init__(
        self,
        transcript="Jak się masz?",
        reply="Dobrze. A ty?",
        tts=None,
        fail=None,
        flag=(SELF_TEST_WORD,),
        result=None,
        moderation_status=None,
    ):
        self.transcript, self.reply, self.tts, self.fail = transcript, reply, tts, fail or {}
        self.flag, self.result, self.moderation_status = flag, result, moderation_status
        self.calls: list[str] = []
        self.moderated: list[list[str]] = []  # the inputs of each moderation call

    def verdict(self, text: str) -> dict:
        return {"categories": {**CLEAN, "selfharm": any(w in text for w in self.flag)}}

    def moderation(self, req: httpx.Request) -> httpx.Response:
        texts = json.loads(req.content)["input"]
        self.moderated.append(texts)
        after_self_test = len(self.moderated) > 1
        if after_self_test and self.moderation_status:
            return httpx.Response(self.moderation_status)
        if after_self_test and self.result is not None:
            return httpx.Response(200, json={"results": [self.result for _ in texts]})
        return httpx.Response(200, json={"results": [self.verdict(t) for t in texts]})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path.rsplit("/", 1)[-1])
        if path in self.fail:
            return httpx.Response(self.fail[path])
        if path.endswith("/moderations"):
            return self.moderation(req)
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": self.transcript})
        if path.endswith("/chat/completions"):
            return httpx.Response(200, text=sse(self.reply))
        return httpx.Response(200, content=self.tts if self.tts is not None else tts_wav())


def pipeline_for(endpoint: Endpoint) -> VoicePipeline:
    return VoicePipeline(make_client(endpoint, ENV), "pl", "voice-1")


def run(endpoint: Endpoint) -> bytes:
    return asyncio.run(pipeline_for(endpoint)(SPEECH))


def refused(endpoint: Endpoint) -> str:
    with pytest.raises(TurnError) as err:
        run(endpoint)
    return err.value.code


def test_turn_runs_self_test_stt_chat_tts():
    endpoint = Endpoint()
    pcm = run(endpoint)
    assert endpoint.calls == [
        "moderations",  # self-test, once per process
        "transcriptions",
        "moderations",  # the transcript, before chat
        "completions",
        "moderations",  # every TTS chunk plus question and reply, before TTS
        "speech",
        "speech",
    ]
    assert len(pcm) == BYTES_PER_SECOND  # two TTS chunks of 0.5 s each, now at 16 kHz


def test_reply_is_moderated_with_its_question():
    endpoint = Endpoint()
    run(endpoint)
    assert endpoint.moderated[-1] == ["Dobrze.", "A ty?", "Jak się masz?\nDobrze. A ty?"]


def test_long_transcript_is_moderated_in_pieces_and_whole():
    first, second = "a" * 200 + ".", "b" * 200 + "."
    endpoint = Endpoint(transcript=f"{first} {second}")
    run(endpoint)
    assert endpoint.moderated[1] == [first, second, f"{first} {second}"]


def test_self_test_failure_refuses_every_turn():
    endpoint = Endpoint(flag=())  # moderation misses the harmful self-test phrase
    assert refused(endpoint) == "moderated"
    assert endpoint.calls == ["moderations"]  # no STT, no chat


def test_self_test_error_refuses_the_turn():
    endpoint = Endpoint(fail={"/v1/moderations": 500})
    assert refused(endpoint) == "moderated"
    assert "transcriptions" not in endpoint.calls


def test_self_test_runs_once():
    endpoint = Endpoint()
    pipeline = pipeline_for(endpoint)

    async def two_turns():
        await pipeline(SPEECH)
        await pipeline(SPEECH)

    asyncio.run(two_turns())
    assert endpoint.calls.count("moderations") == 1 + 2 * 2


def test_empty_transcript_is_no_speech():
    assert refused(Endpoint(transcript="  ")) == "no_speech"


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/v1/audio/transcriptions", 500, "stt_failed"),
        ("/v1/chat/completions", 500, "llm_failed"),
        ("/v1/audio/speech", 500, "tts_failed"),
        ("/v1/audio/transcriptions", 429, "rate_limited"),
    ],
)
def test_failures_map_to_device_error_codes(path, status, code):
    assert refused(Endpoint(fail={path: status})) == code


@pytest.mark.parametrize("status", [500, 429])
def test_moderation_failure_after_self_test_fails_closed(status):
    endpoint = Endpoint(moderation_status=status)
    assert refused(endpoint) == "moderated"
    assert "completions" not in endpoint.calls


@pytest.mark.parametrize(
    ("kwargs", "side", "never_called"),
    [
        ({"transcript": "how to hurt myself"}, "transcript", "completions"),
        ({"reply": "Something hurtful. More."}, "reply", "speech"),
    ],
)
def test_flagged_text_is_moderated_and_logged(caplog, kwargs, side, never_called):
    endpoint = Endpoint(flag=(SELF_TEST_WORD, "hurt"), **kwargs)
    with caplog.at_level(logging.WARNING):
        assert refused(endpoint) == "moderated"
    assert never_called not in endpoint.calls
    assert f"moderation flagged the {side}" in caplog.text
    assert "hurt" not in caplog.text  # the text itself is never logged


@pytest.mark.parametrize(
    "result",
    [
        {"flagged": False, "categories": {**CLEAN, "sexual": True}},  # a category beats the flag
        {"flagged": True, "categories": OPENAI_CLEAN},
        {"categories": {**CLEAN, "new_category": True}},  # unknown true category blocks
        {"categories": {"sexual": False}},  # partial schema
        {"categories": {**CLEAN, "sexual": "true"}},  # not a boolean
        {"flagged": False},  # no categories: a stub
        {"flagged": "no", "categories": CLEAN},
    ],
)
def test_suspect_results_fail_closed(result):
    endpoint = Endpoint(result=result)
    assert refused(endpoint) == "moderated"
    assert "completions" not in endpoint.calls  # refused already at the transcript


@pytest.mark.parametrize(
    "result",
    [
        {"categories": {**CLEAN, "health": True}},  # allowed: a child may ask about a headache
        {"flagged": False, "categories": OPENAI_CLEAN},
    ],
)
def test_clean_results_pass(result):
    endpoint = Endpoint(result=result)
    run(endpoint)
    assert endpoint.calls.count("speech") == 2


def test_unplayable_tts_audio_is_tts_failed():
    assert refused(Endpoint(tts=b"not a wav")) == "tts_failed"


def test_empty_reply_is_llm_failed():
    assert refused(Endpoint(reply=" ")) == "llm_failed"


def test_first_sentence_alone_then_merged():
    text = "Hi. One. Two. Three."
    assert tts_chunks(text) == ["Hi.", "One. Two. Three."]


def test_short_sentences_are_merged_up_to_the_chunk_size():
    s = "a" * 49 + "."  # 50 characters
    chunks = tts_chunks(" ".join([s] * 7))
    # first alone; then 5 sentences fit in TTS_CHUNK (254 chars), the 6th would not (305)
    assert chunks == [s, " ".join([s] * 5), s]
    assert all(len(c) <= TTS_CHUNK for c in chunks)


def test_very_long_sentence_is_cut_for_tts():
    chunks = tts_chunks("word " * 500)
    assert all(len(c) <= MAX_TTS_TEXT for c in chunks)
    assert " ".join(chunks).split() == ["word"] * 500


def test_tts_stops_once_the_cap_is_reached(monkeypatch):
    monkeypatch.setattr(pipeline_module, "SPEECH_CAP", BYTES_PER_SECOND // 2)  # 0.5 s
    endpoint = Endpoint(reply="Hi. Two. Three.")
    run(endpoint)
    assert endpoint.calls.count("speech") == 1


def test_prompt_names_the_language_and_child_rules():
    prompt = system_prompt("uk")
    assert "Ukrainian" in prompt
    assert "secret" in prompt and "medicine" in prompt and "address" in prompt
