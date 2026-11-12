import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../services/api_service.dart';
import 'ui.dart';

/// Manual pump: tap = 2.5 s spray, hold = spray until released.
///
/// Safety logic is the one already proven on the machine (and covered by
/// test/manual_pump_widget_test.dart):
///  - hold sends /hold, then a heartbeat every 500 ms while held; the Pi
///    stops by itself if heartbeats stop
///  - releasing before the Pi confirmed /hold still sends exactly one
///    /release, after the /hold reply arrives
///  - leaving the app (or this widget) while spraying sends /release
///  - during the automatic-spray cooldown it first asks "Spray during the
///    cooldown?"; after "Spray anyway" it is unlocked for 60 s and sends
///    override=1 (the spray restarts the cooldown on the Pi)
class PumpButton extends StatefulWidget {
  const PumpButton({super.key, required this.api, this.demo = false, this.onChanged,
      this.disabledReason, this.cooldown});
  final ApiService api;
  final bool demo;
  final VoidCallback? onChanged;
  final String? disabledReason;
  /// The automatic-spray cooldown from the Pi (null: unknown).
  final Cooldown? cooldown;

  @override
  State<PumpButton> createState() => _PumpButtonState();
}

class _PumpButtonState extends State<PumpButton> with WidgetsBindingObserver {
  bool _held = false, _starting = false, _stopping = false, _confirmed = false;
  int _session = 0;
  Timer? _heartbeat;
  DateTime? _overrideUntil;

  bool get _inCooldown => (widget.cooldown?.secondsLeft ?? 0) > 0;
  bool get _overriding => _overrideUntil != null && DateTime.now().isBefore(_overrideUntil!);

  /// Ask before a manual spray while automatic spraying is paused.
  Future<bool> _confirmCooldown([double? secondsLeft]) async {
    final cooldown = widget.cooldown;
    final left = secondsLeft ?? cooldown?.secondsLeft ?? 0;
    final last = cooldown?.lastAt;
    final total = cooldown?.total ?? 0;
    final period = total >= 86400
        ? '${(total / 86400).round()}-day cooldown'
        : total > 0
            ? '${durationText(total)} cooldown'
            : 'cooldown';
    final ok = await confirmAction(context,
        title: 'Spray during the cooldown?',
        message: 'Automatic spraying is paused for another ${durationText(left)}'
            '${last != null ? ' (last spray ${shortDateTime(last)})' : ''}. '
            'Spraying now restarts the $period.',
        confirmLabel: 'Spray anyway',
        destructive: true);
    if (ok) _overrideUntil = DateTime.now().add(const Duration(seconds: 60));
    return ok;
  }

