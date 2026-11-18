# Decision: per-sentence streaming in the voice pipeline

Date: 2026-11-18. Follows issue #26 (latency report, PR #59).

## Problem
`VoicePipeline` waited for the whole chat reply, moderated it, then ran TTS. The M3 budget rows "LLM to first sentence" and "Moderation of sentence 1" were not real steps.

## Options shown at the design gate
- Pair check: (a) question + reply so far with every chunk, (b) pair on sentence 1 only, (c) keep the whole-reply check before sound.
- Moderation cost: (a) moderation and TTS of a chunk in parallel, (b) sequential, (c) optional setting, (d) remove moderation.
- Concurrency: (a) chat producer task + queue, (b) inline reads.

## Choice (owner)
- Keep fail-closed moderation. Each chunk is moderated with the canary and with the question + reply so far (last 500 of the question, last 1500 of the reply up to the chunk).
- Moderation and TTS of a chunk run at the same time. The audio is yielded only once moderation passed.
- A chat task cuts sentences into a `SentenceFeed`. Sentence 1 is a chunk alone. Later sentences that are ready are merged up to `TTS_CHUNK`.

## Consequences
- A flag on a later chunk stops the turn with `moderated` after the earlier chunks played.
- Moderation calls per turn: 1 + number of chunks. A reply ends cleanly at `MAX_CHUNKS` (20, the TTS cap). `CALL_CAPS[MODERATION]` is 21.
- A chat error ends the reply after the sentences that came before it.
- Stages: `mod_reply` is now `mod_s1`. `mod_s1` and `tts1` are parallel spans.
