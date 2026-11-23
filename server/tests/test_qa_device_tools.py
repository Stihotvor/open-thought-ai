"""QA cases of docs/design/device-tools/qa/cases.md (issue #27), end to end: QA-001..030, 056.

A fake chat endpoint follows a script of rounds, a fake socket plays the device. No board,
no real API. Cases already covered whole by older tests are mapped in qa/results.md.
Timeouts, bad model output and results to the model: test_qa_device_tools_failures.py."""

import asyncio
import json
import logging
from typing import Any

import pytest
from qa_device_support import (
    STATUS,
    TEXT,
    TOOL_ROUND,
    VOL,
    Run,
    limit_texts,
    raw_result,
    refused,
    result,
)
from test_device_tools_e2e import DeviceSocket, ends, run_turn, tool_calls, tool_messages

from ota_server.hub.device_source import DeviceSource
from ota_server.hub.hub import McpHub
from ota_server.hub.types import ToolOutcome, ToolSpec
from ota_server.session import DeviceSession

# ---- functional ----


def test_qa_001_volume_by_voice() -> None:
    run = Run([TOOL_ROUND, ("Volume is 30.", [])], result(result={"level": 30})).go()
    [call] = tool_calls(run.socket)
    assert (call["name"], call["args"]) == ("set_volume", {"level": 30})
    assert '"level": 30' in tool_messages(run.endpoint.chats[1])[0]
    assert run.heard == "Volume is 30."


def test_qa_002_status_question() -> None:
    answer = result(result={"battery": 80, "volume": 30})
    run = Run([("", [STATUS]), TEXT], answer).go()
    [call] = tool_calls(run.socket)
    assert (call["name"], call["args"]) == ("get_status", {})
    assert '"battery": 80' in tool_messages(run.endpoint.chats[1])[0]


def test_qa_003_face_change() -> None:
    run = Run([("", [("device__set_face", '{"face": "listening"}')]), TEXT]).go()
    [call] = tool_calls(run.socket)
    assert (call["name"], call["args"]) == ("set_face", {"face": "listening"})


def test_qa_004_tool_list_for_the_chat_model() -> None:
    run = Run([("Hello.", [])]).go()
    tools = run.endpoint.bodies[0]["tools"]
    assert [t["function"]["name"] for t in tools] == [
        "device__set_volume",
        "device__set_face",
        "device__get_status",
    ]
    assert all(t["function"]["parameters"]["type"] == "object" for t in tools)


def test_qa_005_extra_tool_source() -> None:
    class Ping:
        name = "fake"
        timeout = 1.0

        def __init__(self) -> None:
            self.ran = 0

        def specs(self) -> list[ToolSpec]:
            return [
                ToolSpec(
                    "fake", "ping", "Ping.", {"type": "object", "properties": {}}, confirm=False
                )
            ]

        async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
            self.ran += 1
            return ToolOutcome(True, "pong")

    ping = Ping()
    run = Run([("", [("fake__ping", "{}")]), TEXT])
    session = run.session
    session.turn_tools = lambda turn=0: McpHub(  # type: ignore[method-assign]
        [DeviceSource(session.ws.send_json, session.pending), ping]
    ).turn_context(turn)
    run.go()
    assert ping.ran == 1
    assert tool_messages(run.endpoint.chats[1])[0].endswith("pong")
    assert not tool_calls(run.socket)
    names = [t["function"]["name"] for t in run.endpoint.bodies[0]["tools"]]
    assert "fake__ping" in names


def test_qa_006_no_confirm() -> None:
    run = Run([("", [("device__set_volume", '{"level": 10}')]), TEXT]).go()
    assert len(tool_calls(run.socket)) == 1
    types = {m["type"] for m in run.socket if isinstance(m, dict)}
    assert "confirm.request" not in types
    states = [m["state"] for m in run.socket if isinstance(m, dict) and m["type"] == "state"]
    assert "confirm" not in states


# ---- failures from the model ----


def test_qa_007_unknown_tool_name() -> None:
    refused(("device__reboot", "{}"), "unknown_tool")


def test_qa_008_name_without_namespace() -> None:
    refused(("set_volume", '{"level": 30}'), "unknown_tool")


def test_qa_009_level_above_range() -> None:
    refused(("device__set_volume", '{"level": 150}'), "invalid_args")


def test_qa_010_missing_required_field() -> None:
    refused(("device__set_volume", "{}"), "invalid_args")


def test_qa_011_wrong_type_and_unknown_face() -> None:
    refused(("device__set_face", '{"face": "angry"}'), "invalid_args")
    refused(("device__set_volume", '{"level": "loud"}'), "invalid_args")


def test_qa_012_negative_level() -> None:
    refused(("device__set_volume", '{"level": -1}'), "invalid_args")


def test_qa_013_boundary_levels() -> None:
    calls = [("device__set_volume", '{"level": 0}'), ("device__set_volume", '{"level": 100}')]
    run = Run([("", calls), TEXT]).go()
    assert [c["args"]["level"] for c in tool_calls(run.socket)] == [0, 100]


