// Debounced push button: short press and long hold events.
#pragma once

typedef enum {
    BUTTON_PRESS,  // released before the hold time
    BUTTON_HOLD,   // held down for BUTTON_HOLD_MS (sent once, while still down)
} button_event_t;

#define BUTTON_HOLD_MS 5000

typedef void (*button_handler_t)(button_event_t event);

// Start the polling task. `handler` runs in that task: keep it short.
void button_start(button_handler_t handler);
