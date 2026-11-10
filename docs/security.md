# Security

Threat model: a home LAN with guests and IoT devices, family members including children, and external MCP tools that may return attacker-controlled text.

## Device and provisioning
- Provisioning AP starts only after a 5 s button hold, runs at most 5 minutes, then stops.
- AP is WPA2 with a per-device password shown on the OLED. The form also needs a per-boot PIN shown on the OLED.
- The token is never shown again after it is saved.
- NVS encryption on. Flash encryption and USB-JTAG disable: OPEN (risk of bricking during development); decide before the public release, document the residual risk.
- No OTA. The device runs only what was flashed by USB.

## Transport
- MVP: LAN only. Compose binds ports to the LAN interface, never `0.0.0.0` on a public host.
- `ws://` exposes the token and voice audio on the LAN. Accepted for the MVP. `wss://` (with a RAM spike test in M2) is required before any non-LAN use.
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
- A changed tool description or schema (hash) disables the tool until re-approved. Description length is capped at 1 000 chars.
- Tool args and results are validated against the JSON Schema. Results are wrapped as untrusted data.
- At most 4 tool calls per turn.
- Confirmation is enforced by the hub, bound to the tool name, args and turn, single use. Accepted risk: anyone in earshot can say "yes".
- SSRF: MCP URLs only `http`/`https`. The hub resolves DNS and checks the IP on save and on connect. It blocks loopback, link-local, metadata ranges and compose service names, except hosts on an explicit allowlist (for example the n8n host). No redirects.
- Secret headers reference only `MCP_SECRET_*` env names, each bound to one host.
- Raw error bodies from MCP servers are not shown in the UI.

## Models and data
- Model keys are in `.env` only, never in logs or the UI. Use a key per role with the least scope the provider allows.
- With the optional external LiteLLM: the server uses a virtual key with a rate limit, limited to the model routes it needs. Never give it the master key.
- Moderation fails closed: any error → neutral refusal. Moderation runs per sentence before TTS, and on tool args and results.
- Memory is written only from user turns.
- Logs never contain the token, `Authorization`, or secret header values. Transcripts are logged only at DEBUG.
- `.env` is mode 600 and git-ignored. CI runs a secret scan. `.env.example` has placeholders only.
- Security events are logged: auth failures, session replacements, admin changes, tool calls, confirmations.
