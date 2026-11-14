#include "button.h"

#include <stdbool.h>

#include "board_pins.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define POLL_MS 10
#define STABLE_POLLS 3  // 30 ms of the same level counts as a change

static button_handler_t s_handler;

static void button_task(void *arg)
{
    (void)arg;
    bool down = false;     // debounced state
    int same = 0;          // polls that disagree with the debounced state
    int held_ms = 0;
    bool hold_sent = false;

    for (;;) {
        bool raw_down = gpio_get_level(PIN_BUTTON) == 0;  // to GND when pressed
        same = (raw_down != down) ? same + 1 : 0;
        if (same >= STABLE_POLLS) {
            down = raw_down;
            same = 0;
            if (down) {
                held_ms = 0;
                hold_sent = false;
            } else if (!hold_sent) {
                s_handler(BUTTON_PRESS);
            }
        }
        if (down && !hold_sent) {
            held_ms += POLL_MS;
            if (held_ms >= BUTTON_HOLD_MS) {
                hold_sent = true;
                s_handler(BUTTON_HOLD);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
}

void button_start(button_handler_t handler)
{
    s_handler = handler;
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << PIN_BUTTON,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&config));
    xTaskCreate(button_task, "button", 3072, NULL, 5, NULL);
}
