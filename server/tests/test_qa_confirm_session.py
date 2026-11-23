"""QA cases of the confirm flow (docs/design/confirm-flow/qa/cases.md): session and pipeline.
QA-056 is the gate itself. Fakes only: a fake endpoint, a fake device socket, a fake MCP
connection and a fake sleep for the 10 s clock."""

import asyncio
import logging
from typing import Any

import pytest
from fakes import SPEECH
from qa_confirm_support import (
    MAIL,
    ask,
    device_rig,
    mcp_rig,
    n8n,
    run,
)
from test_confirm_release import DisconnectedSource, claimed_hub, confirm
from test_confirm_step import BrokenGate
from test_mcp_source import FakeConnection
from test_session_confirm import FakeSocket, Refuse, make, timer_stopped, turn
from test_session_confirm import fire as session_fire

import ota_server.hub.hub as hub_module
from ota_server import session as session_module
from ota_server.hub import confirm_text
from ota_server.hub.hub import McpHub
from ota_server.hub.types import CallCtx
from ota_server.mcp.port import RawResult
from ota_server.protocol import AUDIO_CHUNK
from ota_server.session import DeviceSession

# ---- QA-029 .. QA-037, QA-052 .. QA-055: turn rules ----


def test_qa_029_new_turn_during_the_wait_is_the_confirmation_turn() -> None:
    rig = device_rig(["what is the weather"])
    run(rig.turns(2))
    assert rig.spoken()[-1] == "Cancelled."
    assert rig.chats() == 1
    assert rig.device_calls() == []


def test_qa_030_timer_stops_when_the_confirmation_turn_starts() -> None:
    async def scenario() -> tuple[FakeSocket, bool]:
        session, ws, sleep, *_ = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "listen.start"})
        stopped = timer_stopped(session)
        sleep.released.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return ws, stopped

    ws, stopped = asyncio.run(scenario())
    assert stopped
    assert "confirm_timeout" not in ws.codes()


def test_qa_031_abort_during_the_question() -> None:
    async def scenario() -> Any:
        session, ws, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "abort", "turn": session.turn})
        sleep.released.set()
        await asyncio.sleep(0)
        await turn(session)
        return ws, lamp, script

    ws, lamp, script = asyncio.run(scenario())
    assert "confirm_timeout" not in ws.codes()
    assert script.answers == [None] and lamp.calls == []


def test_qa_032_abort_with_a_wrong_turn_id() -> None:
    async def scenario() -> Any:
        session, ws, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "abort", "turn": session.turn + 8})
        await turn(session)
        return lamp, script

    lamp, script = asyncio.run(scenario())
    assert script.answers == ["Done."] or len(script.answers) == 1 and script.answers[0] is not None
    assert len(lamp.calls) == 1


def test_qa_033_socket_closes_while_held() -> None:
    async def scenario() -> Any:
        session, _, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await session.close()
        second = DeviceSession(FakeSocket(), script, mcp_sources=[lamp], sleep=sleep)  # type: ignore[arg-type]
        await turn(second)
        return lamp, script

    lamp, script = asyncio.run(scenario())
    assert lamp.calls == [] and script.answers == [None]


def test_qa_034_listen_start_after_timeout_is_a_normal_turn() -> None:
    rig = device_rig(["hello"])

    async def scenario() -> None:
        await rig.turn()
        await rig.time_out()
        await rig.turn()

    run(scenario())
    assert rig.chats() == 2
    assert rig.device_calls() == []
    assert rig.spoken()[-1] == "Okay."


def test_qa_035_no_speech_in_the_confirmation_turn() -> None:
    rig = device_rig([""])

    async def scenario() -> None:
        await rig.turn()
        await rig.turn()
        rig.sleep.passed.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    run(scenario())
    assert rig.socket.codes() == ["no_speech"]
    assert rig.device_calls() == []
    assert rig.session.hub.gate.holding_turn() is None


def test_qa_036_too_many_turns_refusal_keeps_the_call() -> None:
    async def scenario() -> FakeSocket:
        session, ws, sleep, *_ = make("hold")
        await turn(session)
        session.turn_limit = Refuse()  # type: ignore[assignment]
        await session.handle_text({"type": "listen.start"})
        await session_fire(session, sleep)
        return ws

    ws = asyncio.run(scenario())
    assert ws.codes() == ["too_many_turns", "confirm_timeout"]


def test_qa_037_yes_after_a_refused_turn_still_works() -> None:
    async def scenario() -> Any:
        session, _, _, lamp, script = make("hold", "yes")
        await turn(session)
        limit = session.turn_limit
        session.turn_limit = Refuse()  # type: ignore[assignment]
        await session.handle_text({"type": "listen.start"})
        session.turn_limit = limit
        await turn(session)
        return lamp, script

    lamp, script = asyncio.run(scenario())
    assert len(lamp.calls) == 1


