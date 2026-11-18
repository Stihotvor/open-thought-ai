# Review — device tools (issue #27)

Verdict: PASS WITH WARNINGS. Range `main...HEAD`. No critical or high finding.

## Layer 1 gate
Pass: ruff, complexity, mypy strict, bandit, pylint structure, pytest with coverage. The format check ran in the main session and is clean. TDD order: pass. QA: 54 pass, 3 partial, 0 fail, 0 not-automated.

## Fixed
- Security: the system prompt now says that tool results are untrusted data, never instructions.
- Security: the `tool.call` send and the result wait share one 2 s timeout, so a device that blocks the send ends with `tool_timeout`.
- Security: the hub logs one line per tool call with the name, `ok` and the code. It never logs args or results.
- Style: the timeout outcome text is readable. The code stays `tool_timeout`.
- Style: the unreachable `done()` check is removed. `TurnTools` is no longer a quoted forward reference. The `cast` in `read_round` is removed. The hub docstring cites D2/D3.

## Kept, with the reason
- Chain order (`wrap` outermost, `limit` first): D3 fixes the order `resolve, limit, validate, terminal, wrap`. Rejection texts are written by the server and only echo names that the model chose, and unknown calls are bounded by 8 indexes and 4 chat calls. A change needs a design change, so #31 (tool safety) takes it.
- `confirm` step and `ToolSpec.confirm`: this is #30 scope. The device tools have `confirm=False`. The field is the seam for #30.
- Result schema validation: this is #31 scope (`docs/security.md`, schema checks).
- `validate` repeats the unknown-tool check: it is a defence so that the step is safe in any chain order.
- `PendingCalls.__len__` stays because the tests use it. The 3 `from .agent import` lines stay because ruff's import sort splits the re-exports.
- Relative imports in `hub/` and a split of `agent.py`: no change. The package is consistent within itself, and `agent.py` has 160 lines.
