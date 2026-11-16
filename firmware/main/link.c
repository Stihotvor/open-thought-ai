#include "link.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/message_buffer.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "net.h"
#include "player.h"
#include "proto.h"

static const char *TAG = "link";

#define PROTO_VERSION 1
#define BACKOFF_MIN_MS 1000
#define BACKOFF_MAX_MS 60000
// A send that times out closes the connection (esp_websocket_client), and the weak
// SuperMini radio (2 dBm) can stall for a while: allow 2 s.
#define SEND_TIMEOUT pdMS_TO_TICKS(2000)
#define MAX_TEXT 1024    // docs/protocol.md: text frames are at most 1 KB
#define TX_DEPTH 8       // queued text messages
#define TX_SIZE 128      // the longest message we send (tool.result) fits
#define CREDIT_PERIOD_MS 20  // pending credit goes out when the queue is idle this long
#define AUDIO_FRAME_BYTES (1 + LINK_AUDIO_SAMPLES * 2)  // [turn][PCM]
// Mic audio waits here for the send task, so a slow send never stalls the mic (lost
// samples). 1 s of audio, plus the 4-byte length of each frame.
#define AUDIO_BUFFER_BYTES (10 * (AUDIO_FRAME_BYTES + 4))
#define SLOW_SEND_MS 100     // a send slower than one audio frame is logged


static esp_websocket_client_handle_t s_client;
static link_handler_t s_handler;
static QueueHandle_t s_tx;  // control messages: the send task owns the blocking network write
static MessageBufferHandle_t s_audio;  // mic task writes, send task reads
static portMUX_TYPE s_credit_lock = portMUX_INITIALIZER_UNLOCKED;
static int s_credit_pending;  // audio credit not yet sent (under s_credit_lock)

// Link task only.
static int s_backoff_ms = BACKOFF_MIN_MS;
static bool s_up;          // hello received on this connection
static bool s_stopping;    // a no-reconnect close code arrived
static bool s_in_binary;   // the message in progress is audio
static int s_audio_turn;   // its turn id

// Queue a text message. Never blocks: callers include the player and the link task.
static void send_json(const char *json)
{
    char msg[TX_SIZE];
    if (strlen(json) >= sizeof(msg)) {
        ESP_LOGE(TAG, "message too long for the send queue");
        return;
    }
    strcpy(msg, json);
    if (xQueueSend(s_tx, msg, 0) != pdTRUE) {
        ESP_LOGW(TAG, "send queue full: message dropped");
    }
}

// Send all queued mic audio. It goes before any control message queued after it, so
// listen.stop never overtakes the end of the speech.
static void send_queued_audio(void)
{
    static uint8_t frame[AUDIO_FRAME_BYTES];  // send task only
    size_t n;
    while ((n = xMessageBufferReceive(s_audio, frame, sizeof(frame), 0)) > 0) {
        if (!esp_websocket_client_is_connected(s_client)) {
            continue;  // drop it: the server cancels the turn on disconnect
        }
        TickType_t start = xTaskGetTickCount();
        if (esp_websocket_client_send_bin(s_client, (const char *)frame, (int)n, SEND_TIMEOUT) < 0) {
            ESP_LOGW(TAG, "audio send failed");
        }
        int ms = (int)pdTICKS_TO_MS(xTaskGetTickCount() - start);
        if (ms > SLOW_SEND_MS) {
            ESP_LOGW(TAG, "slow audio send: %d ms", ms);
        }
    }
}

static void tx_task(void *arg)
{
    (void)arg;
    char msg[TX_SIZE];
    for (;;) {
        bool have_msg = xQueueReceive(s_tx, msg, pdMS_TO_TICKS(CREDIT_PERIOD_MS)) == pdTRUE;
        send_queued_audio();
        if (!have_msg) {
            taskENTER_CRITICAL(&s_credit_lock);
            int credit = s_credit_pending;
            s_credit_pending = 0;
            taskEXIT_CRITICAL(&s_credit_lock);
            if (credit == 0) {
                continue;
            }
            snprintf(msg, sizeof(msg), "{\"type\":\"audio.credit\",\"bytes\":%d}", credit);
        }
        if (!esp_websocket_client_is_connected(s_client)) {
            continue;  // the turn is lost anyway: the server cancels it on disconnect
        }
        if (esp_websocket_client_send_text(s_client, msg, (int)strlen(msg), SEND_TIMEOUT) < 0) {
            ESP_LOGW(TAG, "send failed");
        }
    }
}

