// The app: setup, Wi-Fi, the server link and push-to-talk turns (docs/protocol.md).
//
// One app task owns the state. The button, Wi-Fi, the link and the player send it events
// through a queue. The only other shared value is `s_stream_turn`, which the app task sets
// and the mic task reads.
#include <stdbool.h>

#include "audio.h"
#include "board_pins.h"
#include "button.h"
#include "config.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "face.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "link.h"
#include "net.h"
#include "player.h"
#include "provision.h"

static const char *TAG = "app";

#define MAX_TURN_SECONDS 30  // docs/protocol.md: at most 30 s of audio per turn
#define FRAMES_PER_SECOND (AUDIO_SAMPLE_RATE / LINK_AUDIO_SAMPLES)  // 100 ms frames

typedef enum {
    STATE_SETUP,         // not provisioned: waits for the 5 s hold
    STATE_PROVISIONING,  // access point and form are up
    STATE_OFFLINE,       // no Wi-Fi, or no server session yet
    STATE_HALTED,        // the server refused us for good (bad token, version, replaced)
    STATE_IDLE,          // keep the session states last: is_session() compares with this
    STATE_LISTENING,     // listen.start sent: audio streams after turn.start
    STATE_THINKING,
    STATE_SPEAKING,
    STATE_CONFIRM,       // a risky tool call waits for a spoken yes (docs/protocol.md)
} app_state_t;

typedef enum {
    EVENT_PRESS,
    EVENT_HOLD,
    EVENT_ONLINE,
    EVENT_OFFLINE,
    EVENT_LINK,       // value: a link_event_t, arg: its value (turn, volume or close code)
    EVENT_DRAINED,    // the player played the whole reply
    EVENT_TURN_FULL,  // value: the turn that streamed MAX_TURN_SECONDS of audio
    EVENT_STREAMED,   // value: the turn whose last audio the mic task has sent
} event_kind_t;

typedef struct {
    event_kind_t kind;
    int value;
    int arg;
} app_event_t;

static QueueHandle_t s_events;
static config_t s_config;
static bool s_link_started;
static int s_turn = -1;                    // the current turn, -1 = none (app task only)
static int s_done_turn = -1;               // the last turn that played to the end
static int s_confirm_turn = -1;            // a confirm that waits for the reply to drain
static bool s_stop_pending;                // send listen.stop once the mic has streamed
static volatile int s_stream_turn = -1;    // the mic task streams this turn, -1 = off
static volatile bool s_boot_check = true;  // the mic task logs one level at boot

static void post(event_kind_t kind, int value, int arg)
{
    const app_event_t e = {.kind = kind, .value = value, .arg = arg};
    if (xQueueSend(s_events, &e, 0) != pdTRUE) {
        ESP_LOGW(TAG, "event queue full: event %d lost", kind);
    }
}

static void on_button(button_event_t event)
{
    post(event == BUTTON_HOLD ? EVENT_HOLD : EVENT_PRESS, 0, 0);
}

static void on_net(bool online)
{
    post(online ? EVENT_ONLINE : EVENT_OFFLINE, 0, 0);
}

static void on_link(link_event_t event, int value)
{
    post(EVENT_LINK, event, value);
}

static void on_drained(void)
{
    post(EVENT_DRAINED, 0, 0);
}

// Reads the microphone all the time (it keeps the shared I2S clock busy). While a turn
// streams, it sends 100 ms frames to the server.
static void mic_task(void *arg)
{
    (void)arg;
    static int16_t frame[LINK_AUDIO_SAMPLES];
    size_t have = 0;
    int frames = 0;  // frames sent in this turn
    int turn = -1;
    for (;;) {
        size_t n = audio_read(frame + have);
        if (n == 0) {
            vTaskDelay(pdMS_TO_TICKS(20));  // an I2S error: do not spin
            continue;
        }
        if (s_boot_check) {
            ESP_LOGI(TAG, "mic level: rms %d", audio_rms(frame + have, n));
            s_boot_check = false;
        }
        if (s_stream_turn != turn) {  // a new turn, or streaming stopped
            if (turn >= 0) {
                have += n;  // this read belongs to the old turn too
                if (have > 0) {
                    link_send_audio(turn, frame, have);  // the end of the speech
                }
                post(EVENT_STREAMED, turn, 0);
            }
            turn = s_stream_turn;
            have = 0;
            frames = 0;
            continue;
        }
        if (turn < 0) {
            continue;
        }
        have += n;
        if (have + AUDIO_FRAME_SAMPLES > LINK_AUDIO_SAMPLES) {  // the next read would not fit
            link_send_audio(turn, frame, have);
            have = 0;
            if (++frames == MAX_TURN_SECONDS * FRAMES_PER_SECOND) {
                post(EVENT_TURN_FULL, turn, 0);
            }
        }
    }
}

