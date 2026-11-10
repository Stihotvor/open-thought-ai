# Requirements — open-thought-ai MVP

Self-hosted pocket voice assistant: own ESP32-C3 firmware and own Python server. MIT license.

## Business Goals
- G1: A small home and family life assistant. Family members ask by voice and hear spoken answers.
- G2: All code is our own and open source (MIT). No vendor server, no OTA, no hidden endpoints.
- G3: Hobbyists can rebuild the device and the server from the public repo and docs alone.
  Test: a person follows `docs/` on a clean machine and gets a voice round trip without asking the author.
- G4: No deadline. Hobby pace. Milestones are ordered, not dated.

## Technical Constraints
- C1 Hardware: ESP32-C3 SuperMini (no PSRAM), INMP441 mic, MAX98357A amp, SSD1306 0.96" OLED, one button.
  Wiring is reviewed by the EDA agent before bring-up is frozen.
- C2 Firmware: C on ESP-IDF 5.x. No OTA partition. Update only by USB flash.
- C3 Server: Python 3.12, FastAPI, Docker Compose, on HP t630 (4-core AMD, no GPU).
- C4 One server instance serves exactly one device. More devices run more server instances.
- C5 Transport: own WebSocket protocol v1, raw PCM 16 kHz/16-bit mono. MVP is LAN only with `ws://`.
  The firmware accepts any server URL (LAN or public). Public `wss://` is a later milestone.
- C6 Models: the server calls OpenAI-compatible endpoints for LLM, STT and TTS, one per role (Mistral, Ollama, LM Studio).
  An external LiteLLM proxy is optional. It adds provider fallback. It is not part of this stack.
- C7 Languages: English, Polish, Ukrainian. One language per device, set in the web UI.
- C8 Budget: 0 € per month. Free tiers only.
- C9 Storage: reuse the existing Postgres (≥ 15) and Qdrant. One schema, role and collection per instance. Redis is not used in the MVP.
- C10 Tools: the server is the MCP client. Tool sources are device tools and external MCP servers (mainly n8n), managed in the web UI.
- C11 Speakers: one shared conversation history per device. No speaker identification in the MVP.

## Edge Cases
- E1 Long answers: the server stops TTS after 60 s of speech. The user says "continue" to get the rest.
- E2 Children: a family-safe system prompt plus a moderation check before TTS. A blocked reply plays a neutral refusal.
- E3 Risky tools: a tool flagged `confirm` runs only after a spoken "yes" (keyword list per language) within 10 s after the question ends. The hub enforces it (protocol: `confirm.request`). New tools start with `confirm=true`.
- E4 Button pressed during a reply: the device flushes its audio at once, sends `abort`, and starts a new listen (barge-in).
- E5 A new authenticated connection replaces the old session (close code 4000 to the old one). This covers Wi-Fi drops.
- E6 Empty or silent audio (< 300 ms of speech): no LLM call. Server sends `error {code: no_speech}`; the device shows a "did not hear" face.
- E7 Wrong token: close code 4401. The device shows an auth-error face and stops reconnecting until reboot or reprovision.

## Failure Modes
- F1 Server or Wi-Fi down: the OLED shows an offline face. The device reconnects with backoff from 1 s to 60 s. A button press gives an error beep.
- F2 The provider (or LiteLLM, if used) fails or hits limits. The server sends `error {code: llm_failed}`; the device plays a local error sound and ends the turn.
- F2b Moderation error: fail closed, neutral refusal.
- F3 Server restart: conversation history, settings, MCP config and long-term memory survive (Postgres + Qdrant).
  Raw audio is never stored.
- F4 MCP server unreachable: its tools are hidden from the LLM for that turn. The UI shows the server as down.
- F5 Device reboot or power loss: Wi-Fi credentials, server URL and token survive in NVS.
- F6 Retention: configurable in the UI. History default 90 days (whole conversations), memory default 365 days. "Forget everything" action.
- F7 Backup: nightly Postgres schema dump + Qdrant snapshot. RPO 24 h, RTO 1 h.
- OPEN: TLS (`wss://`) on the C3 may not fit in RAM next to the audio buffers. Accepted risk for the MVP (LAN only). Measure before the public milestone.
- OPEN: Exact Postgres and Qdrant versions on the home stack. Assumed Postgres ≥ 15, Qdrant 1.x.
- OPEN: Flash encryption and USB-JTAG disable on the C3. Decide before public release.
- RISK (M3 probe, see ../../research/m3-mistral-audio-probe.md): Mistral free-tier STT does not support Polish or Ukrainian. TTS works on the free tier with English voices only; PL/UK quality is not judged yet. Fallback: a local Piper TTS endpoint.

## Success Metrics
- M1 Latency: button release to first reply sound, p50 ≤ 5 s and p95 ≤ 8 s, over 50 turns on LAN.
- M2 Stability: 24 h soak with 100 round trips, 0 firmware crashes, reconnect after a server restart in ≤ 60 s.
- M3 STT quality: ≥ 27 of 30 recorded test phrases understood correctly, for each of EN, PL and UK.
- M4 Firmware RAM: minimum free heap ≥ 40 KB during a full round trip.
- M5 Abort: device audio silent ≤ 300 ms after the button press (measured on the device), 10 of 10 tries.
- M6 Tools: the LLM calls a device tool and one n8n MCP tool correctly in ≥ 9 of 10 tries.
- M7 Cost: 0 € API spend per month (provider usage page, or the LiteLLM spend log if used).
