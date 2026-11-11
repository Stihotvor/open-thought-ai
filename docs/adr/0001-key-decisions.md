# ADR 0001: Key decisions for the MVP

Status: accepted (2026-11-09). Decision 5 is superseded by [ADR 0002](0002-litellm-optional.md).

| # | Decision | Reason | Cost |
|---|---|---|---|
| 1 | Own firmware and server, MIT, written from scratch | Full control, no hidden update path | More effort |
| 2 | No OTA on the device | No remote code injection path | USB flash for each update |
| 3 | One server instance = one device | No tenant logic, simple code | More containers for more devices |
| 4 | WebSocket + JSON control + raw PCM | Simple in Python and C; LAN bandwidth (256 kbit/s) is fine | Higher bandwidth than Opus |
| 5 | ~~All models through a LiteLLM proxy~~ Superseded by [ADR 0002](0002-litellm-optional.md) | One API in the server; fallback and provider switch in config | One more container |
| 6 | Push-to-talk button in the MVP | C3 has no PSRAM for on-device wake word | No hands-free use yet |
| 7 | Server is the only MCP client | Tools managed in one UI; device tools look like other tools | Device depends on the server for every tool |
| 8 | Server-rendered web UI (Jinja + HTMX) | Python only, no JS build | Less rich UI |
| 9 | Reuse existing Postgres, Qdrant, n8n; no Redis in MVP | No duplicate services | Setup depends on the home stack |
| 10 | Turn ids + credit flow control in protocol | Safe abort and bounded RAM on the C3 | More protocol logic |
| 11 | Session replace instead of busy lock | Survives Wi-Fi drops | A token thief can kick the device off (logged) |