static void show_setup_hint(void)
{
    const char *const lines[] = {"NOT SET UP", "", "HOLD BUTTON 5 S", "TO START SETUP"};
    face_show_text(lines, 4);
}

static void show_halted(int code)
{
    const char *reason = code == LINK_CLOSE_BAD_TOKEN     ? "BAD TOKEN"
                         : code == LINK_CLOSE_BAD_VERSION ? "UPDATE FIRMWARE"
                                                          : "OTHER DEVICE";  // LINK_CLOSE_REPLACED
    const char *const lines[] = {"SERVER REFUSED", "", reason, "", "HOLD 5 S FOR SETUP"};
    face_show_text(lines, 5);
}

static app_state_t enter(app_state_t state)
{
    if (state != STATE_LISTENING) {
        s_stream_turn = -1;
    }
    switch (state) {
        case STATE_SETUP: show_setup_hint(); break;
        case STATE_PROVISIONING: provision_start(); break;  // shows its own screen
        case STATE_OFFLINE: face_show(FACE_OFFLINE); break;
        case STATE_HALTED: break;  // show_halted() shows the reason
        case STATE_IDLE: face_show(FACE_IDLE); break;
        case STATE_LISTENING: face_show(FACE_LISTENING); break;
        case STATE_THINKING: face_show(FACE_THINKING); break;
        case STATE_SPEAKING: face_show(FACE_SPEAKING); break;
        case STATE_CONFIRM: face_show(FACE_CONFIRM); break;
    }
    return state;
}

static bool is_session(app_state_t state)
{
    return state >= STATE_IDLE;
}

