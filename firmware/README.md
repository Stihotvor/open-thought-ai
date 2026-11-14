# Firmware

ESP-IDF v5.5 project for the ESP32-C3 SuperMini (docs/hardware.md).

M1 status: faces, button, test tone, microphone level, Wi-Fi station and SoftAP setup.
The link to the server comes in M2 (`ws_link`).

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

## Use (M1)
- Boot: a short beep, then the log shows `mic level: rms peak N` once.
- Not set up: the screen says "HOLD BUTTON 5 S". Hold the button for 5 s.
- Setup: the screen shows the access point name, its password, a PIN and `192.168.4.1`.
  Join the access point, open `http://192.168.4.1/`, fill the form, save. The device reboots.
  The setup stops after 5 minutes or 5 wrong PINs.
- Set up: the "NO WIFI" face until Wi-Fi connects, then the idle face.
- Short press (idle): the listening face, and the log shows the mic level every second.
  Press again: a test tone, then idle.
