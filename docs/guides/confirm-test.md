# Confirm flow test (owner, by hand)

This guide tests the confirm flow of issue #30 with a real board and your own keys.
The hub holds a risky tool call. The device asks for a spoken "yes". The call runs only after the yes.
The flow is in [protocol.md](../protocol.md), section "Confirm flow (risky tools)".

Items marked **verify** are not confirmed.

## What you test
- Risky tool: `set_volume` (it has `confirm=True`). It is the only built-in risky tool.
- Safe tools: `set_face` and `get_status` (they have `confirm=False`).
- Question text: `Should I run device set_volume with level 30? Say yes to confirm.`
- The args summary (`level 30`) is **spoken**. The screen shows only the face and the text `SAY YES`.
  The `confirm.request` message has `tool` and `timeout_ms`, but no args.
- Timeout: 10 s after the question ends (`CONFIRM_TIMEOUT_S`).
- Yes words: `yes` or `yeah` (EN), `tak` (PL), `так` (UK). Only your session language counts (`OTA_LANGUAGE`).

## Prerequisites
- A board with the firmware flashed and set up. See [firmware/README.md](../../firmware/README.md).
- A speaker volume that you can hear. Know the volume before the test (step 2 shows it).
- Your own keys in `.env` in the repo root. Do not commit `.env`. Do not share it.
- Voice pipeline: `OTA_PIPELINE=voice` with CHAT, STT and TTS set.
  For free local STT and TTS, see [speech-server.md](speech-server.md).
- The board and the server on the same LAN. The board server URL is `ws://<server LAN IP>:8000/v1/device`.
- A serial monitor on the board (step 3) to read the logs.

Run all commands from the repo root.

## Steps

1. Start the server with your keys.

   ```bash
   cd server && .venv/bin/uvicorn ota_server.main:app --host 0.0.0.0 --port 8000
   ```

   **verify**: the command, and that the server reads `.env` from the repo root. The Dockerfile uses the same `uvicorn ota_server.main:app`.
   Expected: the server starts with no error. A bad key or a missing role stops it at startup with a clear message.

2. Flash the board, or reset it.
   - New firmware: `idf.py -p /dev/ttyACM0 erase-flash flash monitor` (in `firmware/`, ESP-IDF v5.5 loaded).
   - Same firmware: press the reset button.

   Expected: a short beep, then the "NO WIFI" face, then the idle face.
   The server log shows the device session. **verify** the exact log line.

3. Open the serial monitor: `idf.py -p /dev/ttyACM0 monitor`.
   Expected: the idle face on the screen. Logs show `turn N: confirm <tool>` later, in step 5.
   Note: opening the serial port can reset the board. Open it before the test turns.

4. Make a risky tool call.
   Press the button. Say: "Set the volume to 30." Press the button again.
   Expected, in this order:
   - Listening face, then thinking face.
   - The board speaks the question: "Should I run device set_volume with level 30? Say yes to confirm."
   - The volume does **not** change yet.

5. Check the confirm face.
   Expected after the question ends:
   - The screen shows the confirm face: two eyes and the text `SAY YES`.
   - The monitor log shows `turn N: confirm device__set_volume`. **verify** the tool name format (it may use a separator other than `__`).
   - The spoken question contains the tool name and the args summary (`level 30`).

6. Approve.
   Repeat steps 4 and 5. While the confirm face shows, press the button. Say "yes". Press the button again.
   Expected:
   - The board speaks "Done."
   - The volume changes to 30. Say anything and check the loudness. Or ask "What is the device status?" and read the volume. **verify** that the status shows the volume.
   - Say "yes" again after "Done." A new turn starts. Nothing runs twice.

7. Deny.
   Repeat steps 4 and 5. While the confirm face shows, press the button. Say "no". Press the button again.
   Expected:
   - The board speaks "Cancelled."
   - The volume does not change.
   - Also test a near-yes: "yes please". Expected: "Cancelled." (the rule is an exact match).

8. Expiry (timeout).
   Repeat steps 4 and 5. Do **not** press the button. Wait 10 s after the question ends.
   Expected:
   - A low beep, then the idle face.
   - The monitor log shows `error: confirm_timeout`.
   - The volume does not change.
   - Now press the button and say "yes". Expected: no volume change. The call is gone. The assistant treats "yes" as a normal question.

9. Check that a safe tool does not ask.
   Press the button. Say: "Show the happy face." Press the button again. **verify** the face names in `FACES` (`server/src/ota_server/hub/device_source.py`).
   Expected:
   - The face changes at once. There is no question and no confirm face.
   - Say: "What is the device status?" Expected: a spoken answer, no question.

10. Optional: abort.
    Repeat steps 4 and 5. While the confirm face shows, press the button (listening starts). Then say "no" and press again. This is step 7.
    For a real abort: **verify** how to send `abort` from the board. A press while the device thinks or speaks sends it. Press during the question. Expected: no confirm face, and "yes" afterwards runs nothing.

## Results

Write pass or fail for each step. Write notes for each fail (log lines, what you saw).

| Step | Check | Result (pass / fail) | Notes |
|---|---|---|---|
| 1 | Server starts with your keys | | |
| 2 | Board boots to the idle face | | |
| 3 | Serial monitor shows the logs | | |
| 4 | Risky call: question is spoken, volume unchanged | | |
| 5 | Confirm face `SAY YES`, args summary spoken | | |
| 6 | Approve: "Done.", volume 30, no second run | | |
| 7 | Deny: "Cancelled.", volume unchanged, "yes please" cancels | | |
| 8 | Expiry: low beep, `confirm_timeout`, late yes runs nothing | | |
| 9 | Safe tools ask nothing | | |
| 10 | Abort drops the held call | | |

## If a step fails
- No question after step 4: the model did not call the tool. Say the request again, with "volume" in it.
- No confirm face: check that the question audio ended. The face shows after the reply drains.
- STT hears "yes" wrong: check the transcript in the server log. **verify** that the log shows it.
- Send the server log and the serial log with your report.
