#include "net.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"

static const char *TAG = "net";

#define BACKOFF_MIN_MS 1000
#define BACKOFF_MAX_MS 60000

static net_handler_t s_handler;
static esp_timer_handle_t s_retry_timer;
static int s_backoff_ms = BACKOFF_MIN_MS;
static bool s_station = false;  // reconnect only while in station mode

static void retry(void *arg)
{
    (void)arg;
    esp_wifi_connect();
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_handler(false);
        if (s_station) {
            ESP_LOGI(TAG, "disconnected, retry in %d ms", s_backoff_ms);
            esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_ms * 1000);
            s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_backoff_ms = BACKOFF_MIN_MS;
        ESP_LOGI(TAG, "online");
        s_handler(true);
    }
}

void net_init(net_handler_t handler)
{
    s_handler = handler;
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));  // our config module owns NVS
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    const esp_timer_create_args_t timer = {.callback = retry, .name = "wifi_retry"};
    ESP_ERROR_CHECK(esp_timer_create(&timer, &s_retry_timer));
    // Start the radio at once, idle: esp_random() is a true RNG only while it runs.
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void net_connect(const char *ssid, const char *password)
{
    wifi_config_t config = {0};
    strlcpy((char *)config.sta.ssid, ssid, sizeof(config.sta.ssid));
    strlcpy((char *)config.sta.password, password, sizeof(config.sta.password));
    s_station = true;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_ERROR_CHECK(esp_wifi_connect());
}

void net_start_ap(const char *ssid, const char *password)
{
    s_station = false;
    esp_timer_stop(s_retry_timer);
    esp_wifi_stop();
    wifi_config_t config = {
        .ap = {
            .channel = 1,
            .max_connection = 1,
            .authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    strlcpy((char *)config.ap.ssid, ssid, sizeof(config.ap.ssid));
    config.ap.ssid_len = strlen(ssid);
    strlcpy((char *)config.ap.password, password, sizeof(config.ap.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void net_ap_ssid(char out[9])
{
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP));
    snprintf(out, 9, "OTA-%02X%02X", mac[4], mac[5]);
}
