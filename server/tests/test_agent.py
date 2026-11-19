"""The tool loop of a turn (agent.py): a fake chat_turn and a fake ToolContext,
inside a full pipeline turn with the fake endpoint for STT, moderation and TTS."""

import asyncio
import copy
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import pytest
from fakes import SPEECH, Endpoint, pipeline_for

from ota_server.hub.types import ToolCall, ToolOutcome
from ota_server.pipeline import SentenceFeed, TurnClock
from ota_server.protocol import TurnError
from ota_server.providers import ProviderError, TextDelta, ToolCallRequest, ToolCallsDone
from ota_server.ratelimit import TurnBudget

type Item = TextDelta | ToolCallsDone | Exception
VOLUME = ToolCallsDone((ToolCallRequest("c1", "device__set_volume", '{"level": 30}'),))
TOOLS_LIST = [{"type": "function", "function": {"name": "device__set_volume"}}]


class FakeChat:
    """chat_turn: round n streams rounds[n] (the last one repeats); records each call."""

    def __init__(self, *rounds: list[Item]) -> None:
        self.rounds, self.calls = rounds, cast(list[tuple[Any, Any, Any]], [])

    async def __call__(
        self, messages: Any, budget: Any, tools: Any = None, tool_choice: Any = None
    ) -> AsyncIterator[TextDelta | ToolCallsDone]:
        self.calls.append((copy.deepcopy(messages), tools, tool_choice))
        for item in self.rounds[min(len(self.calls), len(self.rounds)) - 1]:
            if isinstance(item, Exception):
                raise item
            yield item


class FakeTools:
    def __init__(self, text: str = "ok", wait: Callable[[], None] = lambda: None) -> None:
        self.text, self.wait, self.ran = text, wait, cast(list[ToolCall], [])

    def list_tools(self) -> list[dict[str, Any]]:
        return TOOLS_LIST

    async def run(self, call: ToolCall) -> ToolOutcome:
        self.ran.append(call)
        self.wait()
        return ToolOutcome(True, self.text)


def tool_turn(
    monkeypatch: pytest.MonkeyPatch, chat: FakeChat, tools: FakeTools, endpoint: Endpoint
) -> list[str]:
    pipeline = pipeline_for(endpoint)
    monkeypatch.setattr(pipeline.client, "chat_turn", chat)

    async def turn() -> list[str]:
        return [c.text async for c in pipeline(SPEECH, (), tools=tools)]

    return asyncio.run(turn())


def test_one_tool_round_then_text(monkeypatch: pytest.MonkeyPatch) -> None:
    chat, tools = FakeChat([VOLUME], [TextDelta("Done.")]), FakeTools()
    assert tool_turn(monkeypatch, chat, tools, Endpoint()) == ["Done."]
    assert [(c.name, c.args) for c in tools.ran] == [("device__set_volume", {"level": 30})]
    call = {"id": "c1", "type": "function"}
    call["function"] = {"name": "device__set_volume", "arguments": '{"level": 30}'}  # type: ignore[assignment]
    assert chat.calls[1][0][-2:] == [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]


def test_three_tool_rounds_then_a_text_round(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = FakeChat([VOLUME], [VOLUME], [VOLUME], [TextDelta("Done.")])
    assert tool_turn(monkeypatch, chat, FakeTools(), Endpoint()) == ["Done."]
    assert [(tools, choice) for _, tools, choice in chat.calls] == [(TOOLS_LIST, None)] * 3 + [
        (TOOLS_LIST, "none")
    ]


def test_tool_messages_stay_out_of_history_and_moderation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = Endpoint()
    chat = FakeChat([VOLUME], [TextDelta("Done.")])
    assert tool_turn(monkeypatch, chat, FakeTools("SECRET-RESULT"), endpoint) == ["Done."]
    assert not [t for texts in endpoint.moderated for t in texts if "SECRET-RESULT" in t]


def test_text_before_tool_calls_is_spoken_first(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = FakeChat([TextDelta("Setting it now"), VOLUME], [TextDelta("Done.")])
    assert tool_turn(monkeypatch, chat, FakeTools(), Endpoint()) == ["Setting it now", "Done."]
    assert chat.calls[1][0][-2]["content"] == "Setting it now"


def test_budget_is_extended_by_the_tool_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    extended: list[float] = []
    budget = TurnBudget(clock=lambda: now[0])
    monkeypatch.setattr(budget, "extend", extended.append)
    pipeline = pipeline_for(Endpoint())
    monkeypatch.setattr(pipeline.client, "chat_turn", FakeChat([VOLUME], [TextDelta("Done.")]))
    tools = FakeTools(wait=lambda: now.__setitem__(0, now[0] + 1.0))
    feed = SentenceFeed()
    asyncio.run(pipeline.read_chat([], budget, feed, TurnClock(1), tools))
    assert extended == [pytest.approx(1.0)]


def test_a_turn_without_tool_calls_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    chat, tools = FakeChat([TextDelta("Dobrze. "), TextDelta("A ty?")]), FakeTools()
    assert tool_turn(monkeypatch, chat, tools, Endpoint()) == ["Dobrze.", "A ty?"]
    assert not tools.ran and len(chat.calls) == 1


@pytest.mark.parametrize(
    "rounds",
    [[[VOLUME], [ProviderError("chat")]], [[VOLUME]]],  # a broken stream; tools to the end
    ids=["broken-stream", "tool-calls-in-the-final-round"],
)
def test_a_broken_tool_stream_is_llm_failed(
    monkeypatch: pytest.MonkeyPatch, rounds: list[list[Item]]
) -> None:
    with pytest.raises(TurnError) as err:
        tool_turn(monkeypatch, FakeChat(*rounds), FakeTools(), Endpoint())
    assert err.value.code == "llm_failed"


def test_bad_json_arguments_give_none_args(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = ToolCallsDone((ToolCallRequest("c1", "device__set_volume", "{bad"),))
    blank = ToolCallsDone((ToolCallRequest("c2", "device__ping", " "),))
    chat, tools = FakeChat([bad], [blank], [TextDelta("Done.")]), FakeTools("invalid args")
    assert tool_turn(monkeypatch, chat, tools, Endpoint()) == ["Done."]
    assert [c.args for c in tools.ran] == [None, {}]
    assert chat.calls[1][0][-1] == {"role": "tool", "tool_call_id": "c1", "content": "invalid args"}
