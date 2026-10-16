# FarmCam hardware acceptance

These checks require the actual Pi, camera, Hailo models, ESP32, pump, buzzer, and SIM800L. Automated tests do not establish physical operation.

- Back up the existing databases and service environment. Install the updated client/server together and flash the canonical ESP32 sketch with the correct network settings.
- Verify the Pi can reach ESP32 `/status` and `/sms/status?id=probe`; record the boot ID. Confirm GPIO 26 drives the buzzer and GPIO 27 drives the relay.
- Start with the camera at station 1 upper position. Observe a full four-station cycle: 30-second upper/lower windows, separate five-second down/up movement, one-second settling, ten-second moves, and the fifteen-second return after station 4. Record the actual nominal 333-second cycle and mechanical alignment.
- Move a target during transit/settling. Confirm no new detections or automatic sprays originate from those frames. Confirm a frame finishing inference after its window is rejected.
- Present a real target at station 1, then scan station 2. Confirm station 1 remains recorded. Complete a clean rescan of station 1 and confirm only its corresponding position is resolved.
- Disconnect the camera mid-window and restart the service. Confirm incomplete scans are aborted and prior confirmed tracks remain. Confirm pump/SMS cooldowns retain their remaining time after restart.
- Inspect full snapshots and thumbnails from the client. Mark a reviewed detection correct and another false positive; confirm filters and reviewed precision update.
- Exercise the pump using the established safe test setup. Confirm hold acknowledgement, release, release during pending start, app backgrounding, lost heartbeat, and ESP32 watchdog behavior. Check automatic sprays do not start during a manual hold.
- Trigger multiple new detections together. Confirm coalesced buzzer output, mute/unmute, no continuous manual-hold tone, bounded pulses after Pi disconnection, and the duty limit.
- Configure the actual farmer recipient and send a test SMS deliberately. Observe queued → sending → submitted/failed; confirm actual handset receipt separately. Disconnect the network during submission and verify no duplicate message is generated. Reboot the ESP32 before acknowledgement and confirm the result is unconfirmed.
- Confirm API/control ports are reachable only from the intended local network. Record device firmware versions, test time, results, and remaining issues before normal operation.
