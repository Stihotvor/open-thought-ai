import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakes import (
    CANARY,
    CLEAN,
    OPENAI_CLEAN,
    SILENCE,
    SPEECH,
    Endpoint,
    pipeline_for,
    self_test_only,
    square,
)

from ota_server import pipeline as pipeline_module
from ota_server.audio import pcm_to_wav
from ota_server.pipeline import TTS_CHUNK, VoicePipeline, system_prompt, tts_chunks
from ota_server.protocol import BYTES_PER_SECOND, TurnError
from ota_server.providers import CALL_CAPS, MAX_TTS_TEXT, ProviderError
from ota_server.ratelimit import TurnBudget
from ota_server.reply import Notify, ReplyChunk, no_notify

FIRST, SECOND = "a" * 200 + ".", "b" * 200 + "."
LONG = f"{FIRST} {SECOND}"
DOSE_Q, DOSE_A = "How many pills can I take?", "Yes, take ten."


async def drain(pipeline: VoicePipeline, pcm: bytes = SPEECH, notify: Notify = no_notify) -> bytes:
    return b"".join([chunk.pcm async for chunk in pipeline(pcm, (), notify=notify)])


def run(endpoint: Endpoint, pcm: bytes = SPEECH, notify: Notify = no_notify) -> bytes:
    return asyncio.run(drain(pipeline_for(endpoint), pcm, notify))


def refused(endpoint: Endpoint, pcm: bytes = SPEECH, notify: Notify = no_notify) -> str:
    with pytest.raises(TurnError) as err:
        run(endpoint, pcm, notify)
    return err.value.code


def played_then_refused(endpoint: Endpoint) -> tuple[list[str], str]:
    """The chunk texts the turn yielded before it was refused, and the refusal code."""
    texts: list[str] = []

    async def turn() -> None:
        async for chunk in pipeline_for(endpoint)(SPEECH, ()):
            texts.append(chunk.text)

    with pytest.raises(TurnError) as err:
        asyncio.run(turn())
    return texts, err.value.code


def test_turn_runs_self_test_stt_chat_tts() -> None:
    endpoint = Endpoint()
    pcm = run(endpoint)
    assert endpoint.calls == [
        "moderations",  # self-test, once per process
        "transcriptions",
        "moderations",  # the transcript, before chat
        "completions",
        "moderations",  # chunk 1 plus question and reply so far, at the same time as TTS
        "speech",
        "moderations",  # chunk 2
        "speech",
    ]
    assert len(pcm) == BYTES_PER_SECOND  # two TTS chunks of 0.5 s each, now at 16 kHz


def test_reply_is_moderated_with_its_question() -> None:
    endpoint = Endpoint()
    run(endpoint)
    assert endpoint.moderated[-2] == ["Dobrze.", "Jak się masz?\nDobrze.", CANARY]
    assert endpoint.moderated[-1] == ["A ty?", "Jak się masz?\nDobrze. A ty?", CANARY]


def test_long_transcript_is_moderated_in_pieces_and_whole() -> None:
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
def test_self_test_failure_refuses_every_turn(kwargs: dict[str, Any]) -> None:
    endpoint = Endpoint(**kwargs)
    pipeline = pipeline_for(endpoint)

    async def two_turns() -> None:
        for _ in range(2):
            with pytest.raises(TurnError) as err:
                await drain(pipeline)
            assert err.value.code == "moderated"

    asyncio.run(two_turns())
    assert endpoint.calls == ["moderations", "moderations"]  # retried, never STT or chat
    assert not pipeline.moderation_checked


def test_self_test_error_refuses_the_turn() -> None:
    endpoint = Endpoint(fail={"/v1/moderations": 500})
    assert refused(endpoint) == "moderated"
    assert "transcriptions" not in endpoint.calls


