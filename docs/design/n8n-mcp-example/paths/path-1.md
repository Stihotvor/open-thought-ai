# Path 1: Standalone guide with an offline doc test

Lens: one new guide file, pinned to the real code by a test.

## Components
- `docs/guides/mcp-servers.md`: at most 120 lines. Sections: Overview, Setup steps (1-6), Example servers file, Troubleshooting.
- `README.md`: one link next to the speech guide (line 18).
- `server/tests/test_mcp_servers_guide.py`: offline test, modelled on `test_litellm_guide.py`.

## Test cases
- Steps are numbered 1 to N, with N at most 6. The guide has at most 120 lines.
- The first fenced json block loads through `load_servers`. `load_servers` logs bad entries and returns `[]`, so the test asserts one server and its field values (pins, `no_confirm`, header).
- Each `MCP_SECRET_*` token matches `SECRET_VAR`. Each `OTA_*` token exists in `config.py`.
- Each pin is `sha256:` plus 64 hex characters. Each `no_confirm` name is a key of `tools`.
- Contract strings exist: "current hash sha256:", "any MCP server", "see your n8n UI".
- A small denylist fails invented n8n identifiers (for example `n8n-nodes-base.`).

## Fit and risks
- Effort: S. Gate: needs full type hints in the test. No `src/` change.
- Risk 1: the test checks names, not n8n facts. A wrong n8n claim still passes.
- Risk 2: a weak assertion passes an invalid example. Guard: assert length and fields.
- Risk 3: the denylist is a heuristic.
