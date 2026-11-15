#include "provision.h"

#include <ctype.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "face.h"
#include "net.h"
#include "validate.h"

static const char *TAG = "provision";

#define AP_MINUTES 5
#define MAX_PIN_FAILURES 5
#define MAX_BODY 1024
#define REBOOT_DELAY_MS 2000

static char s_pin[7];
static int s_failures;
static esp_timer_handle_t s_reboot_timer;

// ---- form parsing ----

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// Copy the url-decoded value of `key` from a form body into `out`. False when the key is
// missing, the value is too long, or the encoding is bad.
static bool form_value(const char *body, const char *key, char *out, size_t size)
{
    size_t key_len = strlen(key);
    for (const char *p = body; p && *p; p = strchr(p, '&') ? strchr(p, '&') + 1 : NULL) {
        if (strncmp(p, key, key_len) != 0 || p[key_len] != '=') {
            continue;
        }
        size_t n = 0;
        for (const char *v = p + key_len + 1; *v && *v != '&'; v++) {
            char c = *v;
            if (c == '+') {
                c = ' ';
            } else if (c == '%') {
                int hi = hex_value(v[1]);
                int lo = hi < 0 ? -1 : hex_value(v[2]);
                if (lo < 0) {
                    return false;
                }
                c = (char)(hi * 16 + lo);
                v += 2;
            }
            if (c == '\0' || n + 1 >= size) {
                return false;
            }
            out[n++] = c;
        }
        out[n] = '\0';
        return true;
    }
    return false;
}

static bool pin_matches(const char *pin)
{
    if (strlen(pin) != 6) {
        return false;
    }
    unsigned diff = 0;  // constant time: no early exit on the first wrong digit
    for (int i = 0; i < 6; i++) {
        diff |= (unsigned)(pin[i] ^ s_pin[i]);
    }
    return diff == 0;
}

// ---- HTTP ----

// HTML-escape `s` into one chunk (inputs are at most 128 characters, 6 bytes each escaped).
static void send_escaped(httpd_req_t *req, const char *s)
{
    char out[128 * 6 + 1];
    size_t n = 0;
    for (; *s && n + 7 < sizeof(out); s++) {
        const char *entity = NULL;
        switch (*s) {
            case '&': entity = "&amp;"; break;
            case '<': entity = "&lt;"; break;
            case '>': entity = "&gt;"; break;
            case '"': entity = "&quot;"; break;
            case '\'': entity = "&#39;"; break;
        }
        if (entity) {
            n += (size_t)snprintf(out + n, sizeof(out) - n, "%s", entity);
        } else {
            out[n++] = *s;
        }
    }
    out[n] = '\0';
    if (n > 0) {  // an empty chunk ends a chunked HTTP response: never send one
        httpd_resp_sendstr_chunk(req, out);
    }
}

// The form. Only the Wi-Fi name and the server URL are filled in again: the token and
// the passwords are never sent back.
static esp_err_t send_form(httpd_req_t *req, const char *message, const config_t *values)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_sendstr_chunk(req,
        "<!doctype html><meta charset=utf-8><meta name=viewport "
        "content='width=device-width'><title>open-thought-ai setup</title>"
        "<h1>Device setup</h1>");
    if (message) {
        httpd_resp_sendstr_chunk(req, "<p><b>");
        send_escaped(req, message);
        httpd_resp_sendstr_chunk(req, "</b></p>");
    }
    httpd_resp_sendstr_chunk(req,
        "<form method=post action=/save>"
        "<p>PIN (on the screen)<br><input name=pin inputmode=numeric maxlength=6 required>"
        "<p>Wi-Fi name<br><input name=ssid maxlength=32 required value=\"");
    send_escaped(req, values->ssid);
    httpd_resp_sendstr_chunk(req,
        "\"><p>Wi-Fi password<br><input name=wifi_pass type=password maxlength=64>"
        "<p>Server URL<br><input name=url maxlength=128 required "
        "placeholder='ws://192.168.1.10:8000/v1/device' value=\"");
    send_escaped(req, values->url);
    httpd_resp_sendstr_chunk(req,
        "\"><p>Device token (empty keeps the saved one)<br>"
        "<input name=token type=password maxlength=128>"
        "<p><button>Save</button></form>");
    return httpd_resp_sendstr_chunk(req, NULL);
}

// Before a correct PIN, the form shows no saved values (not even the Wi-Fi name or URL).
static const config_t EMPTY;

static esp_err_t get_form(httpd_req_t *req)
{
    return send_form(req, NULL, &EMPTY);
}

static void reboot(void *arg)
{
    (void)arg;
    esp_restart();
}

static void stop_after_failures(void)
{
    ESP_LOGW(TAG, "%d wrong PINs: setup stops", MAX_PIN_FAILURES);
    face_show(FACE_ERROR);
    esp_timer_stop(s_reboot_timer);
    esp_timer_start_once(s_reboot_timer, (uint64_t)REBOOT_DELAY_MS * 1000);
}

