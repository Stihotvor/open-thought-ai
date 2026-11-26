# Security

Threat model: a home LAN with guests and IoT devices, family members including children, and external MCP tools that may return attacker-controlled text.

## Device and provisioning
- Provisioning AP starts only after a 5 s button hold, runs at most 5 minutes, then stops.
- AP is WPA2 with a per-device password shown on the OLED. The form also needs a per-boot PIN shown on the OLED.
- AP password: 12 random characters, new on each AP start and never stored, not derived from the MAC (the MAC is public in every beacon). PIN: 6 digits, new on each AP start, compared in constant time. 5 wrong PINs stop the AP. Form body ≤ 1 KB, one AP client. Leaving setup (save, timeout, PIN failures) reboots the device.
- The form shows no saved values. After a wrong PIN or a field error, it shows again only what the user typed for the Wi-Fi name and the server URL. Joining the open network that copies the home SSID is blocked: with a saved password the station accepts only WPA2 or better. Logs never print the token, the Wi-Fi password, the AP password or the PIN.
- The token is never shown again after it is saved.
- NVS encryption: OFF in M1 and M2 (owner decision via the architect, 2026-11-15: a dev board, and no irreversible eFuse burn without owner approval). The Wi-Fi password and the device token are in plain NVS. Anyone with USB access can read them with `esptool read_flash`. Mitigation: "regenerate token" in the UI, then reprovision. The `nvs_keys` partition is reserved for it.
- Release gate: before any non-dev use, decide HMAC-based NVS encryption (eFuse key burn), flash encryption and USB-JTAG disable together, and document the residual risk.
- Erase the flash (`idf.py erase-flash`) before the first flash of this firmware and before you flash other firmware on a shared dev board: other firmware can read our NVS.
- No OTA. The device runs only what was flashed by USB.

## Transport
- MVP: LAN only. Compose binds ports to the LAN interface, never `0.0.0.0` on a public host.
- `ws://` exposes the token and voice audio on the LAN. Accepted for the MVP. `wss://` (with a RAM spike test in M2) is required before any non-LAN use.
  - Known effect (M2 review): a LAN attacker who sniffs the token can connect as the device. The server then replaces the real session with close 4000, and the device stops until it is set up again. A forged close frame can do the same without the token. `wss://` with server certificate checks closes both gaps.
- Device input limits (M2): the device refuses JSON deeper than 4 levels before it parses, text frames over 1 KB, and messages with a bad turn id. It echoes a tool call id only when it needs no JSON escape. It logs server strings cut to 32 characters.
- Token: ≥ 32 random bytes, hash stored on the server, "regenerate token" in the UI (then reprovision by the button gesture).
- Auth failures throttled per IP (5 per minute) and logged.
- Limits: text frame 1 KB, audio frame 3.2 KB, 30 s audio per turn, 5 s pre-auth and hello deadlines.

## Web UI
- Admin password stored as an argon2 hash in `.env`. Login rate limit and lockout.
- Session cookie `HttpOnly`, `SameSite=Strict`, `Secure` once behind TLS.
- CSRF token on every state-changing route, plus `Origin` check.
- Jinja autoescape on, no `|safe`. CSP and security headers.
- Re-enter the password for: add MCP server, change secret headers, disable `confirm`, forget everything, regenerate token.
- Audit log of admin changes.

## MCP hub and tools
- New tools start `enabled=false`, `confirm=true`. Tools are namespaced `<server>__<tool>`.
- Device tool schemas are fixed in the server. The device cannot declare tools.
- Each MCP tool is pinned by a SHA-256 hash in the `tools` map of the servers file (example: `docs/examples/mcp/servers.json`). The map is tool name to `"sha256:<64 hex>"`. A tool with a missing or different hash is not admitted. The server then logs the tool name and the current hash at WARNING.
  - To approve a tool, check its description and schema. Then copy the hash from the WARNING log into `tools` and restart the server.
