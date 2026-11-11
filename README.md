# open-thought-ai

A small, self-hosted voice assistant for home and family.
A pocket device (ESP32-C3) records your voice. Your own server answers.

- Own firmware (C, ESP-IDF). No OTA. No cloud vendor server. Update only by USB.
- Own Python server in Docker Compose. One server instance per device.
- Models through OpenAI-compatible endpoints: Mistral directly, Ollama, LM Studio,
  or your own [LiteLLM](https://github.com/BerriAI/litellm) proxy (optional) for fallback across providers.
- Tools through MCP: device tools (volume, face, LED) and your own MCP servers (for example n8n workflows).
- English, Polish, Ukrainian.

Status: design phase. See [docs/](docs/) and the GitHub Project board.

## Docs
- [Requirements](docs/design/mvp/requirements.md)
- [Architecture](docs/architecture.md)
- [Protocol v1](docs/protocol.md)
- [Data model](docs/data-model.md)
- [Security](docs/security.md)
- [Hardware](docs/hardware.md)
- [Delivery plan](docs/delivery-plan.md)
- [Decisions (ADR)](docs/adr/)

## License
MIT. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
