# Manual pump and SMS update

Update all three components: ESP32 sketch, Raspberry Pi server, and Android app.
The existing installed APK and uploaded firmware do not change when these local
source files are edited.

## Button behavior

- Hold the button for about half a second to start. It shows Connecting until
  the ESP32 acknowledges the command.
- The pump and buzzer run together while the button stays held.
- A confirmed start requests ONE `MANUAL_PUMP` SMS immediately, including while
  the button remains held. SMS delivery still depends on the SIM/network.
- Another press within 10 seconds of the last manual SMS attempt does not send
  another manual SMS. This cooldown only limits SMS; the pump can still run.
- Continuing to hold for more than 10 seconds does not send another message.
  A new press after the cooldown can send one new message.
- Release/cancel/backgrounding stops the manual pump and buzzer, with no release
  SMS. Losing app heartbeats also stops the pump. The ESP32 retains its independent
  four-second hardware watchdog, including during SMS waits.
- Manual SMS queue requests and modem submissions are not automatically retried
  after uncertain acknowledgements, to avoid duplicate messages.

The existing boot notification and automatic detection notifications are separate
from manual-spray notifications.

## Install

1. Open `farmcam_esp32/farmcam_esp32.ino` in Arduino IDE. Select the actual ESP32
   board/port, confirm the Wi-Fi credentials, then upload. This sketch is for the
   ESP32 DevKit with pump relay on GPIO27, active buzzer on GPIO26, SIM800L TXD to
   GPIO16, and SIM800L RXD to GPIO17.
2. Open Serial Monitor at 115200 baud and reset the ESP32. Note its reported IP.
3. Copy the updated `main.py` to the Pi's existing FarmCam server folder. Set
   `ESP32_IP` to the address from step 2, or set `FARMCAM_ESP32_IP` in the server
   process environment. Restart the service with `sudo systemctl restart farmcam`
   if using the provided systemd setup. Otherwise restart the Python process.
4. Install the updated `FarmCam-Fixed.apk` from the project root on the phone.
   The app setup address remains the Raspberry Pi IP, port 8000.

## Check without activating the pump

On the Pi, substitute the actual ESP32 IP:

```bash
curl --max-time 5 http://ESP32_IP_HERE/status
```

The response includes `pump`, `sim`, `sim_error`, `sms_result`, and `sms_queued`.
`sim` now means SIM/network/text-mode checks passed at the last check; it is not
a delivery receipt. `submitted to network` means a `+CMGS` acknowledgement was
received, not that the recipient has read or received the message.

In Serial Monitor, enter `SIM_DIAG` with a newline. It prints modem identity,
SIM PIN state, signal, GSM registration, text mode, character set, SMS parameters,
and the SMS service center. It does not activate the pump or send a test SMS.

For the earlier `ERROR` / missing `>` problem, the firmware now waits for each
command's response, enables detailed errors, checks SIM readiness and GSM
registration, confirms SMS text mode, and reads the SIM's SMS center. It no
longer blindly writes CSMP before every SMS or retries three recipient formats.
It waits up to the documented 60 seconds for `+CMGS` plus `OK`.

If a SIM/network check fails, queued messages wait and readiness is rechecked
every 15 seconds. If the modem rejects an actual submission, the exact response
is printed and exposed in status. SIM availability, registration, account credit,
carrier support, and physical power/wiring must still work on the actual device.

Reference: [SIMCom SIM800 AT command manual](https://simcom.ee/documents/SIM800C/SIM800%20Series_AT%20Command%20Manual_V1.10.pdf),
sections 4.2.2 (CMGF), 4.2.5 (CMGS), 4.2.12 (CSCA), and 4.2.15 (CSMP).
