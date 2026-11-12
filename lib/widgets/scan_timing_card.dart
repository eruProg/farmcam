import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../core/theme.dart';
import '../services/api_service.dart';
import '../data/scan_state_model.dart';

/// Countdown line: the only part of the card that redraws 10× a second.
class _LiveStatus extends StatefulWidget {
  const _LiveStatus({required this.clock, required this.running});
  final ScanStateModel clock;
  final bool running;
  @override
  State<_LiveStatus> createState() => _LiveStatusState();
}

class _LiveStatusState extends State<_LiveStatus> {
  Timer? _tick;
  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(milliseconds: 100), (_) {
      if (mounted && widget.running) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final clock = widget.clock;
    return Text(
        !clock.connected
            ? 'Checking scan connection…'
            : widget.running
                ? 'Lettuce ${clock.state['station']} · ${clock.state['phase_label']} · ${clock.remaining.toStringAsFixed(1)} s'
                : 'Ready to start',
        style: const TextStyle(fontWeight: FontWeight.w600));
  }
}

const timingDefaults = <String, String>{
  'scan_s': '60',
  'initial_delay_s': '5',
  'raise_s': '2.8',
  'lower_s': '2.8',
  'travel_1_2_s': '8.5',
  'travel_2_3_s': '8.6',
  'travel_3_4_s': '8.6',
  'return_s': '',
};
const timingLabels = <String, String>{
  'scan_s': 'Scan each part',
  'initial_delay_s': 'Initial wait only',
  'raise_s': 'Lower → upper',
  'lower_s': 'Upper → lower',
  'travel_1_2_s': 'Lettuce 1 → 2',
  'travel_2_3_s': 'Lettuce 2 → 3',
  'travel_3_4_s': 'Lettuce 3 → 4',
  'return_s': 'Return: lettuce 4 → 1',
};

class ScanTimingCard extends StatefulWidget {
  final ApiService? api;
  /// Called after start/stop so the rest of the app refreshes right away.
  final VoidCallback? onCommand;
  const ScanTimingCard({super.key, this.api, this.onCommand});
  @override
  State<ScanTimingCard> createState() => _ScanTimingCardState();
}

class _ScanTimingCardState extends State<ScanTimingCard> {
  final _form = GlobalKey<FormState>();
  final _controllers = {
    for (final e in timingDefaults.entries)
      e.key: TextEditingController(text: e.value)
  };
  final _clock = ScanStateModel();
  Timer? _pollTimer;
  bool _loaded = false, _polling = false, _busy = false, _supported = false;
  String? _error;
  int _revision = 0;
  Future<void> _saveQueue = Future.value();
  bool get _running => _clock.state['running'] == true;

  @override
  void initState() {
    super.initState();
    _load();
    if (widget.api != null) {
      _poll();
      _pollTimer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
      // The countdown line ticks on its own (_LiveStatus); the form does not.
    }
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    try {
      final saved =
          jsonDecode(prefs.getString('scan_timing_draft') ?? '{}') as Map;
      for (final e in _controllers.entries) {
        if (saved[e.key] is String) e.value.text = saved[e.key];
      }
    } catch (_) {/* A malformed draft does not prevent editing. */}
    setState(() => _loaded = true);
  }

  void _save() {
    final value =
        jsonEncode({for (final e in _controllers.entries) e.key: e.value.text});
    _saveQueue = _saveQueue.then((_) async {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('scan_timing_draft', value);
    }).catchError((Object e) {
      if (mounted) setState(() => _error = 'Could not save draft: $e');
    });
  }

  Future<void> _poll() async {
    if (_polling || _busy) return;
    _polling = true;
    final revision = _revision;
    final sent = _clock.now;
    try {
      final result = await widget.api!.scanRequest('settings');
      if (!mounted || revision != _revision) return;
      _clock.update(
          Map<String, dynamic>.from(result['state']), sent, _clock.now);
      setState(() {
        _supported = true;
        _error = null;
      });
    } catch (e) {
      if (mounted && revision == _revision) {
        setState(() {
          _clock.disconnect();
          _supported = false;
          _error = '$e';
        });
      }
    } finally {
      _polling = false;
    }
  }