static esp_err_t post_save(httpd_req_t *req)
{
    char type[48] = "";
    httpd_req_get_hdr_value_str(req, "Content-Type", type, sizeof(type));
    if (strncmp(type, "application/x-www-form-urlencoded", 33) != 0 ||
        req->content_len > MAX_BODY || s_failures >= MAX_PIN_FAILURES) {
        return httpd_resp_send_404(req);
    }
    static char body[MAX_BODY + 1];  // one httpd task runs handlers one at a time
    size_t got = 0;
    while (got < req->content_len) {
        int n = httpd_req_recv(req, body + got, req->content_len - got);
        if (n <= 0) {
            return ESP_FAIL;
        }
        got += (size_t)n;
    }
    body[got] = '\0';

    static config_t in;  // static: keeps the httpd stack small
    memset(&in, 0, sizeof(in));
    char pin[8] = "";
    if (!form_value(body, "pin", pin, sizeof(pin)) || !pin_matches(pin)) {
        s_failures++;
        ESP_LOGW(TAG, "wrong PIN (%d of %d)", s_failures, MAX_PIN_FAILURES);
        if (s_failures >= MAX_PIN_FAILURES) {
            stop_after_failures();
        }
        return send_form(req, "Wrong PIN.", &EMPTY);
    }
    const char *error = NULL;
    if (!form_value(body, "ssid", in.ssid, sizeof(in.ssid))) {
        error = "Wi-Fi name is missing or too long.";
    } else if (!form_value(body, "wifi_pass", in.wifi_pass, sizeof(in.wifi_pass))) {
        error = "Wi-Fi password is missing or too long.";
    } else if (!form_value(body, "url", in.url, sizeof(in.url))) {
        error = "Server URL is missing or too long.";
    } else if (!form_value(body, "token", in.token, sizeof(in.token))) {
        error = "Device token is missing or too long.";
    }
    if (!error) error = validate_ssid(in.ssid);
    if (!error) error = validate_wifi_pass(in.wifi_pass);
    if (!error) error = validate_url(in.url);
    if (!error) error = validate_token(in.token);
    static config_t saved;
    config_load(&saved);
    if (!error && in.token[0] == '\0' && saved.token[0] == '\0') {
        error = "A device token is needed.";
    }
    if (error) {
        ESP_LOGW(TAG, "form rejected: %s (body %d bytes)", error, (int)got);
        in.wifi_pass[0] = in.token[0] = '\0';
        return send_form(req, error, &in);
    }
    config_save(&in);
    ESP_LOGI(TAG, "settings saved, rebooting");
    const char *const lines[] = {"SAVED", "", "RESTARTING"};
    face_show_text(lines, 3);
    httpd_resp_sendstr(req, "<!doctype html><p>Saved. The device restarts now.");
    esp_timer_stop(s_reboot_timer);
    esp_timer_start_once(s_reboot_timer, (uint64_t)REBOOT_DELAY_MS * 1000);
    return ESP_OK;
}

void provision_start(void)
{
    char ssid[9];
    char password[13];
    net_ap_ssid(ssid);
    config_new_ap_password(password);  // after net_init: esp_random needs the radio on
    snprintf(s_pin, sizeof(s_pin), "%06lu", (unsigned long)(esp_random() % 1000000));
    s_failures = 0;

    net_start_ap(ssid, password);

    const esp_timer_create_args_t timer = {.callback = reboot, .name = "setup_end"};
    ESP_ERROR_CHECK(esp_timer_create(&timer, &s_reboot_timer));
    ESP_ERROR_CHECK(esp_timer_start_once(s_reboot_timer, (uint64_t)AP_MINUTES * 60 * 1000000));

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_open_sockets = 2;
    config.stack_size = 6144;
    httpd_handle_t server;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    const httpd_uri_t form = {.uri = "/", .method = HTTP_GET, .handler = get_form};
    const httpd_uri_t save = {.uri = "/save", .method = HTTP_POST, .handler = post_save};
    httpd_register_uri_handler(server, &form);
    httpd_register_uri_handler(server, &save);

    // Scan to join: the standard Wi-Fi QR format. The SSID (OTA-XXXX) and the password
    // alphabet (config.c) have no characters that need escapes (; , : \\ ").
    char qr[64];
    snprintf(qr, sizeof(qr), "WIFI:T:WPA;S:%s;P:%s;;", ssid, password);
    char line_pin[FACE_QR_COLUMNS + 1], pass_1[FACE_QR_COLUMNS + 1], pass_2[FACE_QR_COLUMNS + 1];
    snprintf(line_pin, sizeof(line_pin), "PIN %s", s_pin);
    snprintf(pass_1, sizeof(pass_1), "%.6s", password);
    snprintf(pass_2, sizeof(pass_2), "%s", password + 6);
    const char *const lines[] = {line_pin, "OPEN", "192.168.4.1", "", "PASS", pass_1, pass_2};
    face_show_qr(qr, lines, 7);
    ESP_LOGI(TAG, "setup access point %s started for %d minutes", ssid, AP_MINUTES);
}
