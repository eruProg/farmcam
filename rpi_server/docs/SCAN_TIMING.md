# Adjustable scan timing

Update both the Pi source and the phone application for this feature. Keep the
Pi models, data, Python environment, and hardware configuration. Restart
`farmcam` after copying the complete source folder. No motor firmware change is
included: the motor controller remains separate.

The service now boots **idle**. Camera preview and the HDMI screen remain
available. A restart never assumes that the camera is physically at lettuce 1.

## Use

1. Edit **Scan timing** on the phone connection screen, even while offline.
2. Enter the measured return time from lettuce 4 lower to lettuce 1 lower.
3. Connect to the Pi. Open **Scan timing / Start / Stop** on the dashboard.
4. Position the camera at lettuce 1 lower and synchronize the separate motor
   controller. Tap **Apply timings & start scan**.
5. To adjust a running scan, tap **Stop scan**, stop the motor separately,
   reposition at lettuce 1 lower, edit timings, and start again.

Drafts are saved on the phone. The Pi acknowledges a complete timing profile
before the application displays it as active. Reconnection does not restart or
overwrite a running scan. An outdated Pi server displays an update message.

Default sequence: five-second initial wait, then 60 seconds for each part.
Lettuces 1 and 3 scan lower then upper; lettuces 2 and 4 scan upper then lower.
Raising and lowering each take 2.8 seconds. Horizontal travel is 8.5 seconds
(1 to 2), 8.6 seconds (2 to 3), and 8.6 seconds (3 to 4).
After the user-defined return timer expires, the next cycle starts directly at
lettuce 1 lower, without another initial wait or additional settling delay.

Repeated-cycle duration is **516.9 seconds + return time**. The first cycle adds
the five-second initial wait. These are nominal durations; actual hardware
alignment must be checked through at least two cycles. No limit-switch feedback
is read, and the software cannot know whether the mechanism has reached home.

## Interfaces

- `GET /api/scan/settings`: saved settings and current state.
- `POST /api/scan/start`: full JSON profile with `scan_s`, `initial_delay_s`,
  `raise_s`, `lower_s`, `travel_1_2_s`, `travel_2_3_s`, `travel_3_4_s`, `return_s`.
  All values must be finite positive numbers; only initial delay may be zero.
  Missing return time is invalid. Rejects an already-running scan with 409.
- `POST /api/scan/stop`: aborts an incomplete window and returns idle state;
  repeated stops are safe. Completed history remains intact.
- Scan state includes `running`, `run_id`, and `settings`, with new
  `INITIAL_DELAY` and `RETURNING` phases. Existing countdown fields remain.

The Pi atomically saves accepted settings in `data/scan_settings.json`. Storage
failure prevents starting. Invalid saved settings leave the service idle with
default values and require re-entry. Automatic detection-triggered spraying is
not initiated during idle, startup, travel, or return. Stop scan does not operate
the motor or replace the existing separate pump controls/watchdog.

## Hardware acceptance still required

Measure the actual return interval. Check initial synchronization and the
position at all eight scan windows across two cycles. Reboot the Pi and verify
it stays idle; reconnect the phone during a run and verify it continues. Check
the HDMI labels, phone controls, and inference performance simultaneously.