static void send_hello(void)
{
    char device_id[9];
    net_ap_ssid(device_id);  // OTA-XXXX: from the MAC address, stable per board
    char json[TX_SIZE];
    snprintf(json, sizeof(json), "{\"type\":\"hello\",\"proto\":%d,\"fw\":\"%.32s\",\"device_id\":\"%s\"}",
             PROTO_VERSION, esp_app_get_description()->version, device_id);
    send_json(json);
}

// A turn id 0..255, or -1 when the field is missing or not a valid turn.
static int turn_field(const cJSON *msg)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(msg, "turn");
    return cJSON_IsNumber(item) ? proto_turn(item->valuedouble) : -1;
}

// A volume 0..100, or -1 when the field is missing or not valid.
static int volume_field(const cJSON *msg)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(msg, "volume");
    return cJSON_IsNumber(item) ? proto_volume(item->valuedouble) : -1;
}

static bool is_code(const cJSON *msg, const char *code)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(msg, "code");
    return cJSON_IsString(item) && strcmp(item->valuestring, code) == 0;
}

// Answer a tool call at once: device tools come in M4, and the server waits 2 s.
static void refuse_tool(const cJSON *msg)
{
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(msg, "id");
    if (!cJSON_IsString(id) || !proto_safe_id(id->valuestring)) {
        return;
    }
    char json[TX_SIZE];
    snprintf(json, sizeof(json),
             "{\"type\":\"tool.result\",\"id\":\"%s\",\"ok\":false,\"error\":\"unsupported\"}",
             id->valuestring);
    send_json(json);
}

static void on_text(const char *data, int len)
{
    if (!proto_shallow(data, len)) {
        ESP_LOGW(TAG, "message nests too deep");
        return;
    }
    cJSON *msg = cJSON_ParseWithLength(data, (size_t)len);
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(msg, "type");
    if (!cJSON_IsString(type)) {
        ESP_LOGW(TAG, "message without a type");
        cJSON_Delete(msg);
        return;
    }
    const char *t = type->valuestring;
    int turn = turn_field(msg);
    if (strcmp(t, "hello") == 0) {
        s_up = true;
        s_backoff_ms = BACKOFF_MIN_MS;
        s_handler(LINK_UP, volume_field(msg));
    } else if (strcmp(t, "settings.update") == 0) {
        int volume = volume_field(msg);
        if (volume >= 0) {
            s_handler(LINK_VOLUME, volume);
        }
    } else if (strcmp(t, "tool.call") == 0) {
        refuse_tool(msg);
    } else if (strcmp(t, "error") == 0 && is_code(msg, "too_many_turns")) {
        ESP_LOGW(TAG, "listen.start refused: too_many_turns");  // its turn is the old one
        s_handler(LINK_REFUSED, 0);
    } else if (turn < 0) {
        ESP_LOGW(TAG, "%.32s without a valid turn", t);  // all other messages need one
    } else if (strcmp(t, "turn.start") == 0) {
        s_handler(LINK_TURN_START, turn);
    } else if (strcmp(t, "state") == 0) {
        const cJSON *state = cJSON_GetObjectItemCaseSensitive(msg, "state");
        if (cJSON_IsString(state) && strcmp(state->valuestring, "thinking") == 0) {
            s_handler(LINK_THINKING, turn);
        }
    } else if (strcmp(t, "reply.start") == 0) {
        s_handler(LINK_REPLY_START, turn);
    } else if (strcmp(t, "reply.end") == 0) {
        s_handler(LINK_REPLY_END, turn);
    } else if (strcmp(t, "error") == 0) {
        const cJSON *code = cJSON_GetObjectItemCaseSensitive(msg, "code");
        ESP_LOGW(TAG, "turn %d error: %.32s", turn, cJSON_IsString(code) ? code->valuestring : "?");
        s_handler(LINK_ERROR, turn);
    } else {
        ESP_LOGI(TAG, "ignored message type %.32s", t);
    }
    cJSON_Delete(msg);
}

// `first`: the data starts a new audio message, so byte 0 is the turn id.
static void on_audio(const esp_websocket_event_data_t *d, bool first)
{
    const uint8_t *bytes = (const uint8_t *)d->data_ptr;
    int len = d->data_len;
    if (first && len > 0) {
        s_audio_turn = bytes[0];  // byte 0 of an audio message is the turn id
        bytes++;
        len--;
    }
    if (len > 0) {
        player_push(s_audio_turn, bytes, (size_t)len);
    }
}

