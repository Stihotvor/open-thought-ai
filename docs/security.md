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
- Model keys and base URLs are in `.env` only, never in logs or the UI. Use a key per role with the least scope the provider allows.
- A key is bound to the base URL of its role. A role with its own base URL needs its own key, or the server does not start. Base URLs with embedded credentials are rejected.
- A model endpoint that carries a key must use `https://`, unless it is on loopback. `OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1` allows `http://` to a private LAN IP only. The server then logs a warning at startup.
- Base URLs are never editable in the web UI. If that changes, apply the same re-auth and IP checks as for MCP URLs.
- Child processes (stdio MCP servers) get a clean environment with only the variables they declare, never the model keys or `MCP_SECRET_*`.
- No settings export, debug or `/env` endpoint shows model keys or base URLs.
- With the optional external LiteLLM: the server uses a virtual key with a rate limit, limited to the model routes it needs. Never give it the master key.
- Moderation fails closed: any error, timeout, `rate_limited` or unknown response shape → neutral refusal (`moderated`). Voice mode does not start without a moderation role.
- What is checked: the transcript before chat (in pieces and whole), every TTS chunk plus the question with the reply (the last 500 and first 1500 characters) before TTS, and later tool args and results.
- Self-test: before the first voice turn, a known-harmful and a harmless phrase in the device language must get the right verdicts. Until they do, every turn is refused and an error is logged. It is a smoke test: it shows that the self-harm category fires on one blatant phrase in that language. It does not show that every category works in Polish and Ukrainian, which Mistral does not list.
- Canary: the harmful self-test phrase rides along in every moderation batch. If it is ever not flagged (another model, a stub, a fallback route), the turn is refused and the self-test runs again.
- A result must carry one complete known category schema (Mistral or OpenAI) with boolean values. Every category is blocked except health, financial, law and personal data, so a child can ask about a headache. An unknown category that is true also blocks.
- The system prompt also forbids asking for personal data, secrets from parents, and medicine doses. These are not moderation categories, so the prompt is the only control for now.
- Accepted risk (owner decision, 2026-11-12): health and personal data stay allowed also in replies, so a harmless reply about a headache is not refused. A reply with medicine doses or a request for personal data can therefore pass moderation when a child talks the model past its prompt. Planned control: an LLM judge with a child-safety rubric on the reply.
- A flagged turn logs which side was flagged (transcript or reply), never the text.
- Memory is written only from user turns.
- Data that leaves the LAN: with a cloud provider (the default is the Mistral API), the voice audio, transcripts, replies and memory facts in the prompt go to that provider. Free tiers may keep this data or train on it. For a child-use device, prefer local providers (Parakeet STT and Piper TTS, see research/m3-audio-probe.md). The web UI shows which provider each role uses.
- Logs never contain the token, `Authorization`, secret header values, model keys, or provider error bodies. Transcripts are logged only at DEBUG.
- `.env` is mode 600 and git-ignored. CI runs a secret scan. `.env.example` has placeholders only.
- Security events are logged: auth failures, session replacements, admin changes, tool calls, confirmations.
