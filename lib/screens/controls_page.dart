import 'dart:async';
import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/pump_button.dart';
import '../widgets/scan_timing_card.dart';
import '../widgets/ui.dart';
import 'system_page.dart';

/// Everything that moves hardware or sends something: scan start/stop and
/// timings, the pump, buzzer, SMS, plus the system overview.
class ControlsPage extends StatelessWidget {
  const ControlsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return ListView(padding: const EdgeInsets.all(Space.lg), children: [
      Text('Controls', style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: Space.md),
      const _Label('PUMP'),
      ValueListenableBuilder<AppSnapshot?>(
        valueListenable: controller.snapshot,
        builder: (context, snap, _) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          PumpButton(api: controller.api, demo: controller.isDemo, onChanged: controller.refresh,
              cooldown: snap?.pumpCooldown),
          const SizedBox(height: Space.sm),
          Row(children: [
            Expanded(child: CooldownStatus(label: 'Auto-spray', cooldown: snap?.pumpCooldown)),
            const SizedBox(width: Space.sm),
            Expanded(child: CooldownStatus(label: 'SMS', cooldown: snap?.smsCooldown)),
          ]),
          if (snap != null && !snap.esp32.reachable) ...[
            const SizedBox(height: Space.sm),
            Row(children: [
              Icon(Icons.info_outline_rounded, size: 16, color: farm.fungus),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                    snap.esp32.scanning
                        ? 'Looking for the ESP32 on the hotspot…'
                        : 'The ESP32 (pump/SMS) is not answering; the Pi keeps searching for it.',
                    style: TextStyle(color: farm.fungus, fontSize: 12.5)),
              ),
            ]),
          ],
        ]),
      ),
      const SizedBox(height: Space.xl),
      const _Label('SCAN'),
      ScanTimingCard(api: controller.isDemo ? null : controller.api, onCommand: controller.refresh),
      const SizedBox(height: Space.xl),
      const _Label('ALERTS'),
      const _AlertsCard(),
      const SizedBox(height: Space.xl),
      const _Label('SYSTEM'),
      SectionCard(
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SystemPage())),
        child: Row(children: [
          Icon(Icons.memory_rounded, color: farm.muted),
          const SizedBox(width: Space.md),
          const Text('System overview', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(width: Space.sm),
          Expanded(
            child: Text('thresholds, timings, hardware',
                textAlign: TextAlign.right, overflow: TextOverflow.ellipsis,
                style: TextStyle(color: farm.muted, fontSize: 12)),
          ),
          const Icon(Icons.chevron_right_rounded),
        ]),
      ),
      const SizedBox(height: Space.sm),
      const PowerCard(),
      const SizedBox(height: Space.xl),
    ]);
  }
}

/// Shut down or restart the Pi. Both ask again before anything happens.
class PowerCard extends StatelessWidget {
  const PowerCard({super.key});

  Future<void> _choose(BuildContext context) async {
    final farm = FarmColors.of(context);
    final action = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(Space.xl, 0, Space.xl, Space.xl),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text('Power', style: Theme.of(sheet).textTheme.titleLarge),
            const SizedBox(height: Space.sm),
            Text('Scanning and spraying stop first.', style: TextStyle(color: farm.muted)),
            const SizedBox(height: Space.xl),
            FilledButton.tonalIcon(
              onPressed: () => Navigator.pop(sheet, 'restart'),
              icon: const Icon(Icons.restart_alt_rounded),
              label: const Text('Restart'),
            ),
            const SizedBox(height: Space.sm),
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: farm.pest),
              onPressed: () => Navigator.pop(sheet, 'shutdown'),
              icon: const Icon(Icons.power_settings_new_rounded),
              label: const Text('Shut down'),
            ),
            const SizedBox(height: Space.sm),
            OutlinedButton(onPressed: () => Navigator.pop(sheet), child: const Text('Cancel')),
          ]),
        ),
      ),
    );
    if (action == null || !context.mounted) return;
    final shutdown = action == 'shutdown';
    final ok = await confirmAction(context,
        title: shutdown ? 'Shut down FarmCam?' : 'Restart FarmCam?',
        message: shutdown
            ? 'Scanning and spraying stop and the Pi turns off. To turn it on again, '
                'unplug the Pi\'s power and plug it back in.'
            : 'Scanning stops and the Pi restarts. It is back in about 1–2 minutes and the app reconnects by itself.',
        confirmLabel: shutdown ? 'Shut down' : 'Restart',
        destructive: true);
    if (!ok || !context.mounted) return;
    final controller = FarmCamScope.of(context);
    final problem = controller.isDemo ? null : await controller.api.systemPower(action);
    if (!context.mounted) return;
    showMessage(
        context,
        problem ??
            (shutdown
                ? 'FarmCam is shutting down. Wait 30 seconds before unplugging it.'
                : 'FarmCam is restarting…'),
        error: problem != null);
  }

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    return SectionCard(
      onTap: () => _choose(context),
      child: Row(children: [
        Icon(Icons.power_settings_new_rounded, color: farm.pest),
        const SizedBox(width: Space.md),
        const Text('Power', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(width: Space.sm),
        Expanded(
          child: Text('shut down or restart',
              textAlign: TextAlign.right, overflow: TextOverflow.ellipsis,
              style: TextStyle(color: farm.muted, fontSize: 12)),
        ),
        const Icon(Icons.chevron_right_rounded),
      ]),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: Space.sm),
        child: Text(text,
            style: Theme.of(context).textTheme.labelSmall?.copyWith(color: FarmColors.of(context).muted)),
      );
}