def test_canary_catches_moderation_that_stopped_working() -> None:
    endpoint = Endpoint()
    pipeline = pipeline_for(endpoint)

    async def turns() -> None:
        await drain(pipeline)  # the self-test passes, the first turn speaks
        endpoint.flag = lambda text: False  # the endpoint now passes everything
        with pytest.raises(TurnError) as err:
            await drain(pipeline)
        assert err.value.code == "moderated"

    asyncio.run(turns())
    assert endpoint.calls.count("completions") == 1  # the second turn never reached chat
    assert not pipeline.moderation_checked  # the next turn runs the self-test again


def test_question_with_reply_input_is_capped() -> None:
    endpoint = Endpoint(transcript="q" * 2000 + ".", reply="r" * 3000 + ".")
    run(endpoint)
    context = endpoint.moderated[-1][-2]
    # The reply is one long sentence, cut for TTS at MAX_TTS_TEXT: the cuts join with a space.
    assert context == "q" * 499 + ".\n" + ("r" * MAX_TTS_TEXT + " " + "r" * 2000)[:1500]


def test_self_test_runs_once() -> None:
    endpoint = Endpoint()
    pipeline = pipeline_for(endpoint)

    async def two_turns() -> None:
        await drain(pipeline)
        await drain(pipeline)

    asyncio.run(two_turns())
    assert (
        endpoint.calls.count("moderations") == 1 + 2 * 3
    )  # self-test, then per turn: transcript and 2 chunks


def test_empty_transcript_is_no_speech_without_notify() -> None:
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    assert refused(Endpoint(transcript="  "), notify=notify) == "no_speech"
    assert notified == []


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/v1/audio/transcriptions", 500, "stt_failed"),
        ("/v1/chat/completions", 500, "llm_failed"),
        ("/v1/audio/speech", 500, "tts_failed"),
        ("/v1/audio/transcriptions", 429, "rate_limited"),
    ],
)
def test_failures_map_to_device_error_codes(path: str, status: int, code: str) -> None:
    assert refused(Endpoint(fail={path: status})) == code


# (first failing moderation call, blocked step): "audio" means no chunk was yielded.
STAGES = [(2, "completions"), (3, "audio")]


def assert_blocked(endpoint: Endpoint, blocked: str) -> None:
    """The turn is refused with `moderated`. "completions": chat never ran. "audio": no
    chunk was yielded (TTS may have run at the same time as moderation)."""
    played, code = played_then_refused(endpoint)
    assert code == "moderated"
    if blocked == "audio":
        assert played == []
    else:
        assert blocked not in endpoint.calls


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
def test_moderation_failure_fails_closed_at_each_stage(
    at: int, blocked: str, kwargs: dict[str, Any]
) -> None:
    endpoint = Endpoint(from_call=at, **kwargs)
    assert_blocked(endpoint, blocked)


@pytest.mark.parametrize(
    ("transcript", "reply", "flagged_text", "blocked"),
    [
        (LONG, "Ok.", SECOND, "completions"),  # only the second transcript piece
        (LONG, "Ok.", LONG, "completions"),  # only the whole transcript
        ("Jak się masz?", "Ok.", "Ok.", "audio"),  # only the reply chunk
        (DOSE_Q, DOSE_A, f"{DOSE_Q}\n{DOSE_A}", "audio"),  # only the question with the reply
    ],
)
def test_a_flag_on_any_single_input_refuses(
    transcript: str, reply: str, flagged_text: str, blocked: str
) -> None:
    endpoint = Endpoint(
        transcript=transcript,
        reply=reply,
        flag=lambda text: self_test_only(text) or text == flagged_text,
    )
    assert_blocked(endpoint, blocked)


