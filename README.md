# open-thought-ai

A small, self-hosted voice assistant for home and family.
A pocket device (ESP32-C3) records your voice. Your own server answers.

- Own firmware (C, ESP-IDF). No OTA. No cloud vendor server. Update only by USB.
- Own Python server in Docker Compose. One server instance per device.
- Models through OpenAI-compatible endpoints: Mistral directly, Ollama, LM Studio,
  or your own [LiteLLM](https://github.com/BerriAI/litellm) proxy (optional) for fallback across providers (for example Gemini or local speech models).
- Tools through MCP: device tools (volume, face, LED) and your own MCP servers (for example n8n workflows).
- English, Polish, Ukrainian.

## Database migrations
The schema is applied by a one-shot compose service, and `server` waits for it:

```
docker compose run --rm migrate      # runs `ota-migrate` (upgrade to head)
```

`OTA_DATABASE_URL` must point straight at Postgres, not at a pooler.
For the integration tests, start a throwaway Postgres with the `test` profile:

```
docker compose --profile test up -d postgres-test
cd server && OTA_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55432/postgres \
  uv run pytest tests/integration --no-cov
```

Status: design phase. See [docs/](docs/) and the GitHub Project board.

## Docs
- [Requirements](docs/design/mvp/requirements.md)
- [Architecture](docs/architecture.md)
- [Local speech server](docs/guides/speech-server.md)
- [MCP servers (n8n example)](docs/guides/mcp-servers.md)
- [Protocol v1](docs/protocol.md)
- [Data model](docs/data-model.md)
- [Security](docs/security.md)
- [Hardware](docs/hardware.md)
- [Delivery plan](docs/delivery-plan.md)
- [Decisions (ADR)](docs/adr/)

## License
MIT. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
