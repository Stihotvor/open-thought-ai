# Review — device tools (issue #27)

Verdict: PASS WITH WARNINGS. Range `main...HEAD`. No critical or high finding.

## Layer 1 gate
Pass: ruff, complexity, mypy strict, bandit, pylint structure, pytest with coverage. The format check ran in the main session and is clean. TDD order: pass. QA: 54 pass, 3 partial, 0 fail, 0 not-automated.

## Fixed
- Security: the system prompt now says that tool results are untrusted data, never instructions.
- Security: the `tool.call` send and the result wait share one 2 s timeout, so a device that blocks the send ends with `tool_timeout`.
- Security: the hub logs one line per tool call with the name, `ok` and the code. It never logs args or results.
- Style: the timeout outcome text is readable. The code stays `tool_timeout`.
- Style: `TurnTools` is no longer a quoted forward reference. The `cast` in `read_round` is removed. The hub docstring cites D2/D3.

## Kept, with the reason
- `confirm` step and `ToolSpec.confirm`: this is #30 scope. The device tools have `confirm=False`. The field is the seam for #30.
- Result schema validation: this is #31 scope (`docs/security.md`, schema checks).
- `validate` repeats the unknown-tool check: it is a defence so that the step is safe in any chain order.
- `PendingCalls.__len__` stays because the tests use it. The 3 `from .agent import` lines stay because ruff's import sort splits the re-exports.
- Relative imports in `hub/` and a split of `agent.py`: no change. The package is consistent within itself, and `agent.py` has 160 lines.

## Architect code review (in place of CodeRabbit; the repo has no CI and no CodeRabbit)
Verdict: REQUEST CHANGES, then fixed:
- medium: `PendingCalls.resolve` could set a result on a cancelled future (`InvalidStateError`). It now checks `done()`. The style pass had called that check unreachable, and its removal was wrong.
- medium: the hub is built once per session (D7a), so #28 and #30 can keep state.
- low-medium: `wrap` is now the outermost step (D3a). The earlier "kept" note about chain order was wrong, because `wrap` was the innermost step.
- low: `ToolSource.timeout` with an outer guard in the hub (D4a). The guard waits the source timeout plus 0.5 s, so the source timeout fires first. Source and tool names are checked (D13).
- Recorded for #30/#31: `limit` counts invalid calls. `CallCtx` needs a session id and a turn id.
