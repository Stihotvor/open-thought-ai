#include "player.h"

#include <stdbool.h>
#include <string.h>

#include "audio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

static const char *TAG = "player";

#define JITTER_BYTES 12288       // docs/protocol.md: 12 KB, about 375 ms
#define CREDIT_STEP 4096         // give credit when this much buffer space is free again
#define CHUNK_BYTES (AUDIO_FRAME_SAMPLES * 2)
#define WAIT_MS 20               // the task checks for a flush at least this often

static StreamBufferHandle_t s_buffer;
static player_credit_t s_credit;
static player_drained_t s_drained;

// Set by the app task, read by the link task (push) and the player task.
static volatile int s_turn = -1;       // the accepted turn, -1 = drop all audio
static volatile bool s_reset;          // the player task empties the buffer
static volatile bool s_ending;         // the reply is complete: report when drained
static volatile uint32_t s_dropped;    // bytes lost to a full buffer (a server bug)

static void player_task(void *arg)
{
    (void)arg;
    static uint8_t chunk[CHUNK_BYTES];
    size_t have = 0;    // bytes in `chunk`: an odd byte waits for its pair
    int consumed = 0;   // bytes played since the last credit
    for (;;) {
        if (s_reset) {
            // Only this task reads the buffer, and the link task never blocks on it,
            // so a reset here cannot fail.
            xStreamBufferReset(s_buffer);
            s_reset = false;
            have = 0;
            consumed = 0;
        }
        have += xStreamBufferReceive(s_buffer, chunk + have, sizeof(chunk) - have,
                                     pdMS_TO_TICKS(WAIT_MS));
        size_t samples = have / 2;
        if (samples > 0 && !s_reset) {
            audio_play((const int16_t *)chunk, samples);
            consumed += (int)(samples * 2);
            have -= samples * 2;
            memmove(chunk, chunk + samples * 2, have);
        }
        if (consumed >= CREDIT_STEP && s_turn >= 0) {
            s_credit(consumed);
            consumed = 0;
        }
        if (s_ending && have == 0 && xStreamBufferIsEmpty(s_buffer)) {
            s_ending = false;
            s_drained();
        }
        if (s_dropped > 0) {
            ESP_LOGW(TAG, "jitter buffer full: %u bytes dropped", (unsigned)s_dropped);
            s_dropped = 0;
        }
    }
}

void player_start(player_credit_t credit, player_drained_t drained)
{
    s_credit = credit;
    s_drained = drained;
    s_buffer = xStreamBufferCreate(JITTER_BYTES, 1);
    configASSERT(s_buffer);
    xTaskCreate(player_task, "player", 3072, NULL, 6, NULL);
}

void player_begin(int turn)
{
    s_ending = false;
    s_reset = true;
    s_turn = turn;
}

void player_push(int turn, const uint8_t *pcm, size_t bytes)
{
    if (turn != s_turn || s_reset) {
        return;  // a stale turn, or a flush is pending
    }
    size_t sent = xStreamBufferSend(s_buffer, pcm, bytes, 0);
    s_dropped += bytes - sent;
}

void player_end(void)
{
    s_ending = true;
}

void player_flush(void)
{
    s_turn = -1;
    s_ending = false;
    s_reset = true;
}
