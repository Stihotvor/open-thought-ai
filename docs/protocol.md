# Protocol v1

One WebSocket connection between the device and the server.

## Connection
- URL: `ws://<host>:<port>/v1/device`. `wss://` is a later milestone (see [security](security.md)).
- Auth: header `Authorization: Bearer <token>` (≥ 32 random bytes, base64url). Server stores only a hash.
- Pre-auth timeout: the upgrade must complete in 5 s. `hello` must arrive within 5 s after it.
- Keepalive: RFC 6455 ping/pong every 15 s. 3 missed pongs → both sides close.
- One device per server. A new authenticated connection **replaces** the old session (the old socket is closed, its turn is cancelled). This handles Wi-Fi drops without a `busy` lock-out.
- Disconnect cancels the current turn on the server.

### Close codes
| Code | Meaning | Device action |
|---|---|---|
| 4401 | Bad token | Auth-error face. **Stop reconnecting** until reboot or reprovision. |
| 4426 | Unsupported `proto` | Version-error face. Stop reconnecting. |
| 4400 | Protocol violation (bad JSON, frame too large) | Reconnect with backoff. |
| 4000 | Replaced by a newer session | Do not reconnect (the newer one is us or a clone). |
| other | Network / server restart | Reconnect, backoff 1 → 60 s. |

## Frames
- Text frames: UTF-8 JSON, max **1024 bytes**, always with `type`. Larger → 4400.
- Binary frames: audio. Byte 0 = `turn` id (uint8, wraps), bytes 1..n = PCM. Max 3 201 bytes (100 ms).
- PCM both ways: signed 16-bit little-endian, mono, **16 000 Hz**. Fixed; not negotiated.
  The server decodes and resamples all TTS output to this format.

## Turns
- Every turn has a `turn` id (uint8, server-assigned in `turn.start`, wraps at 255).
- Every server message and audio frame of a turn carries its `turn`. The device drops anything with a stale `turn`.

## Flow control (server → device audio)
- Device jitter buffer: **12 KB** (~375 ms). I2S DMA: 2 × 1 KB.
- The server paces audio at real time with a maximum lead of **300 ms**.
- The device sends `audio.credit {bytes}` when its free buffer rises above 4 KB. The server never sends more than the granted credit. Start credit = 9 600 bytes, set again at each `turn.start`. Credit sent before `reply.start` counts.

## Messages
| type | dir | fields | meaning |
|---|---|---|---|
| `hello` | D→S | `proto: 1`, `fw`, `device_id` | First message. Tools are **not** declared here (server has fixed device tool schemas). |
| `hello` | S→D | `proto: 1`, `language`, `volume` | Session accepted. |
| `listen.start` | D→S | — | Button pressed. Server replies `turn.start`. |
| `turn.start` | S→D | `turn` | New turn id. Device then streams audio frames with this id. |
| `listen.stop` | D→S | `turn` | Button released. Max 30 s of audio per turn; server stops listening after that. |
| `transcript` | S→D | `turn`, `text` (truncated to 200 chars) | What the server heard. |
| `state` | S→D | `turn`, `state`: `thinking`/`speaking`/`idle`/`confirm` | Drives the face. |
| `reply.start` | S→D | `turn` | Audio follows. |
| `reply.end` | S→D | `turn`, `reason`: `done`/`cap`/`abort`/`error` | Audio ends. |
| `abort` | D→S | `turn` | Stop the turn now. |
| `audio.credit` | D→S | `bytes` | Flow control. |
| `confirm.request` | S→D | `turn`, `tool`, `timeout_ms` | A risky tool needs a spoken "yes". Device shows the confirm face. |
| `tool.call` | S→D | `id`, `name`, `args` | Run a device tool. Handled in any state. |
| `tool.result` | D→S | `id`, `ok`, `result` or `error` | Must arrive within **2 s**, else the server records a timeout error. |
| `settings.update` | S→D | `volume?`, `language?` | Pushed when the web UI changes a device setting. |
| `error` | S→D | `turn`, `code` | Turn failed. Device plays a **local** beep and shows a face per code. |

### Error codes
`no_speech` (< 300 ms speech), `stt_failed`, `llm_failed` (chat call failed, not a 429), `rate_limited` (provider limit still hit after retries), `tts_failed`, `too_many_turns` (turn start refused: more than `OTA_TURNS_PER_MINUTE`, burst `OTA_TURN_BURST`; the current turn goes on), `tool_timeout`, `moderated`, `confirm_timeout`, `internal`.
Errors are never sent as audio, so they work even when TTS is down.

## Abort and barge-in
1. Button press during `speaking` or `thinking`: the device **flushes its jitter buffer and I2S DMA at once** (does not wait for the server), sends `abort {turn}`, then starts a new `listen.start` (barge-in).
2. The server cancels in-flight STT, LLM, tool and TTS tasks and sends `reply.end {reason: abort}`.
3. Metric M5 is measured on the device: button press → I2S silent ≤ 300 ms.

## Confirm flow (risky tools)
1. The LLM requests a tool flagged `confirm`. The **hub** (not the LLM) holds the call: tool name + exact args + turn id, single use.
2. The server speaks a short question, then sends `confirm.request {turn, tool, timeout_ms: 10000}`. The timer starts after `reply.end`.
3. The next `listen.start` within the timeout is the confirmation turn. The server classifies the transcript with a keyword list per language (EN yes/yeah, PL tak, UK так). No LLM in this decision.
4. Yes → run the held call once. Anything else or timeout → cancel, `error {code: confirm_timeout}` on timeout.

## Long replies ("continue")
- TTS stops after `speech_cap_s` (default 60 s): `reply.end {reason: cap}`.
- The server keeps the unspoken text in memory for 5 min. History stores the spoken part and marks the turn `truncated`.
- A transcript that matches the "continue" keyword list (EN continue, PL dalej, UK далі) plays the rest, without an LLM call.

## Device tools
Fixed on the server (the device cannot add tools): `set_volume {level 0..100}`, `set_face {face}`, `get_status {}`.

## Versioning
`proto` is an integer. Unknown → close 4426.
