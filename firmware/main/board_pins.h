// All pin numbers in one file (docs/hardware.md).
//
// Development wiring of the current board. GPIO2 and GPIO8 are strapping pins: keep the
// microphone and amplifier idle at boot. The board is rewired to non-strapping pins before
// the release (docs/hardware.md, M0.5 review).
#pragma once

#include "driver/gpio.h"

// One full-duplex I2S port: the INMP441 microphone and the MAX98357A amplifier share
// BCLK and WS.
#define PIN_I2S_BCLK GPIO_NUM_2
#define PIN_I2S_WS GPIO_NUM_1
#define PIN_I2S_DOUT GPIO_NUM_3  // to the amplifier DIN
#define PIN_I2S_DIN GPIO_NUM_8   // from the microphone SD

// SSD1306 0.96" OLED on I2C.
#define PIN_OLED_SDA GPIO_NUM_21
#define PIN_OLED_SCL GPIO_NUM_20
#define OLED_I2C_ADDR 0x3C

// Push button to GND, internal pull-up.
#define PIN_BUTTON GPIO_NUM_10

// Status LED, high = on.
#define PIN_LED GPIO_NUM_0
