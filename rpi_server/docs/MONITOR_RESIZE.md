# Wider Pi monitor preview (Phase 1)

This update changes only `display/display.css`. The camera receives about 69% of the main row, subject to a 310px minimum station column. History uses its content height so the actual camera image can grow. The full image retains its aspect ratio; side borders can still appear. Colors, station cards, history and equipment status retain their existing design.

## Copy to the Pi without SSH

1. On this computer, copy `rpi_server/display/display.css` onto a USB drive. Connect the drive to the Pi.
2. Open a terminal on the Pi (Ctrl+Alt+T). Find the installed server folder:

   ```sh
   systemctl show farmcam -p WorkingDirectory -p ExecStart
   ```

3. Change to the folder printed as `WorkingDirectory`. For example, **only if that is the reported folder**:

   ```sh
   cd ~/Desktop/farmcam_server
   ```

4. Close the monitor window and back up the stylesheet:

   ```sh
   python3 kiosk_runner.py --stop
   cp display/display.css "display/display.css.backup-$(date +%Y%m%d-%H%M%S)"
   ```

5. In the Pi file manager, copy `display.css` from the USB drive into the installed server folder's **display** subfolder. Replace the existing `display.css`. Do not put it beside `main.py`.
6. In the same terminal, restart the display service and reopen the monitor:

   ```sh
   sudo systemctl restart farmcam-display
   python3 kiosk_runner.py
   ```

   Run the launcher as the normal desktop user, without sudo. Reopening Chromium loads the new stylesheet. There is no need to restart `farmcam`, rerun the installer, change HDMI resolution, or install a phone APK.

For future desktop access, repeat `python3 kiosk_runner.py --stop` from the installed folder. To return to the monitor, run `python3 kiosk_runner.py` from a terminal in the Pi desktop session.

## Verification

Browser checks passed at 1024x600, 1280x800 and 1920x1080 with 100% and 125% typography. They cover waiting, monitoring, camera unavailable and reconnection states, station readability, history clipping, image containment and polling recovery. A synthetic 4:3 image grew in visible width at every tested size (normal text: approximately 429 to 445px, 509 to 536px, and 883 to 909px respectively).

Screenshots use simulated data, not live detection. Actual Pi deployment and a visual check on the physical monitor remain necessary. Detection, capture settings, scan timings and the phone app are unchanged. Android notifications remain Phase 2, pending the user's “continue.”
