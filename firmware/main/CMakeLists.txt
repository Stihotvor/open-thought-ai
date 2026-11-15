idf_component_register(
    SRCS "main.c" "audio.c" "button.c" "config.c" "face.c" "net.c" "provision.c" "validate.c" "link.c" "player.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_gpio esp_driver_i2c esp_driver_i2s esp_lcd esp_wifi esp_netif
                  esp_event esp_timer esp_http_server nvs_flash esp_hw_support json esp_app_format
)