static void log_heap(void)
{
    ESP_LOGI(TAG, "free heap %u, largest block %u, minimum ever %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT));
}

// docs/delivery-plan.md asks for button -> silence <= 300 ms. Logs the time from the
// press event to the first silent DMA buffer. The 30-40 ms button debounce comes on top.
static int64_t s_press_at;

static void log_barge_in(void *arg)
{
    (void)arg;
    int64_t silent_at = audio_silent_at();
    if (silent_at == 0) {
        ESP_LOGW(TAG, "barge-in: still playing after 500 ms");
    } else {
        ESP_LOGI(TAG, "barge-in: press to silence %d ms (+ debounce)",
                 (int)((silent_at - s_press_at) / 1000));
    }
}

static void measure_barge_in(void)
{
    static esp_timer_handle_t timer;
    if (timer == NULL) {
        const esp_timer_create_args_t args = {.callback = log_barge_in, .name = "barge_in"};
        ESP_ERROR_CHECK(esp_timer_create(&args, &timer));
    }
    s_press_at = esp_timer_get_time();
    audio_watch_silence();
    esp_timer_stop(timer);  // a second press inside 500 ms restarts the measurement
    esp_timer_start_once(timer, 500 * 1000);
}

// The turn ends without a reply: back to idle with a local beep (docs/protocol.md).
static app_state_t turn_failed(void)
{
    player_flush();
    s_turn = -1;
    s_stop_pending = false;
    app_state_t state = enter(STATE_IDLE);
    audio_tone(330, 200);
    return state;
}

// The speech is complete (a press or the 30 s cap).
static app_state_t end_listening(void)
{
    if (s_turn < 0) {
        return enter(STATE_IDLE);  // no turn.start came: cancel the request
    }
    s_stop_pending = true;  // listen.stop follows EVENT_STREAMED
    return enter(STATE_THINKING);
}

static app_state_t on_press(app_state_t state)
{
    switch (state) {
        case STATE_CONFIRM:
            s_turn = -1;  // the answer is a new turn: the server ends the wait at listen.start
            // fall through
        case STATE_IDLE:
            link_send_listen_start();
            return enter(STATE_LISTENING);
        case STATE_LISTENING:
            return end_listening();
        case STATE_THINKING:
        case STATE_SPEAKING:
            // Barge-in: silence first, without a wait for the server, then a new turn.
            measure_barge_in();
            player_flush();
            link_send_abort(s_turn);
            s_turn = -1;
            s_stop_pending = false;
            link_send_listen_start();
            return enter(STATE_LISTENING);
        default:
            return state;
    }
}

// The server sends confirm after reply.end, so the question can still play. Show the
// confirm face when the reply has drained.
static app_state_t on_confirm(app_state_t state, int turn)
{
    if (state == STATE_SPEAKING && turn == s_turn) {
        s_confirm_turn = turn;
        return state;
    }
    if (state == STATE_IDLE && turn == s_done_turn) {
        s_turn = turn;  // error confirm_timeout comes with this turn
        return enter(STATE_CONFIRM);
    }
    return state;
}

static app_state_t on_link_event(app_state_t state, link_event_t event, int arg)
{
    switch (event) {
        case LINK_UP:
            if (arg >= 0) {
                audio_set_volume(arg);
            }
            log_heap();
            return state == STATE_OFFLINE ? enter(STATE_IDLE) : state;
        case LINK_DOWN:
            player_flush();
            s_turn = -1;
            s_stop_pending = false;
            return is_session(state) ? enter(STATE_OFFLINE) : state;
        case LINK_STOPPED:
            link_halt();
            player_flush();
            s_turn = -1;
            show_halted(arg);
            return enter(STATE_HALTED);
        case LINK_VOLUME:
            audio_set_volume(arg);
            return state;
        case LINK_TURN_START:
            if (state != STATE_LISTENING || s_turn >= 0) {
                link_send_abort(arg);  // the press was cancelled: close the turn on the server
                return state;
            }
            s_turn = arg;
            player_begin(arg);
            s_stream_turn = arg;
            return state;
        default:
            break;
    }
    if (event == LINK_CONFIRM) {
        return on_confirm(state, arg);
    }
    if (event == LINK_REFUSED) {
        return (state == STATE_LISTENING && s_turn < 0) ? turn_failed() : state;
    }
    if (s_turn < 0 || arg != s_turn || !is_session(state)) {
        return state;  // a message of an old turn (after a barge-in)
    }
    switch (event) {
        case LINK_THINKING:
            return state == STATE_LISTENING ? enter(STATE_THINKING) : state;
        case LINK_REPLY_START:
            return enter(STATE_SPEAKING);
        case LINK_REPLY_END:
            player_end();  // EVENT_DRAINED follows when the buffer is empty
            return state;
        case LINK_ERROR:
            return turn_failed();
        default:
            return state;
    }
}

static app_state_t on_event(app_state_t state, const app_event_t *e)
{
    if (state == STATE_PROVISIONING) {
        return state;  // setup ends with a reboot
    }
    switch (e->kind) {
        case EVENT_HOLD:
            // Setup ends with a reboot: stop the session for good, so the link does not
            // fight the access point with reconnects.
            player_flush();
            if (s_link_started) {
                link_halt();
            }
            return enter(STATE_PROVISIONING);
        case EVENT_ONLINE:
            if (!s_link_started) {
                s_link_started = true;
                link_start(s_config.url, s_config.token, on_link);
            }
            return state;
        case EVENT_OFFLINE:
            return is_session(state) ? enter(STATE_OFFLINE) : state;
        case EVENT_LINK:
            return on_link_event(state, (link_event_t)e->value, e->arg);
        case EVENT_DRAINED:
            if (state == STATE_SPEAKING) {
                if (s_confirm_turn == s_turn) {
                    s_confirm_turn = -1;
                    return enter(STATE_CONFIRM);  // keep s_turn for error confirm_timeout
                }
                s_done_turn = s_turn;
                s_turn = -1;
                return enter(STATE_IDLE);
            }
            return state;
        case EVENT_STREAMED:
            if (s_stop_pending && e->value == s_turn) {
                s_stop_pending = false;
                link_send_listen_stop(s_turn);
            }
            return state;
        case EVENT_TURN_FULL:
            return (state == STATE_LISTENING && e->value == s_turn) ? end_listening() : state;
        case EVENT_PRESS:
            return on_press(state);
    }
    return state;
}

void app_main(void)
{
    const gpio_config_t led = {.pin_bit_mask = 1ULL << PIN_LED, .mode = GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&led));
    gpio_set_level(PIN_LED, 1);

    s_events = xQueueCreate(32, sizeof(app_event_t));
    config_init();
    face_init();
    audio_init();
    net_init(on_net);
    button_start(on_button);
    player_start(link_send_credit, on_drained);
    audio_tone(880, 150);  // boot beep
    vTaskDelay(pdMS_TO_TICKS(200));  // let the beep fade, so the check hears the room
    xTaskCreate(mic_task, "mic", 4096, NULL, 5, NULL);

    config_load(&s_config);
    app_state_t state;
    if (s_config.provisioned) {
        state = enter(STATE_OFFLINE);
        net_connect(s_config.ssid, s_config.wifi_pass);
    } else {
        state = enter(STATE_SETUP);
    }
    ESP_LOGI(TAG, "started, %s", s_config.provisioned ? "provisioned" : "not set up");

    for (;;) {
        app_event_t event;
        xQueueReceive(s_events, &event, portMAX_DELAY);
        state = on_event(state, &event);
    }
}