  /// A long press can't continue through a dialog: confirm, then hold again.
  Future<void> _confirmThenHoldAgain([double? secondsLeft]) async {
    if (await _confirmCooldown(secondsLeft) && mounted) {
      showMessage(context, 'Confirmed. Hold the button again to spray, or tap for 2.5 s.');
    }
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _heartbeat?.cancel();
    _held = false;
    if (_confirmed && !_starting && !_stopping) widget.api.pumpRelease();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) _pressEnd();
  }

  void _error(String message) {
    if (mounted) showMessage(context, message, error: true);
  }

  Future<void> _pressStart() async {
    if (_held || _starting || _stopping || widget.demo || widget.disabledReason != null) return;
    if (_inCooldown && !_overriding) {
      await _confirmThenHoldAgain();
      return;
    }
    HapticFeedback.heavyImpact();
    final session = ++_session;
    setState(() {
      _held = true;
      _starting = true;
    });
    final result = await widget.api.pumpHold(override: _overriding);
    _starting = false;
    if (result.blockedByCooldown) {
      // Nothing started on the Pi; the cooldown info here was out of date.
      if (mounted) setState(() => _held = false);
      if (mounted) await _confirmThenHoldAgain(result.cooldownLeft);
      return;
    }
    // Release may have happened while /hold was in flight: stop AFTER it returns.
    if (!mounted || !_held || !result.ok) {
      if (mounted) setState(() => _held = false);
      if (!result.ok) _error(result.error!);
      await _stop();
      return;
    }
    setState(() => _confirmed = true);
    widget.onChanged?.call();
    _heartbeat?.cancel();
    var inFlight = false;
    _heartbeat = Timer.periodic(const Duration(milliseconds: 500), (_) async {
      if (!_held || inFlight) return;
      inFlight = true;
      final beat = await widget.api.pumpHeartbeat();
      inFlight = false;
      if (!beat.ok && _held && mounted && session == _session) {
        _error(beat.error!);
        _pressEnd();
      }
    });
  }

  void _pressEnd() {
    if (!_held) return;
    _heartbeat?.cancel();
    setState(() => _held = false);
    if (!_starting) _stop();
  }

  Future<void> _stop() async {
    if (_stopping) return;
    _stopping = true;
    _heartbeat?.cancel();
    if (mounted) setState(() => _confirmed = false);
    final result = await widget.api.pumpRelease();
    _stopping = false;
    if (mounted) setState(() {});
    widget.onChanged?.call();
    if (!result.ok) _error(result.error!);
  }

  Future<void> _tap() async {
    if (widget.disabledReason != null) {
      _error(widget.disabledReason!);
      return;
    }
    if (widget.demo) {
      setState(() => _confirmed = !_confirmed);
      showMessage(context, _confirmed ? 'Demo: pump started' : 'Demo: pump stopped');
      return;
    }
    if (_held || _confirmed) {
      await _stop();
      return;
    }
    if (_inCooldown && !_overriding && !await _confirmCooldown()) return;
    if (!mounted) return;
    HapticFeedback.mediumImpact();
    setState(() => _starting = true);
    var result = await widget.api.triggerPump(override: _overriding);
    if (!mounted) return;
    if (result.blockedByCooldown) {
      setState(() => _starting = false);
      if (!await _confirmCooldown(result.cooldownLeft) || !mounted) return;
      setState(() => _starting = true);
      result = await widget.api.triggerPump(override: true);
      if (!mounted) return;
    }
    setState(() {
      _starting = false;
      _confirmed = result.ok;
    });
    widget.onChanged?.call();
    if (!result.ok) {
      _error(result.error ?? 'The pump did not start.');
      return;
    }
    showMessage(context, 'Spraying for 2.5 s. Hold the button to spray longer.');
    Future.delayed(const Duration(milliseconds: 3000), () {
      if (mounted && !_held) setState(() => _confirmed = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final spraying = _held || _confirmed;
    final label = _starting
        ? 'Connecting…'
        : _stopping
            ? 'Stopping…'
            : spraying
                ? 'SPRAYING — Stop'
                : 'Manual Pump';
    final color = spraying ? farm.info : Theme.of(context).colorScheme.primary;
    return GestureDetector(
      onTap: _tap,
      onLongPressStart: (_) => _pressStart(),
      onLongPressEnd: (_) => _pressEnd(),
      onLongPressCancel: _pressEnd,
      child: AnimatedScale(
        scale: _held ? 0.97 : 1,
        duration: const Duration(milliseconds: 120),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 220),
          padding: const EdgeInsets.symmetric(horizontal: Space.lg, vertical: Space.lg),
          decoration: BoxDecoration(
            color: spraying ? farm.infoBg : farm.card,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: spraying ? color : farm.border, width: spraying ? 2 : 1),
            boxShadow: spraying ? [BoxShadow(color: color.withOpacity(0.25), blurRadius: 16)] : const [],
          ),
          child: Row(children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              width: 48,
              height: 48,
              decoration: BoxDecoration(color: color.withOpacity(spraying ? 1 : 0.12), shape: BoxShape.circle),
              child: _starting || _stopping
                  ? Padding(
                      padding: const EdgeInsets.all(14),
                      child: CircularProgressIndicator(strokeWidth: 2.4, color: color))
                  : Icon(Icons.water_drop_rounded, color: spraying ? Colors.white : color),
            ),
            const SizedBox(width: Space.md),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(label, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, color: spraying ? color : null)),
                const SizedBox(height: 2),
                Text(widget.disabledReason ?? (spraying ? 'Release or tap to stop' : 'Tap: 2.5 s spray · Hold: spray while held'),
                    style: TextStyle(color: farm.muted, fontSize: 12.5)),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}
