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
- Device jitter buffer: **48 KB** (~1.5 s). It rides out Wi-Fi stalls of up to ~1.2 s, as seen on the 2 dBm SuperMini radio. I2S DMA: 90 ms (driver default).
- The server paces audio at real time with a maximum lead of **1.5 s**.
- The device sends `audio.credit {bytes}` when it has played 3.2 KB (100 ms) more. The server never sends more than the granted credit. Start credit = 48 000 bytes, set again at each `turn.start`. Credit sent before `reply.start` counts.
- The server caps the open credit at 48 KB (the jitter buffer). If the device gives no credit for 10 s, the server ends the reply with `reply.end {reason: error}`.

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
| `tool.call` | S→D | `id`, `name`, `args` | Run a device tool. Handled in any state. `id` is an integer, counted from 1 per session. |
| `tool.result` | D→S | `id`, `ok`, `result` or `error` | `id` is the same integer. Must arrive within **2 s**, else the server records a timeout error. |
| `settings.update` | S→D | `volume?`, `language?` | Pushed when the web UI changes a device setting. |
| `error` | S→D | `turn`, `code` | Turn failed. Device plays a **local** beep and shows a face per code. |

### Error codes
`no_speech` (< 300 ms speech), `stt_failed`, `llm_failed` (chat call failed, not a 429), `rate_limited` (provider limit still hit after retries), `tts_failed`, `too_many_turns` (turn start refused: more than `OTA_TURNS_PER_MINUTE`, burst `OTA_TURN_BURST`; the current turn goes on), `tool_timeout`, `confirm_timeout`, `internal`.
Errors are never sent as audio, so they work even when TTS is down.

## Abort and barge-in
1. Button press during `speaking` or `thinking`: the device **flushes its jitter buffer and I2S DMA at once** (does not wait for the server), sends `abort {turn}`, then starts a new `listen.start` (barge-in).
2. The server cancels in-flight STT, LLM, tool and TTS tasks and sends `reply.end {reason: abort}`.
3. Metric M5 is measured on the device: button press → I2S silent ≤ 300 ms.

## Confirm flow (risky tools)
1. The LLM requests a tool flagged `confirm`. The **hub** (not the LLM) holds the call: tool name + exact args + turn id, single use. The hub holds one call per session. A second `confirm` call while a call is held gets `confirm_busy` and does not run.
2. The server stops the LLM and speaks a fixed question. Then it sends, in this order: `reply.end {reason: done}`, `state {state: confirm}`, `confirm.request {turn, tool, timeout_ms: 10000}`. The timer starts after `reply.end`. If `reply.end` has a reason other than `done` (for example a cap), the server sends no `confirm.request` and drops the call.
3. The next `listen.start` within the timeout is the confirmation turn. The server cancels the timer at that `listen.start`. The confirmation turn makes no LLM call.
4. The server matches the transcript of the confirmation turn with the exact-match rule (below). Yes → run the held call once, then speak "Done." or "That did not work.". Anything else → cancel and speak "Cancelled.".
5. Timeout: 10 s after `reply.end` with no `listen.start`, the server drops the call and sends `error {turn, code: confirm_timeout}`.
6. `abort` and the close of the connection drop the held call. A `listen.start` refused with `too_many_turns` does not drop it, and the timer goes on.

### Exact-match rule
1. Make the transcript lowercase.
2. Remove punctuation and spaces at the start and at the end.
3. The result must be equal to one yes word of the session language. Other languages do not count.

| Language | Yes words |
|---|---|
| `en` | `yes`, `yeah` |
| `pl` | `tak` |
| `uk` | `так` |

"Yes." and " YES! " are a yes. "Yes please" and "yes, do it" are not a yes.

### Fixed texts
The server speaks only these texts in the confirm flow. They are the same as in `server/src/ota_server/hub/confirm_text.py`. `{what}` is the source name, the bare tool name, and a short summary of the validated args, for example `device set_volume with level 30`.
The args summary follows fixed rules, so an injected call cannot hide its args behind the tool name:
- The server speaks at most 3 args. More args add `and more` (`i więcej`, `та інше`).
- A key or a string value is spoken only if it is a short token: 1 to 24 characters of `A-Z a-z 0-9 _ . -`. A key that is not a token is spoken as `?`.
- Booleans and numbers up to 24 characters are spoken as is.
- Any other value (free text, a list, an object) is spoken as `some text` (`jakiś tekst`, `якийсь текст`). The server never speaks free text verbatim.
- The connector word is `with` (`z`, `з`). A call with no args gets no summary.

| Text | `en` | `pl` | `uk` |
|---|---|---|---|
| Question | Should I run {what}? Say yes to confirm. | Czy mam uruchomić {what}? Powiedz tak, aby potwierdzić. | Запустити {what}? Скажіть так, щоб підтвердити. |
| Done | Done. | Gotowe. | Готово. |
| Failed | That did not work. | To się nie udało. | Це не вдалося. |
| Cancelled | Cancelled. | Anulowano. | Скасовано. |

## Long replies ("continue")
- TTS stops after 60 s of reply audio (a fixed cap): `reply.end {reason: cap}`.
- Reply audio streams per TTS chunk: the first sentence plays while the next one is synthesized.
- A failure before `reply.start` sends `error {code}`. A failure after it sends `reply.end {reason: error}`, because audio already played.
- The server keeps the unspoken text in memory for 5 min. History stores the spoken part and marks the turn `truncated`.
- A transcript that matches the "continue" keyword list (EN continue, PL dalej, UK далі) plays the rest, without an LLM call.

## Device tools
Fixed on the server (the device cannot add tools): `set_volume {level 0..100}`, `set_face {face}`, `get_status {}`.
`set_volume` needs a spoken yes (confirm flow). `set_face` and `get_status` do not.
MCP tools need a spoken yes, unless the servers file lists them in `no_confirm` (see [security](security.md)).

## Versioning
`proto` is an integer. Unknown → close 4426.