- A tool with a description over 1 000 chars, or an input schema over 4 000 chars of canonical JSON, is not admitted.
- Tool args and results are validated against the JSON Schema. Results are wrapped as untrusted data.
- At most 4 tool calls per turn.
- Confirmation is enforced by the hub, bound to the tool name, args and turn, single use. The rules and the fixed texts are in [protocol](protocol.md#confirm-flow-risky-tools).
  - Every MCP tool needs a spoken yes by default.
  - `no_confirm` in a servers file entry is an optional list of bare tool names. Each name must be a key of `tools`. An unknown name rejects the entry. The default is empty, so all MCP tools of the entry confirm. Add only read-only tools, for example `"no_confirm": ["get_current_time"]`.
  - Device tools: `set_volume` needs a spoken yes. `set_face` and `get_status` do not.
  - The yes match is exact and has no LLM. The LLM cannot answer for the user.
  - Accepted risk: anyone in earshot can say "yes".
- SSRF: MCP URLs only `http`/`https`. The hub resolves DNS and checks the IP on save, on connect and on each reconnect. It blocks loopback, link-local, metadata ranges and compose service names, except hosts on an explicit allowlist (for example the n8n host).
  - The allowlist is `OTA_MCP_ALLOWED_HOSTS`. It is a comma-separated list of hosts. The default is empty.
  - `http` is allowed only to a loopback host or an allowlisted host. Other hosts must use `https`.
  - No redirects. A redirect makes the connect or the call fail.
- Accepted risk: DNS rebinding. The hub resolves the host, and the HTTP client resolves it again at connect. A hostile DNS server can return a safe IP first and a blocked IP second. Follow-up: an httpcore backend that connects only to the vetted IP.
- Secret headers reference only `MCP_SECRET_*` env names, each bound to one host. The `headers` map in the servers file is header name to env name, for example `"Authorization": "MCP_SECRET_N8N"`. The file never holds the secret value.
  - One `MCP_SECRET_*` name is in one entry only. A second entry with the same name is rejected.
  - If the variable is not set, the server is denied. Other servers still start.
  - A stdio entry with a secret-like `env` key or a `MCP_SECRET_` value is rejected.
- The source name `device` is reserved. A servers file entry with that name is rejected.
- Raw error bodies from MCP servers are not shown in the UI.

## Models and data
- Model keys and base URLs are in `.env` only, never in logs or the UI. Use a key per role with the least scope the provider allows.
- A key is bound to the base URL of its role. A role with its own base URL needs its own key, or the server does not start. Base URLs with embedded credentials are rejected.
- A model endpoint that carries a key must use `https://`, unless it is on loopback. `OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1` allows `http://` to a private LAN IP only. The server then logs a warning at startup.
- Base URLs are never editable in the web UI. If that changes, apply the same re-auth and IP checks as for MCP URLs.
- Child processes (stdio MCP servers) get a clean environment with only the variables they declare, never the model keys or `MCP_SECRET_*`.
- No settings export, debug or `/env` endpoint shows model keys or base URLs.
- With the optional external LiteLLM: the server uses a virtual key with a rate limit, limited to the model routes it needs. Never give it the master key.
- No moderation layer (owner decision, 2026-11-26): the server does not moderate text. It relies on the safety filters of the model gateway or provider in front of chat, STT and TTS. Check that your gateway has them on before you give the device to a child. The server cannot show that they work.
- The system prompt refuses violence, weapons, self-harm, drugs and crime, and forbids asking for personal data and giving medicine doses. With no moderation layer, the prompt and the gateway filters are the only controls.
- Accepted risk: a reply with medicine doses or a request for personal data can pass when a user talks the model past its prompt.
- Owner decision (2026-11-14): the MVP is not for children. No LLM judge: the system prompt, the gateway filters and a safe chat model are the controls.
- Chat history: the last `OTA_HISTORY_TURNS` turns (default 15) of one device connection, in server memory only. A reconnect or a restart clears it. It keeps only the reply chunks the device played in full. At most 8 000 characters of it go to the chat provider with every turn (the newest turns first).
- Residual risk: a slow jailbreak over several harmless-looking turns can steer later replies. The system prompt applies to every turn. Gateway filters may check each call alone.
- Memory is written only from user turns.
- Data that leaves the LAN: with a cloud provider (the default is the Mistral API), the voice audio, transcripts, replies and memory facts in the prompt go to that provider. Free tiers may keep this data or train on it. For a child-use device, prefer local providers (Parakeet STT and Piper TTS, see research/m3-audio-probe.md). The web UI shows which provider each role uses.
- Logs never contain the token, `Authorization`, secret header values, model keys, or provider error bodies. Transcripts are logged only at DEBUG.
- `.env` is mode 600 and git-ignored. CI runs a secret scan. `.env.example` has placeholders only.
- Security events are logged: auth failures, session replacements, admin changes, tool calls, confirmations.