@pytest.mark.parametrize(
    ("kwargs", "side", "never_called"),
    [
        ({"transcript": "how to hurt myself"}, "transcript", "completions"),
        ({"reply": "Something hurtful. More."}, "reply", "audio"),
    ],
)
def test_flagged_text_is_moderated_and_logged(
    caplog: pytest.LogCaptureFixture, kwargs: dict[str, Any], side: str, never_called: str
) -> None:
    endpoint = Endpoint(flag=lambda text: self_test_only(text) or "hurt" in text, **kwargs)
    with caplog.at_level(logging.WARNING):
        assert_blocked(endpoint, never_called)
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
def test_suspect_results_fail_closed(result: dict[str, Any], at: int, blocked: str) -> None:
    endpoint = Endpoint(result=result, from_call=at)
    assert_blocked(endpoint, blocked)


@pytest.mark.parametrize(
    "result",
    [
        {"categories": {**CLEAN, "health": True}},  # allowed: a child may ask about a headache
        {"flagged": False, "categories": OPENAI_CLEAN},
    ],
)
def test_clean_results_pass(result: dict[str, Any]) -> None:
    endpoint = Endpoint(result=result)
    run(endpoint)
    assert endpoint.calls.count("speech") == 2


def test_unplayable_tts_audio_is_tts_failed() -> None:
    assert refused(Endpoint(tts=b"not a wav")) == "tts_failed"


def test_empty_reply_is_llm_failed() -> None:
    assert refused(Endpoint(reply=" ")) == "llm_failed"


def test_first_sentence_alone_then_merged() -> None:
    text = "Hi. One. Two. Three."
    assert tts_chunks(text) == ["Hi.", "One. Two. Three."]


def test_short_sentences_are_merged_up_to_the_chunk_size() -> None:
    s = "a" * 49 + "."  # 50 characters
    chunks = tts_chunks(" ".join([s] * 7))
    # first alone; then 5 sentences fit in TTS_CHUNK (254 chars), the 6th would not (305)
    assert chunks == [s, " ".join([s] * 5), s]
    assert all(len(c) <= TTS_CHUNK for c in chunks)


def test_very_long_sentence_is_cut_for_tts() -> None:
    chunks = tts_chunks("word " * 500)
    assert all(len(c) <= MAX_TTS_TEXT for c in chunks)
    assert " ".join(chunks).split() == ["word"] * 500


def test_first_chunk_is_yielded_before_the_next_tts_call() -> None:
    endpoint = Endpoint(reply="Hi. Two. Three.")

    async def first_chunk() -> bytes:
        chunks = pipeline_for(endpoint)(SPEECH, ())
        chunk = await anext(chunks)
        await chunks.aclose()  # as the session does at the cap: no more TTS calls
        return chunk.pcm

    assert len(asyncio.run(first_chunk())) == BYTES_PER_SECOND // 2
    assert endpoint.calls.count("speech") == 1


def test_prompt_names_the_language_and_child_rules() -> None:
    prompt = system_prompt("uk")
    assert "Ukrainian" in prompt
    assert "self-harm" in prompt and "medicine" in prompt and "password" in prompt
    assert '"continue"' in prompt


def test_playback_time_does_not_count_against_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline_module, "new_turn_budget", lambda: TurnBudget(max_calls=CALL_CAPS, deadline_s=2.0)
    )
    endpoint = Endpoint(reply="Hi. " + "Two. " * 70 + "Three.")  # three TTS chunks

    async def slow_listener() -> int:
        count = 0
        async for _ in pipeline_for(endpoint)(SPEECH, ()):
            count += 1
            await asyncio.sleep(1.5)  # the device plays: 3 x 1.5 s is past the 2 s deadline
        return count

    assert asyncio.run(slow_listener()) == 3


def test_a_flagged_chunk_is_not_played() -> None:
    endpoint = Endpoint(flag=lambda t: self_test_only(t) or "ty?" in t)
    assert played_then_refused(endpoint) == (["Dobrze."], "moderated")


def test_a_flag_on_a_later_chunk_stops_the_reply_there() -> None:
    endpoint = Endpoint(reply="One. Two. Three.", flag=lambda t: self_test_only(t) or "Two" in t)
    assert played_then_refused(endpoint) == (["One."], "moderated")