# ---- results from the device ----


def test_qa_014_unknown_result_id(caplog: pytest.LogCaptureFixture) -> None:
    run = Run([("Hello.", [])])
    with caplog.at_level(logging.INFO):
        asyncio.run(
            run.session.handle_text({"type": "tool.result", "id": "999", "ok": True, "result": {}})
        )
    assert "tool.result" in caplog.text
    run.go()
    assert run.heard == "Hello."
    assert ends(run.socket) == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def test_qa_015_duplicate_result() -> None:
    def answer(call_id: int) -> list[dict[str, Any]]:
        return [
            {"type": "tool.result", "id": call_id, "ok": True, "result": "first"},
            {"type": "tool.result", "id": call_id, "ok": False, "error": "late"},
        ]

    run = Run([TOOL_ROUND, TEXT], answer).go()
    [content] = tool_messages(run.endpoint.chats[1])
    assert content.endswith("first") and "late" not in content
    assert ends(run.socket)[-1]["reason"] == "done"


def test_qa_017_malformed_result_with_waiting_id() -> None:
    run = Run([TOOL_ROUND, TEXT], raw_result()).go()
    assert run.codes == ["device_error"]
    assert "malformed" in tool_messages(run.endpoint.chats[1])[0]
    assert ends(run.socket)[-1]["reason"] == "done"


def test_qa_018_wrong_types_in_result() -> None:
    run = Run([TOOL_ROUND, TEXT], raw_result(ok="yes", result=5)).go()
    assert run.codes == ["device_error"]
    assert "malformed" in tool_messages(run.endpoint.chats[1])[0]
    assert ends(run.socket)[-1]["reason"] == "done"


def test_qa_019_malformed_result_unknown_id() -> None:
    run = Run([("Hello.", [])])
    asyncio.run(run.session.handle_text({"type": "tool.result", "ok": 1}))
    run.go()
    assert run.heard == "Hello."


# ---- the call cap ----


def test_qa_020_five_calls() -> None:
    run = Run([("", [STATUS] * 5), TEXT]).go()
    assert len(tool_calls(run.socket)) == 4
    last = limit_texts(run, 1, 4)
    assert len(last) == 1 and "tool call limit reached" in last[0]


def test_qa_021_six_calls() -> None:
    calls = [("device__set_volume", json.dumps({"level": n})) for n in range(10, 70, 10)]
    run = Run([("", calls), TEXT]).go()
    assert [c["args"]["level"] for c in tool_calls(run.socket)] == [10, 20, 30, 40]
    assert run.codes[4:] == ["call_limit", "call_limit"]


def test_qa_022_cap_counts_across_rounds() -> None:
    run = Run([("", [STATUS] * 3), ("", [STATUS] * 3), TEXT]).go()
    assert len(tool_calls(run.socket)) == 4
    assert run.codes == ["call_limit" if i >= 4 else None for i in range(6)]


def test_qa_023_cap_resets_on_next_turn() -> None:
    run = Run([("", [STATUS] * 5), ("ok", []), ("", [STATUS]), TEXT])
    asyncio.run(run_turn(run.session))
    asyncio.run(run_turn(run.session))
    assert len(tool_calls(run.socket)) == 5
    assert run.codes[:5] == [None, None, None, None, "call_limit"]
    assert run.codes[5] is None


def test_qa_056_calls_over_cap_do_not_run() -> None:
    calls = [("device__set_volume", '{"level": 50}')] * 8
    run = Run([("", calls), TEXT]).go()
    assert len(tool_calls(run.socket)) == 4
    assert run.codes[4:] == ["call_limit"] * 4


# ---- speech around the calls ----


def test_qa_027_tool_call_with_no_text() -> None:
    run = Run([("", [("device__set_face", '{"face": "thinking"}')]), TEXT]).go()
    assert run.heard == "Done."
    assert ends(run.socket) == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def test_qa_028_text_before_a_tool_call_is_spoken() -> None:
    run = Run([("Setting it now.", [VOL]), TEXT]).go()
    assert run.heard.index("Setting it now.") < run.heard.index("Done.")


def test_qa_029_half_sentence_before_a_tool_call() -> None:
    run = Run([("Setting the volume", [VOL]), TEXT]).go()
    assert run.endpoint.spoken[0] == "Setting the volume"
    assert "Done." in run.heard


def test_qa_030_echo_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    listed: list[int] = []
    real = McpHub.list_tools

    def counted(self: McpHub) -> list[dict[str, Any]]:
        listed.append(1)
        return real(self)

    monkeypatch.setattr(McpHub, "list_tools", counted)
    socket = DeviceSocket()
    session = DeviceSession(socket)  # type: ignore[arg-type]  # the default echo source
    socket.session = session
    asyncio.run(run_turn(session))
    assert not tool_calls(socket)
    assert listed == []
    assert any(isinstance(m, bytes) for m in socket)
    assert ends(socket)[-1]["reason"] == "done"