  Future<void> _command(bool stop) async {
    if (!stop && !_form.currentState!.validate()) return;
    _revision++;
    setState(() {
      _busy = true;
      _error = null;
    });
    final sent = _clock.now;
    try {
      await _saveQueue;
      final result = await widget.api!.scanRequest(stop ? 'stop' : 'start',
          body: stop
              ? <String, dynamic>{}
              : {
                  for (final e in _controllers.entries)
                    e.key: double.parse(e.value.text.trim()),
                });
      if (!mounted) return;
      _clock.update(result, sent, _clock.now);
      setState(() {});
      widget.onCommand?.call();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = '$e';
          _clock.disconnect();
          _supported = false;
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final active = _clock.state['settings'] as Map?;
    final theme = Theme.of(context);
    final farm = theme.extension<FarmColors>();
    final accent = farm?.healthy ?? const Color(0xFF15803D);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
          color: farm?.card ?? theme.colorScheme.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: farm?.border ?? const Color(0xFFE4E4E7))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Scan timing',
            style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(
            '1 Lower → Upper · 2 Upper → Lower\n3 Lower → Upper · 4 Upper → Lower',
            style: TextStyle(color: accent, height: 1.6)),
        if (widget.api != null) ...[
          const SizedBox(height: 12),
          _LiveStatus(clock: _clock, running: _running),
          if (_running && active != null)
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('Active timings on Pi'),
              children: [
                for (final e in timingLabels.entries)
                  ListTile(
                      dense: true,
                      title: Text(e.value),
                      trailing: Text('${active[e.key]} s'))
              ],
            ),
        ],
        ExpansionTile(
          initiallyExpanded: true,
          maintainState: true,
          tilePadding: EdgeInsets.zero,
          title: Text(_running ? 'Edit timings for next run' : 'Edit timings'),
          subtitle:
              const Text('Saved on this phone • applied only when starting'),
          children: [
            Form(
                key: _form,
                child: Column(children: [
                  const SizedBox(height: 8),
                  for (final e in _controllers.entries)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 14),
                      child: TextFormField(
                        key: ValueKey(e.key),
                        controller: e.value,
                        enabled: _loaded && !_busy,
                        keyboardType: const TextInputType.numberWithOptions(
                            decimal: true),
                        autovalidateMode: AutovalidateMode.onUserInteraction,
                        decoration: InputDecoration(
                            labelText: timingLabels[e.key],
                            suffixText: 'seconds',
                            border: const OutlineInputBorder(),
                            helperText: e.key == 'return_s'
                                ? 'Measure travel from lettuce 4 back to lettuce 1.'
                                : null,
                            helperMaxLines: 3),
                        validator: (text) {
                          final n = double.tryParse(text?.trim() ?? '');
                          return n == null ||
                                  !n.isFinite ||
                                  n < 0 ||
                                  (n == 0 && e.key != 'initial_delay_s')
                              ? 'Enter ${e.key == 'initial_delay_s' ? 'zero or positive' : 'positive'} seconds'
                              : null;
                        },
                        onChanged: (_) => _save(),
                      ),
                    ),
                ]))
          ],
        ),
        if (_error != null)
          Text(_error!, style: TextStyle(color: farm?.pest ?? Colors.red)),
        if (widget.api != null) ...[
          Text(
              _running
                  ? 'Stop scanning here and stop the separate motor controller separately.'
                  : 'Position the camera at lettuce 1 lower. Synchronize the separate motor controller when starting.',
              style: TextStyle(color: farm?.muted)),
          const SizedBox(height: 10),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: _running ? (farm?.pest ?? Colors.red) : accent,
                foregroundColor: Colors.white),
            onPressed: !_loaded || !_supported || !_clock.connected || _busy
                ? null
                : () => _command(_running),
            child: Text(_busy
                ? 'Please wait…'
                : _running
                    ? 'Stop scan'
                    : 'Apply timings & start scan'),
          ),
        ],
      ]),
    );
  }
}