static void on_data(const esp_websocket_event_data_t *d)
{
    switch (d->op_code) {
        case WS_TRANSPORT_OPCODES_CLOSE: {
            int code = d->close_status_code;
            ESP_LOGW(TAG, "server closed the link: %d", code);
            if (code == LINK_CLOSE_REPLACED || code == LINK_CLOSE_BAD_TOKEN ||
                code == LINK_CLOSE_BAD_VERSION) {
                s_stopping = true;
                s_handler(LINK_STOPPED, code);
            }
            return;
        }
        case WS_TRANSPORT_OPCODES_BINARY:
            s_in_binary = true;
            on_audio(d, d->payload_offset == 0);  // a large frame comes in parts
            return;
        case WS_TRANSPORT_OPCODES_CONT:
            if (s_in_binary) {
                on_audio(d, false);  // the rest of an audio message: no turn byte
            }
            return;
        case WS_TRANSPORT_OPCODES_TEXT:
            s_in_binary = false;
            if (d->payload_len > MAX_TEXT || d->data_len != d->payload_len) {
                ESP_LOGW(TAG, "text message too large: %d bytes", d->payload_len);
                return;
            }
            on_text(d->data_ptr, d->data_len);
            return;
        default:
            return;  // ping and pong: the client answers them
    }
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *event_data)
{
    (void)arg;
    (void)base;
    const esp_websocket_event_data_t *d = event_data;
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "connected");
            s_up = false;
            s_in_binary = false;
            xQueueReset(s_tx);  // messages of the old connection mean nothing now
            taskENTER_CRITICAL(&s_credit_lock);
            s_credit_pending = 0;
            taskEXIT_CRITICAL(&s_credit_lock);
            send_hello();
            break;
        case WEBSOCKET_EVENT_DATA:
            on_data(d);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
        case WEBSOCKET_EVENT_CLOSED:
            if (s_up) {
                s_up = false;
                s_handler(LINK_DOWN, 0);
            }
            if (!s_stopping) {
                ESP_LOGI(TAG, "reconnect in %d ms", s_backoff_ms);
                esp_websocket_client_set_reconnect_timeout(s_client, s_backoff_ms);
                s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
            }
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGW(TAG, "error, upgrade status %d", d->error_handle.esp_ws_handshake_status_code);
            break;
        default:
            break;
    }
}

void link_start(const char *url, const char *token, link_handler_t handler)
{
    s_handler = handler;
    s_tx = xQueueCreate(TX_DEPTH, TX_SIZE);
    s_audio = xMessageBufferCreate(AUDIO_BUFFER_BYTES);
    configASSERT(s_tx && s_audio);
    xTaskCreate(tx_task, "link_tx", 4096, NULL, 5, NULL);

    static char headers[160];  // the client keeps a pointer: static
    snprintf(headers, sizeof(headers), "Authorization: Bearer %s\r\n", token);
    const esp_websocket_client_config_t config = {
        .uri = url,  // copied by the client
        .headers = headers,
        .buffer_size = 4096,             // one 3201-byte audio frame in one event
        .task_stack = 6144,
        .reconnect_timeout_ms = BACKOFF_MIN_MS,
        .network_timeout_ms = 5000,      // the server allows 5 s for the upgrade
        .ping_interval_sec = 15,         // docs/protocol.md: ping every 15 s,
        .pingpong_timeout_sec = 45,      // close after 3 missed pongs
        .enable_close_reconnect = true,  // a server restart closes cleanly: come back
    };
    s_client = esp_websocket_client_init(&config);
    configASSERT(s_client);
    esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_event, NULL);
    ESP_ERROR_CHECK(esp_websocket_client_start(s_client));
}

void link_halt(void)
{
    esp_websocket_client_stop(s_client);
}

void link_send_listen_start(void)
{
    send_json("{\"type\":\"listen.start\"}");
}

void link_send_listen_stop(int turn)
{
    char json[48];
    snprintf(json, sizeof(json), "{\"type\":\"listen.stop\",\"turn\":%d}", turn);
    send_json(json);
}

void link_send_abort(int turn)
{
    char json[48];
    snprintf(json, sizeof(json), "{\"type\":\"abort\",\"turn\":%d}", turn);
    send_json(json);
}

void link_send_credit(int bytes)
{
    taskENTER_CRITICAL(&s_credit_lock);
    s_credit_pending += bytes;
    taskEXIT_CRITICAL(&s_credit_lock);
}

void link_send_audio(int turn, const int16_t *pcm, size_t samples)
{
    static uint8_t frame[AUDIO_FRAME_BYTES];  // one caller: the mic task
    if (samples > LINK_AUDIO_SAMPLES || !esp_websocket_client_is_connected(s_client)) {
        return;
    }
    frame[0] = (uint8_t)turn;
    memcpy(frame + 1, pcm, samples * 2);  // the C3 is little-endian, as the protocol
    if (xMessageBufferSend(s_audio, frame, 1 + samples * 2, 0) == 0) {
        ESP_LOGW(TAG, "audio buffer full: frame dropped");  // the link is too slow
    }
}