/// "Ready" or a live countdown, ticking locally between polls.
class CooldownStatus extends StatefulWidget {
  const CooldownStatus({super.key, required this.label, required this.cooldown});
  final String label;
  final Cooldown? cooldown;
  @override
  State<CooldownStatus> createState() => _CooldownStatusState();
}

class _CooldownStatusState extends State<CooldownStatus> {
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && (widget.cooldown?.secondsLeft ?? 0) > 0) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final c = widget.cooldown;
    final left = c?.secondsLeft ?? 0;
    final ready = c != null && left <= 0;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: Space.md, vertical: Space.sm),
      decoration: BoxDecoration(
          color: ready ? farm.healthyBg : farm.subtle, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        SizedBox(
          width: 18,
          height: 18,
          child: CircularProgressIndicator(
            value: c == null ? 0 : ready ? 1 : 1 - left / c.total,
            strokeWidth: 2.4,
            color: ready ? farm.healthy : farm.fungus,
            backgroundColor: farm.border,
          ),
        ),
        const SizedBox(width: Space.sm),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(widget.label,
                maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: farm.muted, fontSize: 11.5)),
            Text(c == null ? '–' : ready ? 'Ready' : '${durationText(left)} left',
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.w700, color: ready ? farm.healthy : null)),
          ]),
        ),
      ]),
    );
  }
}

class _AlertsCard extends StatefulWidget {
  const _AlertsCard();
  @override
  State<_AlertsCard> createState() => _AlertsCardState();
}

class _AlertsCardState extends State<_AlertsCard> {
  bool? _mutedOverride;
  bool _muting = false, _sending = false;
  String? _smsStatus;

  static const _smsLabels = {
    'idle': 'No message yet', 'queued': 'Queued', 'sending': 'Sending…',
    'sent': 'Sent to the network', 'failed': 'Failed', 'unknown': 'Unconfirmed',
  };

  Future<void> _setMute(bool muted) async {
    final controller = FarmCamScope.of(context);
    setState(() {
      _mutedOverride = muted;
      _muting = true;
    });
    final ok = controller.isDemo || await controller.api.setBuzzerMute(muted);
    if (!mounted) return;
    setState(() {
      _muting = false;
      if (!ok) _mutedOverride = null;
    });
    if (!ok) showMessage(context, 'The Pi did not confirm the buzzer change.', error: true);
    controller.refresh();
  }

  Future<void> _sms() async {
    final controller = FarmCamScope.of(context);
    if (controller.isDemo) {
      showMessage(context, 'Demo: test SMS sent');
      return;
    }
    final cd = controller.snapshot.value?.smsCooldown;
    if (cd != null && cd.secondsLeft > 0) {
      showMessage(context, 'SMS cooldown: wait ${mmss(cd.secondsLeft)}');
      return;
    }
    if (!await confirmAction(context,
        title: 'Send a test SMS?', message: 'The farmer\'s phone receives "FarmCam test: SMS alerts are working."',
        confirmLabel: 'Send SMS')) {
      return;
    }
    setState(() => _sending = true);
    try {
      final r = await controller.api.testSms();
      if (mounted) {
        showMessage(context, r['via'] == 'v2'
            ? 'SMS queued on the ESP32 (it texts the number in its firmware)'
            : 'SMS queued to ${r['to'] ?? 'the farmer'}');
      }
      // The SIM800L may need up to a minute (network registration + sending).
      Map<String, dynamic>? status;
      for (var i = 0; i < 45 && mounted; i++) {
        await Future.delayed(const Duration(seconds: 2));
        status = await controller.api.getSmsStatus();
        if (!mounted) return;
        setState(() => _smsStatus = status?['status'] as String?);
        if (const {'sent', 'failed'}.contains(_smsStatus)) break;
      }
      final err = '${status?['err'] ?? ''}';
      if (mounted && _smsStatus == 'failed' && err.isNotEmpty) {
        showMessage(context, 'SMS failed: $err', error: true);
      }
    } catch (e) {
      if (mounted) showMessage(context, '$e'.replaceFirst('Exception: ', ''), error: true);
    } finally {
      if (mounted) setState(() => _sending = false);
      controller.refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return ValueListenableBuilder<AppSnapshot?>(
      valueListenable: controller.snapshot,
      builder: (context, snap, _) {
        final muted = _muting ? _mutedOverride : snap?.buzzerMuted ?? _mutedOverride;
        return SectionCard(
          child: Column(children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              secondary: Icon(muted == true ? Icons.volume_off_rounded : Icons.volume_up_rounded),
              title: const Text('Buzzer'),
              subtitle: Text(muted == null ? 'Unavailable' : muted ? 'Muted' : 'Beeps on new detections'),
              value: muted == false,
              onChanged: muted == null || _muting ? null : (on) => _setMute(!on),
            ),
            const Divider(),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.sms_outlined),
              title: const Text('Test SMS to the farmer'),
              subtitle: Text(_smsStatus == null ? 'Checks the SIM800L and signal' : 'Last: ${_smsLabels[_smsStatus] ?? _smsStatus}',
                  style: TextStyle(color: _smsStatus == 'failed' ? farm.pest : null)),
              trailing: _sending
                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.send_rounded),
              onTap: _sending ? null : _sms,
            ),
          ]),
        );
      },
    );
  }
}