def test_chunks_carry_their_text_and_question() -> None:
    async def collect() -> list[ReplyChunk]:
        return [chunk async for chunk in pipeline_for(Endpoint())(SPEECH, ())]

    chunks = asyncio.run(collect())
    assert [(c.text, c.question) for c in chunks] == [
        ("Dobrze.", "Jak się masz?"),
        ("A ty?", "Jak się masz?"),
    ]


def test_history_goes_to_chat_in_order() -> None:
    history = (("q1", "r1"), ("q2", "r2"))
    messages = pipeline_for(Endpoint()).messages("q3", history)
    assert messages[0]["role"] == "system"
    assert [m["content"] for m in messages[1:]] == ["q1", "r1", "q2", "r2", "q3"]
    assert [m["role"] for m in messages[1:]] == ["user", "assistant"] * 2 + ["user"]


def test_history_keeps_the_newest_turns_that_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "MAX_HISTORY_CHARS", 10)
    history = (("old", "aaaa"), ("new", "bbbb"))  # 7 characters each
    messages = pipeline_for(Endpoint()).messages("q", history)
    assert [m["content"] for m in messages[1:]] == ["new", "bbbb", "q"]


def test_the_chat_call_gets_the_history() -> None:
    endpoint = Endpoint()

    async def turn() -> None:
        async for _ in pipeline_for(endpoint)(SPEECH, (("q1", "r1"),)):
            pass

    asyncio.run(turn())
    assert [m["content"] for m in endpoint.chats[0][1:]] == ["q1", "r1", "Jak się masz?"]


@pytest.mark.parametrize("pcm", [SILENCE * 3, square(3, 300)], ids=["silence", "low-noise"])
def test_silence_makes_no_provider_call(pcm: bytes) -> None:
    endpoint = Endpoint()
    assert refused(endpoint, pcm) == "no_speech"
    assert endpoint.calls == []  # no self-test, no STT


def test_odd_byte_count_is_internal() -> None:
    endpoint = Endpoint()
    assert refused(endpoint, SPEECH + b"\x00") == "internal"
    assert endpoint.calls == []


def test_stt_gets_trimmed_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    def spy(pcm: bytes) -> bytes:
        sizes.append(len(pcm))
        return pcm_to_wav(pcm)

    monkeypatch.setattr(pipeline_module, "pcm_to_wav", spy)
    run(Endpoint(), SILENCE + SPEECH + SILENCE)
    assert len(sizes) == 1
    assert BYTES_PER_SECOND <= sizes[0] <= 1.5 * BYTES_PER_SECOND


def test_first_audio_comes_before_the_chat_stream_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentence 1 is moderated and spoken while chat still streams: the chat fake holds
    sentence 2 back until the turn yielded the first audio."""
    pipeline = pipeline_for(Endpoint())
    played = asyncio.Event()

    async def chat(messages: Any, budget: Any) -> AsyncIterator[str]:
        yield "Dobrze. "
        await asyncio.wait_for(played.wait(), 5)
        yield "A ty?"

    monkeypatch.setattr(pipeline.client, "chat", chat)

    async def turn() -> list[str]:
        texts = []
        async for chunk in pipeline(SPEECH, ()):
            texts.append(chunk.text)
            played.set()
        return texts

    assert asyncio.run(turn()) == ["Dobrze.", "A ty?"]


def test_a_chat_error_after_sentence_1_is_llm_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = pipeline_for(Endpoint())

    async def chat(messages: Any, budget: Any) -> AsyncIterator[str]:
        yield "Dobrze. "
        raise ProviderError("chat")

    monkeypatch.setattr(pipeline.client, "chat", chat)
    texts: list[str] = []

    async def turn() -> None:
        async for chunk in pipeline(SPEECH, ()):
            texts.append(chunk.text)

    with pytest.raises(TurnError) as err:
        asyncio.run(turn())
    assert err.value.code == "llm_failed"
    assert texts in ([], ["Dobrze."])  # sentence 1 may play before the error arrives
