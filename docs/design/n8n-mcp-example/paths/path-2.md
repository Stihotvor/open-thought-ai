# Path 2: Annotate existing artifacts, no new guide file

Lens: put the worked example in files that exist. Add no test.

## Components
- `docs/security.md`, section "MCP hub and tools": add an "Add an n8n server" subsection with at most 6 steps, plus a short troubleshooting list.
- `docs/examples/mcp/servers.json`: keep the n8n entry. JSON has no comments, so change little.
- `README.md`: one link to the new section.

## Fit and risks
- Effort: S. Docs only. No code, no test.
- Gives up: no drift test, no stand-alone guide, and `security.md` mixes a threat model with a how-to.
- Risk 1: names in the text drift from the config without notice.
- Risk 2: it does not match the issue wording "n8n MCP Server Trigger example workflow and docs" as a guide.
