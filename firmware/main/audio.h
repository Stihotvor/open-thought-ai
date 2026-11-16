// One full-duplex I2S port: microphone in, amplifier out, 16 kHz mono.
#pragma once

#include <stddef.h>
#include <stdint.h>

#define AUDIO_SAMPLE_RATE 16000
#define AUDIO_FRAME_SAMPLES 320  // 20 ms

void audio_init(void);

// 0..100. Applied to everything played after the call.
void audio_set_volume(int volume);

// Play 16-bit PCM. Blocks until the samples are in the DMA buffers. Call from one task.
void audio_play(const int16_t *pcm, size_t samples);

// DMA underruns since the last call: the speaker played silence because no new audio
// came in time. While nothing plays, every DMA buffer counts, so read it around playback.
uint32_t audio_take_underruns(void);

// Barge-in measurement: after a watch, the time (esp_timer_get_time) when the speaker
// first ran dry, or 0 while it still plays.
void audio_watch_silence(void);
int64_t audio_silent_at(void);

// Play a sine tone for `ms` milliseconds at the current volume.
void audio_tone(int hz, int ms);

// Read one 20 ms frame of 16-bit PCM. Returns the number of samples (0 on error).
// Call from one task.
size_t audio_read(int16_t pcm[AUDIO_FRAME_SAMPLES]);

// Root mean square of a frame without its DC offset, 0..32767.
int audio_rms(const int16_t *pcm, size_t samples);
