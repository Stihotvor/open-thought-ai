#include "config.h"

#include <string.h>

#include "esp_random.h"
#include "nvs.h"
#include "nvs_flash.h"

#define NAMESPACE "otacfg"
// No look-alike characters (0/O, 1/I/L), so the password is easy to type from the OLED.
#define AP_ALPHABET "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

void config_init(void)
{
#if CONFIG_NVS_ENCRYPTION
    // Release gate (docs/security.md): needs an eFuse key, never burned in M1.
    esp_err_t err = nvs_flash_secure_init(NULL);
#else
    esp_err_t err = nvs_flash_init();
#endif
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

static void get_str(nvs_handle_t nvs, const char *key, char *out, size_t size)
{
    out[0] = '\0';
    if (nvs_get_str(nvs, key, out, &size) != ESP_OK) {
        out[0] = '\0';
    }
}

void config_load(config_t *out)
{
    memset(out, 0, sizeof(*out));
    nvs_handle_t nvs;
    if (nvs_open(NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return;  // nothing saved yet
    }
    get_str(nvs, "ssid", out->ssid, sizeof(out->ssid));
    get_str(nvs, "wifi_pass", out->wifi_pass, sizeof(out->wifi_pass));
    get_str(nvs, "url", out->url, sizeof(out->url));
    get_str(nvs, "token", out->token, sizeof(out->token));
    uint8_t flag = 0;
    out->provisioned = nvs_get_u8(nvs, "provisioned", &flag) == ESP_OK && flag == 1;
    nvs_close(nvs);
}

void config_save(const config_t *in)
{
    nvs_handle_t nvs;
    ESP_ERROR_CHECK(nvs_open(NAMESPACE, NVS_READWRITE, &nvs));
    ESP_ERROR_CHECK(nvs_set_u8(nvs, "provisioned", 0));
    ESP_ERROR_CHECK(nvs_commit(nvs));
    ESP_ERROR_CHECK(nvs_set_str(nvs, "ssid", in->ssid));
    ESP_ERROR_CHECK(nvs_set_str(nvs, "wifi_pass", in->wifi_pass));
    ESP_ERROR_CHECK(nvs_set_str(nvs, "url", in->url));
    if (in->token[0] != '\0') {
        ESP_ERROR_CHECK(nvs_set_str(nvs, "token", in->token));
    }
    ESP_ERROR_CHECK(nvs_commit(nvs));
    ESP_ERROR_CHECK(nvs_set_u8(nvs, "provisioned", 1));
    ESP_ERROR_CHECK(nvs_commit(nvs));
    nvs_close(nvs);
}

void config_ap_password(char out[13])
{
    nvs_handle_t nvs;
    ESP_ERROR_CHECK(nvs_open(NAMESPACE, NVS_READWRITE, &nvs));
    size_t size = 13;
    if (nvs_get_str(nvs, "ap_pass", out, &size) != ESP_OK || strlen(out) != 12) {
        // esp_random is a true RNG while Wi-Fi or Bluetooth runs: call after Wi-Fi starts.
        for (int i = 0; i < 12; i++) {
            out[i] = AP_ALPHABET[esp_random() % (sizeof(AP_ALPHABET) - 1)];
        }
        out[12] = '\0';
        ESP_ERROR_CHECK(nvs_set_str(nvs, "ap_pass", out));
        ESP_ERROR_CHECK(nvs_commit(nvs));
    }
    nvs_close(nvs);
}
