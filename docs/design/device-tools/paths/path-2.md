# Path 2 — Event-driven bus with id correlator

Core idea: the hub never touches the socket. It publishes `ToolCallEvent`s on a per-session asyncio bus and awaits futures. The session publishes `ToolResultEvent`s, and a `Correlator` resolves the futures by id.

## Components
- `hub/events.py`, `hub/bus.py` (`EventBus.publish/subscribe`), `hub/correlator.py` (`expect`, `resolve`, `fail_all`).
- `hub/hub.py`: `McpHub` with `ToolSource` registrations, `openai_tools`, `run` (never raises).
- `hub/device_source.py`: fixed schemas, `device__` namespace, validates, publishes, awaits.
- `hub/wrap.py`: truncation and untrusted wrapper.
- `providers.py`: `chat(..., tools)` yields `TextDelta | ToolCallsReady` (return type changes for all callers).
- `pipeline.py`: tool loop in `read_chat`, 1 follow-up round. `session.py`: owns bus, correlator and a pump task that sends `tool.call` frames.

## Failure modes
- F1: `asyncio.timeout(2.0)` in the source → `tool_timeout`. F2: error outcome. F3: `fail_all` and pump cancel on teardown.
- F4: `ProviderError` → `llm_failed`. F5: `wrap.py`.

Effort: M (about 5 new modules). Main risk: four moving parts (hub, bus, pump, correlator) for one request/response. Exact unsubscribe/cancel lifecycle and frame ordering with audio on the same socket are needed.
