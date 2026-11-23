# Firmware

ESP-IDF v5.5 project for the ESP32-C3 SuperMini (docs/hardware.md).

M2 status: setup, Wi-Fi, and push-to-talk turns over the server link (protocol v1,
`docs/protocol.md`). With `OTA_PIPELINE=echo` the server plays your speech back.

## Build and flash
1. Load ESP-IDF v5.5: `. $IDF_PATH/export.sh`.
2. Build: `idf.py build` (in this folder).
3. First flash, or after other firmware: erase first, because other firmware can read our NVS
   (docs/security.md).

   ```bash
   idf.py -p /dev/ttyACM0 erase-flash flash monitor
   ```

## Host tests
The form checks (`main/validate.c`) are pure C and run on the PC:

```bash
make -C firmware/test
```

## Use
- Boot: a short beep, then the log shows `mic level: rms N` once.
- Not set up: the screen says "HOLD BUTTON 5 S". Hold the button for 5 s.
- Setup: the screen shows a Wi-Fi QR code, the PIN, `192.168.4.1` and the access point
  password. Scan the code with the phone camera to join the access point. Open
  `http://192.168.4.1/` and fill the form:
  - PIN: the 6 digits on the screen.
  - Wi-Fi name and password: your home network (WPA2 or WPA3).
  - Server URL: `ws://<server LAN IP>:8000/v1/device`.
  - Device token: 43 to 128 characters of A-Z, a-z, 0-9, `-` and `_`. The server keeps
    its SHA-256 in `OTA_DEVICE_TOKEN_SHA256`: `printf '%s' "$TOKEN" | sha256sum`.

  Save: the device reboots. The setup stops after 5 minutes or 5 wrong PINs.
- Set up: the "NO WIFI" face until Wi-Fi and the server session are up, then the idle face.
- Turn: press (listening face), speak, press again (thinking, then speaking face).
- Barge-in: a press while the device thinks or speaks stops the reply at once and starts a
  new turn.
- Confirm: a risky tool asks a question, then the screen shows "SAY YES". Press, say "yes",
  press again. With no press in 10 s, the server sends `confirm_timeout`: a low beep, then
  the idle face.
- Errors (for example `no_speech` or `too_many_turns`): a low beep, then the idle face.
- "SERVER REFUSED": a bad token (4401), an old firmware (4426) or another device took the
  session (4000). The device does not reconnect. Hold the button 5 s to set it up again.

## Board notes
- The SuperMini radio goes deaf at 20 dBm: the firmware limits Wi-Fi TX power to 2 dBm.
  Keep the board near the access point.
- Opening the USB serial port can reset the board.
