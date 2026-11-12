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
from ota_server.pipeline import SELF_TEST, TTS_CHUNK, VoicePipeline, system_prompt, tts_chunks
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
CANARY = SELF_TEST["pl"][0]  # rides along in every moderation batch
SPEECH = b"\x01\x00" * BYTES_PER_SECOND  # 1 s of device PCM
FIRST, SECOND = "a" * 200 + ".", "b" * 200 + "."
LONG = f"{FIRST} {SECOND}"
DOSE_Q, DOSE_A = "How many pills can I take?", "Yes, take ten."


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


def self_test_only(text: str) -> bool:
    return SELF_TEST_WORD in text


class Endpoint:
    """A fake OpenAI-compatible endpoint. `flag(text)` decides each moderation verdict.
    Moderation calls are numbered: 1 is the self-test, 2 the transcript, 3 the reply.
    `result`, `moderation_status` and `result_count_delta` apply from call `from_call` on,
    so a retried 429 or a repeated self-test meets the same answer."""

    def __init__(
        self,
        transcript="Jak się masz?",
        reply="Dobrze. A ty?",
        tts=None,
        fail=None,
        flag=self_test_only,
        result=None,
        moderation_status=None,
        result_count_delta=0,
        from_call=2,
    ):
        self.transcript, self.reply, self.tts, self.fail = transcript, reply, tts, fail or {}
        self.flag, self.result, self.moderation_status = flag, result, moderation_status
        self.result_count_delta, self.from_call = result_count_delta, from_call
        self.calls: list[str] = []
        self.moderated: list[list[str]] = []  # the inputs of each moderation call

    def verdict(self, text: str) -> dict:
        return {"categories": {**CLEAN, "selfharm": self.flag(text)}}

    def moderation(self, req: httpx.Request) -> httpx.Response:
        texts = json.loads(req.content)["input"]
        self.moderated.append(texts)
        aimed = len(self.moderated) >= self.from_call
        if aimed and self.moderation_status:
            return httpx.Response(self.moderation_status)
        results = [self.verdict(t) for t in texts]
        if aimed and self.result is not None:  # the canary keeps its true verdict
            results = [
                r if self_test_only(t) else self.result for t, r in zip(texts, results, strict=True)
            ]
        if aimed and self.result_count_delta < 0:
            results = results[: self.result_count_delta]
        if aimed and self.result_count_delta > 0:
            results += results[:1] * self.result_count_delta
        return httpx.Response(200, json={"results": results})

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
    assert endpoint.moderated[-1] == ["Dobrze.", "A ty?", "Jak się masz?\nDobrze. A ty?", CANARY]


def test_long_transcript_is_moderated_in_pieces_and_whole():
    endpoint = Endpoint(transcript=LONG)
    run(endpoint)
    assert endpoint.moderated[1] == [FIRST, SECOND, LONG, CANARY]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"flag": lambda text: False},  # misses the harmful phrase
        {"flag": lambda text: True},  # flags the harmless phrase too
        {"result_count_delta": -1, "from_call": 1},
        {"result_count_delta": 1, "from_call": 1},
    ],
)
def test_self_test_failure_refuses_every_turn(kwargs):
    endpoint = Endpoint(**kwargs)
    pipeline = pipeline_for(endpoint)

    async def two_turns():
        for _ in range(2):
            with pytest.raises(TurnError) as err:
                await pipeline(SPEECH)
            assert err.value.code == "moderated"

    asyncio.run(two_turns())
    assert endpoint.calls == ["moderations", "moderations"]  # retried, never STT or chat
    assert not pipeline.moderation_checked


def test_self_test_error_refuses_the_turn():
    endpoint = Endpoint(fail={"/v1/moderations": 500})
    assert refused(endpoint) == "moderated"
    assert "transcriptions" not in endpoint.calls


def test_canary_catches_moderation_that_stopped_working():
    endpoint = Endpoint()
    pipeline = pipeline_for(endpoint)

    async def turns():
        await pipeline(SPEECH)  # the self-test passes, the first turn speaks
        endpoint.flag = lambda text: False  # the endpoint now passes everything
        with pytest.raises(TurnError) as err:
            await pipeline(SPEECH)
        assert err.value.code == "moderated"

    asyncio.run(turns())
    assert endpoint.calls.count("completions") == 1  # the second turn never reached chat
    assert not pipeline.moderation_checked  # the next turn runs the self-test again


def test_question_with_reply_input_is_capped():
    endpoint = Endpoint(transcript="q" * 2000 + ".", reply="r" * 3000 + ".")
    run(endpoint)
    context = endpoint.moderated[-1][-2]
    assert context == "q" * 499 + ".\n" + "r" * 1500


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


STAGES = [(2, "completions"), (3, "speech")]  # (first failing moderation call, blocked call)


@pytest.mark.parametrize(("at", "blocked"), STAGES)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"moderation_status": 500},
        {"moderation_status": 429},
        {"result_count_delta": -1},
        {"result_count_delta": 1},
    ],
)
def test_moderation_failure_fails_closed_at_each_stage(at, blocked, kwargs):
    endpoint = Endpoint(from_call=at, **kwargs)
    assert refused(endpoint) == "moderated"
    assert blocked not in endpoint.calls


@pytest.mark.parametrize(
    ("transcript", "reply", "flagged_text", "blocked"),
    [
        (LONG, "Ok.", SECOND, "completions"),  # only the second transcript piece
        (LONG, "Ok.", LONG, "completions"),  # only the whole transcript
        ("Jak się masz?", "Dobrze. A ty?", "A ty?", "speech"),  # only the last reply chunk
        (DOSE_Q, DOSE_A, f"{DOSE_Q}\n{DOSE_A}", "speech"),  # only the question with the reply
    ],
)
def test_a_flag_on_any_single_input_refuses(transcript, reply, flagged_text, blocked):
    endpoint = Endpoint(
        transcript=transcript,
        reply=reply,
        flag=lambda text: self_test_only(text) or text == flagged_text,
    )
    assert refused(endpoint) == "moderated"
    assert blocked not in endpoint.calls


@pytest.mark.parametrize(
    ("kwargs", "side", "never_called"),
    [
        ({"transcript": "how to hurt myself"}, "transcript", "completions"),
        ({"reply": "Something hurtful. More."}, "reply", "speech"),
    ],
)
def test_flagged_text_is_moderated_and_logged(caplog, kwargs, side, never_called):
    endpoint = Endpoint(flag=lambda text: self_test_only(text) or "hurt" in text, **kwargs)
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
@pytest.mark.parametrize(("at", "blocked"), STAGES)
def test_suspect_results_fail_closed(result, at, blocked):
    endpoint = Endpoint(result=result, from_call=at)
    assert refused(endpoint) == "moderated"
    assert blocked not in endpoint.calls


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