def test_qa_052_listen_start_during_the_question() -> None:
    async def scenario() -> Any:
        session, ws, _, lamp, script = make("question", "hold")
        await session.handle_text({"type": "listen.start"})
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 1})
        while not script.outcomes:
            await asyncio.sleep(0)
        await turn(session)
        return ws, lamp, script

    ws, lamp, script = asyncio.run(scenario())
    assert lamp.calls == []
    assert [o.code for o in script.outcomes] == ["confirm_required", "confirm_required"]
    assert [m["turn"] for m in ws.json() if m["type"] == "confirm.request"] == [2]


def test_qa_053_claimed_call_with_no_listen_stop() -> None:
    async def scenario() -> Any:
        session, _, _, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "listen.start"})
        await turn(session)
        return lamp, script

    lamp, script = asyncio.run(scenario())
    assert lamp.calls == [] and script.answers == [None]


def test_qa_054_confirmation_turn_does_not_repeat_the_hold() -> None:
    rig = device_rig(["yes"])
    run(rig.turns(2))
    assert rig.types().count("confirm.request") == 1
    assert rig.chats() == 1


def test_qa_055_repeated_listen_start_without_speech() -> None:
    rig = device_rig(["", "yes"])
    run(rig.turns(3))
    assert rig.device_calls() == []
    assert rig.chats() == 2


# ---- QA-041 .. QA-045: failures after yes ----


def test_qa_041_source_fails_after_yes() -> None:
    conn = FakeConnection(["send_mail", "list_mail"], result=RawResult(True, [("text", "boom")]))
    rig = mcp_rig(["yes", "again"], conn=conn)
    run(rig.turns(2))
    assert rig.spoken()[-1] == "That did not work."
    run(rig.turns(1))  # the session stays open
    assert rig.spoken()[-1] == "Okay."


def test_qa_042_source_times_out_after_yes() -> None:
    conn = FakeConnection(["send_mail", "list_mail"], delay=5.0)
    rig = mcp_rig(["yes"], conn=conn, timeout=0.05)
    run(rig.turns(2))
    assert rig.spoken()[-1] == "That did not work."


def test_qa_043_mcp_server_disconnected_before_yes() -> None:
    conn = FakeConnection(["send_mail", "list_mail"], raises=ConnectionError("closed"))
    rig = mcp_rig(["yes", "again"], conn=conn)
    run(rig.turns(2))
    assert rig.spoken()[-1] == "That did not work."
    run(rig.turns(1))
    assert rig.spoken()[-1] == "Okay."
    hub, _ = claimed_hub(DisconnectedSource())  # a raising source object too
    assert confirm(hub.turn_context(4), "yes") == "That did not work."


def test_qa_044_bug_in_the_confirm_step_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hub_module, "ConfirmGate", BrokenGate)
    source, conn = n8n()
    with caplog.at_level(logging.ERROR):
        out = ask(McpHub([source]), MAIL, CallCtx(turn=1))
    assert out.code == "confirm_required" and not out.ok
    assert conn.calls == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_qa_045_bug_in_the_yes_decision_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(text: str, language: str) -> bool:
        raise RuntimeError("bug")

    monkeypatch.setattr(confirm_text, "is_yes", broken)
    hub, source = claimed_hub()
    with caplog.at_level(logging.ERROR):
        reply = confirm(hub.turn_context(4), "yes")
    assert reply == "Cancelled."
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert hub.gate.holding_turn() is None and not hub.gate.claimed_by(4)
    assert source.calls == []


# ---- QA-046 .. QA-049: the pipeline cuts ----


def test_qa_046_moderation_refusal_in_the_confirmation_turn() -> None:
    rig = device_rig(["badword", "yes"])
    rig.endpoint.flagged.append("badword")
    run(rig.turns(3))
    assert rig.device_calls() == []
    assert rig.session.hub.gate.holding_turn() is None
    assert rig.chats() == 2


def test_qa_047_speech_to_text_failure_in_the_confirmation_turn() -> None:
    rig = device_rig([None, "yes"])
    run(rig.turns(3))
    assert rig.device_calls() == []
    assert rig.session.hub.gate.holding_turn() is None


def test_qa_048_moderation_cut_of_the_question() -> None:
    rig = device_rig([])
    rig.endpoint.flagged.append("set_volume")
    run(rig.turns(1))
    assert "confirm.request" not in rig.types()
    assert rig.device_calls() == []


def test_qa_049_cap_cut_during_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK)

    async def scenario() -> Any:
        session, ws, _, lamp, script = make("cap", "yes")
        await turn(session)
        await turn(session)
        return ws, lamp, script

    ws, lamp, script = asyncio.run(scenario())
    assert "confirm.request" not in ws.types()
    assert lamp.calls == [] and script.answers == [None]
