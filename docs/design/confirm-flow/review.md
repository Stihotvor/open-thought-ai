# Review — confirm flow (issue #30)

Scope: `main..feat/30-confirm-flow` (merge base `78fb3b4`), 12 source files and the tests.

Verdict: PASS WITH WARNINGS (2026-11-23).

## Layer 1 gate
- First run: FAIL. Structure: pylint C0302, `tests/test_qa_confirm_flow.py` had 788 lines (limit 500).
- Fix: the file was split into `test_qa_confirm_hub.py`, `test_qa_confirm_session.py` and `qa_confirm_support.py`. Test names and assertions did not change.
- Second run: PASS. Lint, complexity, types, security, structure (with cycles), tests. `scripts/gate.sh`: 933 passed, 1 skipped, coverage 96.77 %.
- TDD order (`--base 78fb3b4`): pass.

## Layer 2 and 3 findings
No critical or high findings in the structure, security, style or performance passes.

Fixed:
- Security low: `Held.args` was a shallow copy. Now `copy.deepcopy`, with a test.
- Style medium and structure low: `TurnTools` read `McpHub._language`. Now a public `language` property.
- Style medium and structure low: the English fallback was written twice. Now one fallback inside `confirm_text`. The dead `_text` helper was removed.
- Security medium: the question named the tool but not its args. Closed in a follow-up PR: the question now speaks a bounded summary of the validated args (`confirm_text.args_summary`, rules in `docs/protocol.md`).

Stays, with the reason:
- Security low: a claimed call has no wall-clock expiry when `listen.stop` never comes. The next `listen.start`, `abort` or `close` drops it. Follow-up: a claim timer.
- Security low: `ToolSpec.confirm` defaults to `False` (code from #27). Both sources set it explicitly. Follow-up: make it required or default `True`. That change touches many #27 and #28 tests.
- Structure medium: the session calls gate phase methods at 6 sites. Structure medium: `DeviceSession` owns the confirm timer. Both follow D9. A `ConfirmTimer` helper and intent-level hub methods are a refactor follow-up.
- Style medium: the step returns `confirm_required` when the gate raises (D3, F4: fail closed). Kept as decided.
- Style medium: `turn_tools(turn=0)` and `turn_context(turn=0)` keep defaults, so older callers keep working (T4 plan note). The real path passes the turn.
- Style low (names, `Literal` reason, constant order) and structure low (a test that the language keys match `agent.LANGUAGE_NAMES`): cosmetic, left.
- Accepted risk (docs/security.md): anyone in earshot can say "yes".

## SDLC checks
- `qa/results.md`: pass 55, partial 2 (QA-020, QA-026), fail 0.
- `status.json`: 10 of 10 tasks done.

## Out of scope, found during the build
- `hub/steps.py` `_type_ok` accepts only `object`, `integer` and `string`. An MCP tool arg of type `boolean`, `number` or `array` gets `invalid_args` (#27 code).
- The firmware does not handle `confirm.request` or `state confirm` yet.
