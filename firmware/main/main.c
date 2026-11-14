// M1 bring-up: faces, button, test tone, microphone level, Wi-Fi and SoftAP setup.
//
// One app task owns the state. The button, Wi-Fi and microphone send it events through a
// queue, so no state is shared between tasks.
#include <stdbool.h>

#include "audio.h"
#include "board_pins.h"
#include "button.h"
#include "config.h"
#include "esp_log.h"
#include "face.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "net.h"
#include "provision.h"

static const char *TAG = "app";

typedef enum {
    STATE_SETUP,         // not provisioned: waits for the 5 s hold
    STATE_PROVISIONING,  // access point and form are up
    STATE_OFFLINE,       // provisioned, Wi-Fi not connected
    STATE_IDLE,
    STATE_LISTENING,     // M1: logs the microphone level
} app_state_t;

typedef enum {
    EVENT_PRESS,
    EVENT_HOLD,
    EVENT_ONLINE,
    EVENT_OFFLINE,
} app_event_t;

static QueueHandle_t s_events;
// Read by the mic task. True at boot, so the log shows one microphone level for the M1
// check without a button press.
static volatile bool s_listening = true;

static void on_button(button_event_t event)
{
    app_event_t e = event == BUTTON_HOLD ? EVENT_HOLD : EVENT_PRESS;
    xQueueSend(s_events, &e, 0);
}

static void on_net(bool online)
{
    app_event_t e = online ? EVENT_ONLINE : EVENT_OFFLINE;
    xQueueSend(s_events, &e, 0);
}

// Reads the microphone all the time (it keeps the shared I2S clock busy) and logs the
// level once a second while listening.
static void mic_task(void *arg)
{
    (void)arg;
    int16_t frame[AUDIO_FRAME_SAMPLES];
    int peak = 0;
    int frames = 0;
    for (;;) {
        size_t n = audio_read(frame);
        if (!s_listening) {
            continue;
        }
        int rms = audio_rms(frame, n);
        peak = rms > peak ? rms : peak;
        if (++frames == 50) {  // 50 x 20 ms
            ESP_LOGI(TAG, "mic level: rms peak %d", peak);
            frames = 0;
            peak = 0;
        }
    }
}

static void show_setup_hint(void)
{
    const char *const lines[] = {"NOT SET UP", "", "HOLD BUTTON 5 S", "TO START SETUP"};
    face_show_text(lines, 4);
}

static app_state_t enter(app_state_t state)
{
    s_listening = state == STATE_LISTENING;
    switch (state) {
        case STATE_SETUP: show_setup_hint(); break;
        case STATE_PROVISIONING: provision_start(); break;  // shows its own screen
        case STATE_OFFLINE: face_show(FACE_OFFLINE); break;
        case STATE_IDLE: face_show(FACE_IDLE); break;
        case STATE_LISTENING: face_show(FACE_LISTENING); break;
    }
    return state;
}

static app_state_t on_event(app_state_t state, app_event_t event)
{
    if (state == STATE_PROVISIONING) {
        return state;  // setup ends with a reboot
    }
    switch (event) {
        case EVENT_HOLD:
            return enter(STATE_PROVISIONING);
        case EVENT_ONLINE:
            return state == STATE_OFFLINE ? enter(STATE_IDLE) : state;
        case EVENT_OFFLINE:
            return state == STATE_SETUP ? state : enter(STATE_OFFLINE);
        case EVENT_PRESS:
            if (state == STATE_IDLE) {
                return enter(STATE_LISTENING);
            }
            if (state == STATE_LISTENING || state == STATE_OFFLINE) {
                face_show(FACE_SPEAKING);
                audio_tone(660, 300);  // M1 check: the amplifier plays a tone
                return enter(state == STATE_LISTENING ? STATE_IDLE : STATE_OFFLINE);
            }
            return state;
    }
    return state;
}

void app_main(void)
{
    const gpio_config_t led = {.pin_bit_mask = 1ULL << PIN_LED, .mode = GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&led));
    gpio_set_level(PIN_LED, 1);

    s_events = xQueueCreate(8, sizeof(app_event_t));
    config_init();
    face_init();
    audio_init();
    net_init(on_net);
    button_start(on_button);
    audio_tone(880, 150);  // boot beep
    vTaskDelay(pdMS_TO_TICKS(200));  // let the beep fade, so the check hears the room
    xTaskCreate(mic_task, "mic", 4096, NULL, 5, NULL);
    vTaskDelay(pdMS_TO_TICKS(1100));  // the mic task logs one level (boot check)

    config_t config;
    config_load(&config);
    app_state_t state;
    if (config.provisioned) {
        state = enter(STATE_OFFLINE);
        net_connect(config.ssid, config.wifi_pass);
    } else {
        state = enter(STATE_SETUP);
    }
    ESP_LOGI(TAG, "started, %s", config.provisioned ? "provisioned" : "not set up");

    for (;;) {
        app_event_t event;
        xQueueReceive(s_events, &event, portMAX_DELAY);
        state = on_event(state, event);
    }
}
