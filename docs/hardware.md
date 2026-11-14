# Hardware

Status: draft. The pins below are the current development wiring (owner decision, 2026-11-14: use as is for development, rewire at the end). The EDA review (milestone M0.5) must approve this page before the pins are frozen.

## Parts (BOM)
| Part | Qty | Note |
|---|---|---|
| ESP32-C3 SuperMini | 1 | No PSRAM, 400 KB SRAM, 4 MB flash |
| INMP441 I2S microphone | 1 | 3.3 V |
| MAX98357A I2S amplifier | 1 | 5 V supply recommended |
| Speaker 4–8 Ω, 2–3 W | 1 | |
| SSD1306 0.96" OLED, I2C | 1 | 3.3 V |
| Push button | 1 | To GND, internal pull-up |

## Pin table (proposed, to review)
All pins are defined in `firmware/main/board_pins.h`.
| Signal | GPIO | Review note |
|---|---|---|
| I2S BCLK (mic + amp) | 2 | Strapping pin: dev wiring only, rewire before release |
| I2S WS (mic + amp) | 1 | |
| I2S DIN (mic SD) | 8 | Strapping pin: dev wiring only, rewire before release |
| I2S DOUT (amp DIN) | 3 | |
| I2C SDA / SCL (OLED, 0x3C) | 21 / 20 | |
| Button | 10 | To GND, internal pull-up |
| Status LED | 0 | High = on |
| USB D-/D+ | GPIO18/19 | Keep free for USB flashing and logs |

## Review checklist for the EDA agent
- Strapping pins and USB pins not used for signals.
- Decoupling: 100 nF at each module supply pin, bulk capacitor (≥ 100 µF) at the amplifier supply.
- Amplifier on 5 V, common ground, short speaker leads. Check brown-out on loud audio.
- I2S mic and amp share BCLK/WS only if the firmware uses one full-duplex I2S port.
- OLED I2C pull-ups present (module usually has them).
