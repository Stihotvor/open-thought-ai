# Path 4 — source-agnostic hub admission

- New `hub/admission.py`: `AdmissionRules(reserved={"device"}, max_desc=1000, max_schema=4000)`, `Admission.admit_all(sources)`: name format, reservation, caps (T4), pin check (T3), first-name-wins collision (T2). Each step isolated (F4).
- `ToolSource` gains `pins: Mapping[str, str] | None`. `McpSource` passes every listed tool and its pins to the hub. `McpHub.__init__` logs and skips instead of raising.
- Connection safety (T5-T10) stays in the MCP package: `SafeConnectionPolicy` with SSRF and secret headers, config rules, no-redirect client.
- Effort M (about 150 new hub lines). Risks: a source with `pins=None` is silently unpinned; the registry must still close a source with 0 admitted tools; the hub protocol changes for all sources. Rebinding gap as path 1.
