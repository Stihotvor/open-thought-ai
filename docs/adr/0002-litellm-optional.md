# ADR 0002: LiteLLM is external and optional

Status: accepted (2026-11-10). Supersedes decision 5 in [ADR 0001](0001-key-decisions.md).

## Context
ADR 0001 put a LiteLLM container in the stack for all model calls.
The stack has a budget of 30% of the t630 (1.2 CPU, 1800 MB). Many users already run LiteLLM, or need only one provider.

## Decision
- The server calls OpenAI-compatible endpoints directly. Each role (chat, STT, TTS, moderation, embeddings) has its own base URL, key and model. A role with no base URL uses the default endpoint and its key. A role with its own base URL must have its own key: a missing key is a startup error. A key is never sent to another host.
- The default is the Mistral API, with a direct Mistral key.
- LiteLLM is not in `compose.yaml`. A user can point any role at an external LiteLLM proxy to get provider fallback.

## Consequences
- Smaller stack and fewer moving parts. No master key in this stack.
- Without LiteLLM there is no provider fallback. A failed call ends the turn with its error code (`stt_failed`, `llm_failed`, `tts_failed`), or with `rate_limited` when the 429 retries run out.
- The server must not use provider-specific features that LiteLLM cannot pass through.
