# Delivery plan

No deadline. Milestones run in order. Each task becomes one GitHub issue with a "Done when" check.
Hours are human hours with AI help (estimates).

| # | Milestone | Done when | Hours |
|---|---|---|---|
| M0 | Repo, docs, protocol v1 spec | Docs reviewed and merged | 3–5 |
| M0.5 | Hardware review (EDA agent) | `docs/hardware.md` approved, pins fixed | 3–6 |
| M1 | Firmware bring-up | Wi-Fi via SoftAP, tone plays, face shows, mic level logged | 8–15 |
| M2 | Link + echo | Speech goes to server and plays back | 6–10 |
| M3 | Voice pipeline | EN/PL/UK round trip, latency p50 ≤ 5 s measured | 10–20 |
| M4 | MCP hub | Device tool + one n8n MCP tool work, risky tool asks to confirm | 10–18 |
| M5 | Web UI + Postgres | Settings, MCP servers, tools, history, retention in UI | 6–12 |
| M6 | Hardening | Abort ≤ 300 ms, 24 h soak, CI green, heap ≥ 40 KB | 8–15 |
| M7 | Later | Qdrant memory, server wake word, public `wss://` | 6–12 |

Total M0–M6: 54–101 h.

## Tasks
### M0
- Repo skeleton, MIT license, README, docs.
- Protocol v1 spec: turn ids, flow control, abort, confirm, error codes, close codes.
- Security doc and data model doc.
- ADRs for key decisions.

### M0.5
- EDA review of wiring: pins, capacitors, power. Update `docs/hardware.md`.

### M1 Firmware bring-up
- ESP-IDF project, partition table without OTA, `board_pins.h`.
- SoftAP provisioning: button gesture, WPA2 + OLED PIN, 5 min timeout, NVS encryption.
- OLED faces for all states.
- I2S out: test tone and volume.
- I2S in: mic capture and level log.
- Button with debounce.

### M2 Link + echo
- Firmware `ws_link`: connect, hello, ping, backoff reconnect.
- Server skeleton: FastAPI, settings, gateway with token auth and session replace.
- Echo mode: server returns captured PCM with `audio.credit` pacing.
- Measure free heap with WS + audio buffers (target ≥ 40 KB).
- Spike: `wss://` with mbedTLS, measure heap. Record result in an ADR.
- Abort flush on the device: measure button → silence.
- Compose file with `server`.

### M3 Voice pipeline
- Probe: Mistral STT and TTS with one EN, PL and UK phrase each. Record language support and output format.
- `providers`: STT, chat (stream), TTS against an OpenAI-compatible endpoint per role.
- Docs example: optional external LiteLLM config with Mistral → Gemini → Ollama/LM Studio fallback.
- Pipeline: VAD, STT, agent, sentence-split TTS streaming, 60 s cap, "continue".
- Per-sentence moderation (fail closed) and family-safe prompt.
- TTS decode + resample to 16 kHz.
- "continue" keyword handling and truncated history turns.
- Error codes to the device (`llm_failed`, `no_speech`, ...); device plays local sounds.
- Test set: 30 phrases × 3 languages; latency report.

### M4 MCP hub
- Device tools over `tool.call`/`tool.result`.
- External MCP client (Streamable HTTP, SSE, stdio).
- n8n MCP Server Trigger example workflow and docs.
- `confirm` flow enforced in the hub.
- Tool safety: namespacing, schema hash re-approval, SSRF checks, secret header allowlist, 4 calls per turn.

### M5 Web UI + storage
- Alembic migrations (per-schema version table, advisory lock, `migrate` service).
- Role and schema setup script per instance.
- Backup script (pg_dump + Qdrant snapshot) and restore test.
- Pages: status, settings, MCP servers, tools, history.
- Admin login: argon2, CSRF, rate limit, audit log, re-auth for dangerous actions.
- Retention job.

### M6 Hardening
- Abort/barge-in timing.
- Reconnect tests, server restart test.
- Unit tests (pytest), firmware build in CI (GitHub Actions).
- 24 h soak script and report.
