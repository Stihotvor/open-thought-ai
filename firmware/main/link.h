// WebSocket link to the server: protocol v1 (docs/protocol.md).
//
// The link sends JSON messages and the microphone audio, and turns server messages into
// events for the app task. Reply audio goes to the player (player.h).
#pragma once

#include <stddef.h>
#include <stdint.h>

typedef enum {
    LINK_UP,           // server hello: value = volume 0..100, or -1 when it has none
    LINK_DOWN,         // connection lost: the link reconnects with backoff
    LINK_STOPPED,      // a close code that forbids reconnect: value = close code
    LINK_VOLUME,       // settings.update: value = volume
    LINK_TURN_START,   // value = turn
    LINK_THINKING,     // value = turn
    LINK_REPLY_START,  // value = turn
    LINK_REPLY_END,    // value = turn
    LINK_ERROR,        // value = turn
    LINK_REFUSED,      // listen.start refused (too_many_turns): no new turn
} link_event_t;

// Close codes that stop the device for good (docs/protocol.md).
#define LINK_CLOSE_REPLACED 4000
#define LINK_CLOSE_BAD_TOKEN 4401
#define LINK_CLOSE_BAD_VERSION 4426

// Runs in the link task: keep it short (post to a queue).
typedef void (*link_handler_t)(link_event_t event, int value);

// Connect to `url` with the bearer `token`, and reconnect with backoff 1 -> 60 s.
// The strings are copied.
void link_start(const char *url, const char *token, link_handler_t handler);

// Stop for good (after LINK_STOPPED). Call from the app task, not from the handler.
void link_halt(void);

void link_send_listen_start(void);
void link_send_listen_stop(int turn);
void link_send_abort(int turn);
// Credit adds up and goes out as one message: it never pushes out a control message.
void link_send_credit(int bytes);

// One audio frame of the turn: at most LINK_AUDIO_SAMPLES samples (100 ms).
#define LINK_AUDIO_SAMPLES 1600
void link_send_audio(int turn, const int16_t *pcm, size_t samples);
