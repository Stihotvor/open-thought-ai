"""A voice turn through DeviceSession with VoicePipeline: what the device gets, in order."""

import asyncio

from test_pipeline import SPEECH, Endpoint, pipeline_for, self_test_only, tts_wav

from ota_server.protocol import MAX_CREDIT
from ota_server.session import DeviceSession


class FakeSocket(list):
    async def send_json(self, msg: dict | bytes) -> None:
        self.append(msg)

    send_bytes = send_json


def session_turn(endpoint: Endpoint) -> list:
    """Run one voice turn through a DeviceSession: the messages the device gets."""

    async def scenario() -> list:
        session = DeviceSession(FakeSocket(), pipeline_for(endpoint))  # type: ignore[arg-type]
        session.credit, session.open_turn = MAX_CREDIT, 1
        await session.reply(1, SPEECH)
        return list(session.ws)

    return asyncio.run(scenario())


def kinds(sent: list) -> list[str]:
    """Message kinds in order, with each run of audio frames as one "audio"."""
    out: list[str] = []
    for msg in sent:
        kind = "audio" if isinstance(msg, bytes) else msg["type"]
        kind = f"state {msg['state']}" if kind == "state" else kind
        if not (kind == "audio" and out and out[-1] == "audio"):
            out.append(kind)
    return out


def transcripts(sent: list) -> list[str]:
    return [m["text"] for m in sent if isinstance(m, dict) and m["type"] == "transcript"]


def test_device_message_order():
    sent = session_turn(Endpoint(tts=tts_wav(0.05)))
    order = ["state thinking", "transcript", "state speaking", "reply.start", "audio"]
    assert kinds(sent) == [*order, "reply.end"]
    assert {"type": "transcript", "turn": 1, "text": "Jak się masz?"} in sent
    assert all(len(text) <= 200 for text in transcripts(sent))


def test_long_transcript_goes_whole_to_chat_and_cut_to_the_device():
    text = "a" * 299 + "."
    endpoint = Endpoint(transcript=text, tts=tts_wav(0.05))
    sent = session_turn(endpoint)
    assert endpoint.chats[0][-1]["content"] == text
    assert transcripts(sent) == [text[:200]]


def test_flagged_transcript_sends_no_transcript():
    endpoint = Endpoint(flag=lambda text: self_test_only(text) or "masz" in text)
    sent = session_turn(endpoint)
    assert {"type": "error", "turn": 1, "code": "moderated"} in sent
    assert transcripts(sent) == []
