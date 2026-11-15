// Reply playback: a 12 KB jitter buffer, a playback task and the audio credit
// (docs/protocol.md, "Flow control").
#pragma once

#include <stddef.h>
#include <stdint.h>

typedef void (*player_credit_t)(int bytes);  // give the server this much new credit
typedef void (*player_drained_t)(void);      // the reply after player_end() has played

void player_start(player_credit_t credit, player_drained_t drained);

// A new turn: drop any old audio and accept only audio of `turn`.
void player_begin(int turn);

// Reply audio from the link task. Audio of another turn is dropped.
void player_push(int turn, const uint8_t *pcm, size_t bytes);

// The reply is complete: play what is buffered, then call `drained`.
void player_end(void);

// Abort: stop at once and drop all audio until the next player_begin().
void player_flush(void);
