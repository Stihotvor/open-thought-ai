#include "link.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "net.h"
#include "player.h"

static const char *TAG = "link";

#define PROTO_VERSION 1
#define BACKOFF_MIN_MS 1000
#define BACKOFF_MAX_MS 60000
#define SEND_TIMEOUT pdMS_TO_TICKS(200)
#define MAX_TEXT 1024           // docs/protocol.md: text frames are at most 1 KB

// Close codes after which the device must not reconnect (docs/protocol.md).
#define CLOSE_REPLACED 4000
#define CLOSE_BAD_TOKEN 4401
#define CLOSE_BAD_VERSION 4426

static esp_websocket_client_handle_t s_client;
static link_handler_t s_handler;
static int s_backoff_ms = BACKOFF_MIN_MS;
static bool s_up;          // hello received on this connection (link task only)
static bool s_stopping;    // a no-reconnect close code arrived (link task only)
static int s_audio_turn;   // turn of the binary message in progress (link task only)

static void send_json(const char *json)
{
    if (!esp_websocket_client_is_connected(s_client)) {
        return;  // the turn is lost anyway: the server cancels it on disconnect
    }
    if (esp_websocket_client_send_text(s_client, json, (int)strlen(json), SEND_TIMEOUT) < 0) {
        ESP_LOGW(TAG, "send failed: %s", json);
    }
}

static void send_hello(void)
{
    char device_id[9];
    net_ap_ssid(device_id);  // OTA-XXXX: from the MAC address, stable per board
    char json[128];
    snprintf(json, sizeof(json), "{\"type\":\"hello\",\"proto\":%d,\"fw\":\"%s\",\"device_id\":\"%s\"}",
             PROTO_VERSION, esp_app_get_description()->version, device_id);
    send_json(json);
}

static int int_field(const cJSON *msg, const char *name, int fallback)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(msg, name);
    return cJSON_IsNumber(item) ? item->valueint : fallback;
}

static void on_text(const char *data, int len)
{
    cJSON *msg = cJSON_ParseWithLength(data, (size_t)len);
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(msg, "type");
    if (!cJSON_IsString(type)) {
        ESP_LOGW(TAG, "message without a type");
        cJSON_Delete(msg);
        return;
    }
    const char *t = type->valuestring;
    int turn = int_field(msg, "turn", -1);
    if (strcmp(t, "hello") == 0) {
        s_up = true;
        s_backoff_ms = BACKOFF_MIN_MS;
        s_handler(LINK_UP, int_field(msg, "volume", -1));
    } else if (strcmp(t, "settings.update") == 0) {
        int volume = int_field(msg, "volume", -1);
        if (volume >= 0) {
            s_handler(LINK_VOLUME, volume);
        }
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
        ESP_LOGW(TAG, "turn %d error: %s", turn, cJSON_IsString(code) ? code->valuestring : "?");
        s_handler(LINK_ERROR, turn);
    } else if (strcmp(t, "tool.call") == 0) {
        // Device tools come in M4. Answer at once, so the server does not wait 2 s.
        const cJSON *id = cJSON_GetObjectItemCaseSensitive(msg, "id");
        if (cJSON_IsString(id) && strlen(id->valuestring) < 64 &&
            strpbrk(id->valuestring, "\"\\") == NULL) {  // no JSON escapes needed
            char json[160];
            snprintf(json, sizeof(json),
                     "{\"type\":\"tool.result\",\"id\":\"%s\",\"ok\":false,\"error\":\"unsupported\"}",
                     id->valuestring);
            send_json(json);
        }
    } else {
        ESP_LOGI(TAG, "ignored message type %s", t);
    }
    cJSON_Delete(msg);
}

static void on_data(const esp_websocket_event_data_t *d)
{
    if (d->op_code == WS_TRANSPORT_OPCODES_CLOSE || d->close_status_code != 0) {
        int code = d->close_status_code;
        ESP_LOGW(TAG, "server closed the link: %d", code);
        if (code == CLOSE_REPLACED || code == CLOSE_BAD_TOKEN || code == CLOSE_BAD_VERSION) {
            s_stopping = true;
            s_handler(LINK_STOPPED, code);
        }
        return;
    }
    if (d->op_code == WS_TRANSPORT_OPCODES_BINARY ||
        (d->op_code == WS_TRANSPORT_OPCODES_CONT && d->payload_offset > 0)) {
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
        return;
    }
    if (d->op_code == WS_TRANSPORT_OPCODES_TEXT) {
        if (d->payload_len > MAX_TEXT || d->data_len != d->payload_len) {
            ESP_LOGW(TAG, "text message too large: %d bytes", d->payload_len);
            return;
        }
        on_text(d->data_ptr, d->data_len);
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
