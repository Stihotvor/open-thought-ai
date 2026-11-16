#include "player.h"

#include <stdbool.h>
#include <string.h>

#include "audio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

static const char *TAG = "player";

#define JITTER_BYTES (48 * 1024) // docs/protocol.md: 48 KB, about 1.5 s against Wi-Fi stalls
#define CREDIT_STEP 3200         // give credit when this much buffer space is free again (100 ms)
// Start (and restart after a gap) only with this much audio in the buffer: it rides out
// late frames on a weak radio. Below the server start credit (48000), so it always fills.
#define PREBUFFER_BYTES 6400     // 200 ms
#define CHUNK_SAMPLES AUDIO_FRAME_SAMPLES
#define WAIT_MS 20               // the task checks for a flush at least this often

static StreamBufferHandle_t s_buffer;
// The link task writes the buffer and the player task resets it. A stream buffer is not
// safe for a reset during a send, so both hold this lock.
static SemaphoreHandle_t s_lock;
static player_credit_t s_credit;
static player_drained_t s_drained;

// Written by the app task under s_lock. Each begin or flush adds one to the generation:
// the player task then empties the buffer, and credit of an older generation is dropped.
static int s_turn = -1;              // the accepted turn, -1 = drop all audio
static uint32_t s_generation;
static volatile bool s_ending;       // no lock: one bool, the app task sets it, the player clears it
static uint32_t s_dropped;           // bytes lost to a full buffer (under s_lock)

static void player_task(void *arg)
{
    (void)arg;
    static int16_t chunk[CHUNK_SAMPLES];
    uint8_t *bytes = (uint8_t *)chunk;
    size_t have = 0;          // bytes in `chunk`: an odd byte waits for its pair
    int consumed = 0;         // bytes played since the last credit
    uint32_t generation = 0;  // the generation this task has emptied the buffer for
    bool playing = false;     // false = fill to PREBUFFER_BYTES first
    int gaps = 0;             // times the buffer ran dry in this reply
    for (;;) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (generation != s_generation) {
            generation = s_generation;
            xStreamBufferReset(s_buffer);  // no send can run: the link task needs s_lock
            have = 0;
            consumed = 0;
            playing = false;
            gaps = 0;
        }
        uint32_t dropped = s_dropped;
        s_dropped = 0;
        bool accepting = s_turn >= 0;
        xSemaphoreGive(s_lock);

        if (dropped > 0) {
            ESP_LOGW(TAG, "jitter buffer full: %u bytes dropped", (unsigned)dropped);
        }
        if (!playing) {
            if (xStreamBufferBytesAvailable(s_buffer) < PREBUFFER_BYTES && !s_ending) {
                vTaskDelay(pdMS_TO_TICKS(WAIT_MS));
                continue;
            }
            if (gaps == 0) {
                audio_take_underruns();  // drop the count of the idle time before the reply
            }
            playing = true;
        }

        size_t got = xStreamBufferReceive(s_buffer, bytes + have, sizeof(chunk) - have,
                                          pdMS_TO_TICKS(WAIT_MS));
        if (got == 0 && !s_ending) {
            playing = false;  // ran dry mid-reply: refill before the next sound
            gaps++;
        }
        have += got;
        size_t samples = have / 2;
        if (samples > 0 && generation == s_generation) {  // skip a chunk a flush made stale
            audio_play(chunk, samples);
            consumed += (int)(samples * 2);
        }
        have -= samples * 2;
        memmove(bytes, bytes + samples * 2, have);

        if (consumed >= CREDIT_STEP && accepting && generation == s_generation) {
            s_credit(consumed);  // queued by the link: this call does not block
            consumed = 0;
        }
        if (s_ending && have == 0 && xStreamBufferIsEmpty(s_buffer)) {
            s_ending = false;
            if (gaps > 0) {
                ESP_LOGW(TAG, "reply had %d gaps (late audio)", gaps);
            }
            uint32_t underruns = audio_take_underruns();
            if (underruns > 1) {  // the last DMA buffer after the reply always counts
                ESP_LOGW(TAG, "reply had %u speaker underruns", (unsigned)underruns);
            }
            gaps = 0;
            playing = false;
            s_drained();
        }
    }
}

void player_start(player_credit_t credit, player_drained_t drained)
{
    s_credit = credit;
    s_drained = drained;
    s_buffer = xStreamBufferCreate(JITTER_BYTES, 1);
    s_lock = xSemaphoreCreateMutex();
    configASSERT(s_buffer && s_lock);
    xTaskCreate(player_task, "player", 4096, NULL, 6, NULL);
}

static void new_generation(int turn)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_turn = turn;
    s_generation++;
    s_ending = false;
    xSemaphoreGive(s_lock);
}

void player_begin(int turn)
{
    new_generation(turn);
}

void player_flush(void)
{
    new_generation(-1);
}

void player_push(int turn, const uint8_t *pcm, size_t bytes)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (turn == s_turn && s_turn >= 0) {  // else a stale turn, or flushed
        s_dropped += bytes - xStreamBufferSend(s_buffer, pcm, bytes, 0);
    }
    xSemaphoreGive(s_lock);
}

void player_end(void)
{
    s_ending = true;
}
