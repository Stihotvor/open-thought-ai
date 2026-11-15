#include "link.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "net.h"
#include "player.h"

static const char *TAG = "link";

#define PROTO_VERSION 1
#define BACKOFF_MIN_MS 1000
#define BACKOFF_MAX_MS 60000
// A send that times out closes the connection (esp_websocket_client), and the weak
// SuperMini radio (2 dBm) can stall for a while: allow 2 s.
#define SEND_TIMEOUT pdMS_TO_TICKS(2000)
#define MAX_TEXT 1024    // docs/protocol.md: text frames are at most 1 KB
#define MAX_NESTING 4    // our messages are flat: deeper JSON is refused before parsing
#define TX_DEPTH 8       // queued text messages
#define TX_SIZE 128      // the longest message we send (tool.result) fits

// Close codes after which the device must not reconnect (docs/protocol.md).
#define CLOSE_REPLACED 4000
#define CLOSE_BAD_TOKEN 4401
#define CLOSE_BAD_VERSION 4426

static esp_websocket_client_handle_t s_client;
static link_handler_t s_handler;
static QueueHandle_t s_tx;  // text messages: the send task owns the blocking network write

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

static void tx_task(void *arg)
{
    (void)arg;
    char msg[TX_SIZE];
    for (;;) {
        xQueueReceive(s_tx, msg, portMAX_DELAY);
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
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 || item->valuedouble > 255) {
        return -1;
    }
    return item->valueint;
}

static int volume_field(const cJSON *msg)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(msg, "volume");
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 || item->valuedouble > 100) {
        return -1;
    }
    return item->valueint;
}

// True when the JSON nests no deeper than MAX_NESTING (cJSON parses by recursion).
static bool shallow(const char *data, int len)
{
    int depth = 0;
    for (int i = 0; i < len; i++) {
        if (data[i] == '{' || data[i] == '[') {
            if (++depth > MAX_NESTING) {
                return false;
            }
        } else if (data[i] == '}' || data[i] == ']') {
            depth--;
        }
    }
    return true;  // brackets inside strings only make the count stricter
}

// Answer a tool call at once: device tools come in M4, and the server waits 2 s.
static void refuse_tool(const cJSON *msg)
{
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(msg, "id");
    if (!cJSON_IsString(id) || strlen(id->valuestring) > 48) {
        return;
    }
    for (const char *c = id->valuestring; *c; c++) {
        if (*c < 0x20 || *c == '"' || *c == '\\') {
            return;  // would need a JSON escape
        }
    }
    char json[TX_SIZE];
    snprintf(json, sizeof(json),
             "{\"type\":\"tool.result\",\"id\":\"%s\",\"ok\":false,\"error\":\"unsupported\"}",
             id->valuestring);
    send_json(json);
}

static void on_text(const char *data, int len)
{
    if (!shallow(data, len)) {
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

static void on_audio(const esp_websocket_event_data_t *d)
{
    const uint8_t *bytes = (const uint8_t *)d->data_ptr;
    int len = d->data_len;
    if (d->payload_offset == 0 && len > 0) {
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
            if (code == CLOSE_REPLACED || code == CLOSE_BAD_TOKEN || code == CLOSE_BAD_VERSION) {
                s_stopping = true;
                s_handler(LINK_STOPPED, code);
            }
            return;
        }
        case WS_TRANSPORT_OPCODES_BINARY:
            s_in_binary = true;
            on_audio(d);
            return;
        case WS_TRANSPORT_OPCODES_CONT:
            if (s_in_binary) {
                on_audio(d);  // the rest of an audio message
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
    configASSERT(s_tx);
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
    char json[48];
    snprintf(json, sizeof(json), "{\"type\":\"audio.credit\",\"bytes\":%d}", bytes);
    send_json(json);
}

void link_send_audio(int turn, const int16_t *pcm, size_t samples)
{
    static uint8_t frame[1 + LINK_AUDIO_SAMPLES * 2];  // one caller: the mic task
    if (samples > LINK_AUDIO_SAMPLES || !esp_websocket_client_is_connected(s_client)) {
        return;
    }
    frame[0] = (uint8_t)turn;
    memcpy(frame + 1, pcm, samples * 2);  // the C3 is little-endian, as the protocol
    esp_websocket_client_send_bin(s_client, (const char *)frame, (int)(1 + samples * 2),
                                  SEND_TIMEOUT);
}
