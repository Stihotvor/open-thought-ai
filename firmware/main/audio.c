#include "audio.h"

#include <math.h>
#include <stdbool.h>

#include "board_pins.h"
#include "driver/i2s_std.h"
#include "freertos/FreeRTOS.h"

// Both chips use 32-bit slots. The INMP441 sends a signed 24-bit sample in the top bits
// of its slot; the MAX98357A takes the top 16 bits we send.
#define MIC_SHIFT 14  // 24-bit sample in a 32-bit slot, with some gain, to 16 bits

// DMA holds 6 x 480 frames = 180 ms of output (default 90 ms): the player task can wait
// that long behind Wi-Fi work before the speaker runs dry.
#define DMA_DESC_NUM 6
#define DMA_FRAME_NUM 480

static i2s_chan_handle_t s_tx;
static volatile uint32_t s_underruns;  // DMA had no new output data (ISR increments)

static bool IRAM_ATTR on_send_overflow(i2s_chan_handle_t handle, i2s_event_data_t *event,
                                       void *ctx)
{
    (void)handle; (void)event; (void)ctx;
    s_underruns++;
    return false;
}
static i2s_chan_handle_t s_rx;
static int s_volume = 60;

void audio_init(void)
{
    i2s_chan_config_t chan_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    // Send silence when no new audio comes, instead of the last DMA buffers in a loop.
    chan_config.auto_clear_after_cb = true;
    chan_config.dma_desc_num = DMA_DESC_NUM;
    chan_config.dma_frame_num = DMA_FRAME_NUM;
    ESP_ERROR_CHECK(i2s_new_channel(&chan_config, &s_tx, &s_rx));

    i2s_std_config_t std_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_BCLK,
            .ws = PIN_I2S_WS,
            .dout = PIN_I2S_DOUT,
            .din = PIN_I2S_DIN,
        },
    };
    std_config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;  // the mic L/R pin is tied low
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_tx, &std_config));
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx, &std_config));
    const i2s_event_callbacks_t callbacks = {.on_send_q_ovf = on_send_overflow};
    ESP_ERROR_CHECK(i2s_channel_register_event_callback(s_tx, &callbacks, NULL));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx));
    ESP_ERROR_CHECK(i2s_channel_enable(s_rx));
}

void audio_set_volume(int volume)
{
    s_volume = volume < 0 ? 0 : (volume > 100 ? 100 : volume);
}

void audio_play(const int16_t *pcm, size_t samples)
{
    // On the stack (1.3 KB): the player task and the app task (tones) both call this.
    // The I2S driver serializes the writes.
    int32_t out[AUDIO_FRAME_SAMPLES];
    while (samples > 0) {
        size_t n = samples < AUDIO_FRAME_SAMPLES ? samples : AUDIO_FRAME_SAMPLES;
        for (size_t i = 0; i < n; i++) {
            out[i] = (int32_t)(pcm[i] * s_volume / 100) * 65536;  // top 16 bits of the slot
        }
        size_t written;
        i2s_channel_write(s_tx, out, n * sizeof(int32_t), &written, portMAX_DELAY);
        pcm += n;
        samples -= n;
    }
}

uint32_t audio_take_underruns(void)
{
    uint32_t n = s_underruns;  // a 32-bit read is atomic on the C3
    s_underruns -= n;
    return n;
}

// The ESP32-C3 has no FPU: sinf() for each sample is slower than real time, so the DMA
// runs dry and the tone breaks up. Build one sine period once, then step through it.
#define SINE_TABLE_SIZE 256
#define TONE_AMPLITUDE 12000

void audio_tone(int hz, int ms)
{
    static int16_t table[SINE_TABLE_SIZE];
    static bool table_ready;  // tones play only from the app task: no lock needed
    if (!table_ready) {
        for (int i = 0; i < SINE_TABLE_SIZE; i++) {
            table[i] = (int16_t)(TONE_AMPLITUDE * sinf(2 * (float)M_PI * i / SINE_TABLE_SIZE));
        }
        table_ready = true;
    }
    // Phase in 16.16 fixed point, in table steps.
    const uint32_t step = (uint32_t)(((uint64_t)hz * SINE_TABLE_SIZE << 16) / AUDIO_SAMPLE_RATE);
    uint32_t phase = 0;
    int16_t frame[AUDIO_FRAME_SAMPLES];
    int total = (int)((int64_t)AUDIO_SAMPLE_RATE * ms / 1000);
    for (int done = 0; done < total; done += AUDIO_FRAME_SAMPLES) {
        for (int i = 0; i < AUDIO_FRAME_SAMPLES; i++) {
            frame[i] = table[(phase >> 16) % SINE_TABLE_SIZE];
            phase += step;
        }
        int left = total - done;
        audio_play(frame, left < AUDIO_FRAME_SAMPLES ? left : AUDIO_FRAME_SAMPLES);
    }
}

size_t audio_read(int16_t pcm[AUDIO_FRAME_SAMPLES])
{
    static int32_t raw[AUDIO_FRAME_SAMPLES];  // one reader (the mic task): static saves stack
    size_t bytes = 0;
    if (i2s_channel_read(s_rx, raw, sizeof(raw), &bytes, 200) != ESP_OK) {
        return 0;
    }
    size_t n = bytes / sizeof(int32_t);
    for (size_t i = 0; i < n; i++) {
        int32_t s = raw[i] >> MIC_SHIFT;
        pcm[i] = (int16_t)(s > 32767 ? 32767 : (s < -32768 ? -32768 : s));
    }
    return n;
}

int audio_rms(const int16_t *pcm, size_t samples)
{
    if (samples == 0) {
        return 0;
    }
    int64_t mean = 0;  // remove the microphone DC offset, so silence reads near 0
    for (size_t i = 0; i < samples; i++) {
        mean += pcm[i];
    }
    mean /= (int64_t)samples;
    int64_t sum = 0;
    for (size_t i = 0; i < samples; i++) {
        int64_t d = pcm[i] - mean;
        sum += d * d;
    }
    return (int)sqrtf((float)(sum / (int64_t)samples));
}
