import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/ui.dart';

/// Read-only view of how the Pi is configured: health, model setup,
/// reviewed precision, scan cycle, thresholds, cooldowns and hardware.
class SystemPage extends StatefulWidget {
  const SystemPage({super.key});
  @override
  State<SystemPage> createState() => _SystemPageState();
}

class _SystemPageState extends State<SystemPage> {
  Map<String, dynamic> _cfg = {}, _precision = {};
  bool _loading = true;
  String? _error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_loading && _cfg.isEmpty && _error == null) _load();
  }

  Future<void> _load() async {
    final api = FarmCamScope.of(context).api;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final cfg = await api.fetchConfig();
      Map<String, dynamic> precision = {};
      try {
        precision = await api.fetchPrecision();
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _cfg = cfg;
        _precision = precision;
        _loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Could not load the Pi configuration.';
        });
      }
    }
  }

  String _seconds(String key) {
    final v = _cfg[key];
    return v is num ? durationText(v.toDouble()) : '–';
  }

  String _v(String key, [String unit = '']) {
    final v = _cfg[key];
    if (v == null) return '–';
    final s = v is double && v == v.truncateToDouble() ? v.toInt().toString() : '$v';
    return unit.isEmpty ? s : '$s $unit';
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final snap = controller.snapshot.value;
    return Scaffold(
      appBar: AppBar(title: const Text('System overview'), actions: [
        IconButton(tooltip: 'Refresh', onPressed: _load, icon: const Icon(Icons.refresh_rounded)),
      ]),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? EmptyState(icon: Icons.wifi_off_rounded, title: _error!, actionLabel: 'Retry', onAction: _load)
              : ListView(padding: const EdgeInsets.all(Space.lg), children: [
                  _Group('Machine', Icons.memory_rounded, [
                    ('Server build', snap?.build ?? '–'),
                    ('Models loaded', snap == null ? '–' : snap.health.models.isEmpty ? 'none' : snap.health.models.join(' + ')),
                    ('Model colours', snap?.modelColors ?? '–'),
                    ('Camera', snap == null ? '–' : snap.health.cameraOk ? 'OK' : 'No frames'),
                    ('ESP32', snap == null ? '–' : '${snap.esp32.ip} · ${snap.esp32.reachable ? 'connected' : 'not answering'}'),
                    ('Pi address', '${controller.link.value.ip}:${controller.link.value.port}'),
                  ]),
                  _Group('Precision of reviewed detections', Icons.fact_check_outlined, [
                    if (_precision.isEmpty) ('Reviewed', 'none yet'),
                    for (final e in _precision.entries)
                      (classLabel(e.key), e.value == null ? '–' : '${((e.value as num) * 100).toStringAsFixed(1)}%'),
                  ]),
                  _Group('Scan cycle', Icons.timer_outlined, [
                    ('Scan each part', _v('detect_window_secs', 's')),
                    ('Lower → upper', _v('transit_up_secs', 's')),
                    ('Upper → lower', _v('transit_down_secs', 's')),
                    ('Lettuce 1 → 2', _v('move_secs', 's')),
                    ('Lettuce 2 → 3', _v('travel_2_3_s', 's')),
                    ('Lettuce 3 → 4', _v('travel_3_4_s', 's')),
                    ('Return 4 → 1', _v('loop_after_4_secs', 's')),
                    ('Full cycle', _cfg['cycle_time_secs'] == null ? 'set the return time first' : mmss((_cfg['cycle_time_secs'] as num).toDouble())),
                  ]),
                  _Group('Detection thresholds', Icons.tune_rounded, [
                    for (final e in (_cfg['per_class_thresholds'] as Map? ?? {}).entries)
                      (classLabel('${e.key}'), '${((e.value as num) * 100).round()}%'),
                    ('Confirm (frames)', '${_v('track_k')} of ${_v('track_n')}'),
                  ]),
                  _Group('Actuators', Icons.water_drop_outlined, [
                    ('Auto-spray cooldown (whole machine)', _seconds('pump_cooldown_s')),
                    ('Spray duration', _v('pump_duration_s', 's')),
                    ('Spray when', _cfg['spray_whiteflies_min'] == null
                        ? '–'
                        : '≥ ${_v('spray_whiteflies_min')} whiteflies or ≥ ${_v('spray_worms_min')} worms per lettuce'),
                    ('SMS cooldown', _seconds('sms_cooldown_s')),
                    ('Buzzer min interval', _v('buzz_min_interval_s', 's')),
                  ]),
                  _Group('Severity', Icons.warning_amber_rounded, [
                    ('Pest high', '≥ ${_v('sev_pest_high')} insects'),
                    ('Pest medium', '≥ ${_v('sev_pest_medium')} insects'),
                    ('Fungus high', '≥ ${_v('sev_fungus_high_pct')}% leaf'),
                    ('Fungus medium', '≥ ${_v('sev_fungus_med_pct')}% leaf'),
                  ]),
                  _Group('Hardware & storage', Icons.settings_input_hdmi_outlined, [
                    ('Pump pin (ESP32)', _v('pump_pin')),
                    ('Buzzer pin (ESP32)', _v('buzzer_pin')),
                    ('Snapshot retention', '${_v('retention_max_gb')} GB · ${_v('retention_max_days')} days'),
                    ('Station order', (_cfg['lettuce_sequence'] as List? ?? []).join(' → ')),
                  ]),
                  Text('Settings are changed in config.py on the Pi.', style: TextStyle(color: farm.muted)),
                ]),
    );
  }
}

class _Group extends StatelessWidget {
  const _Group(this.title, this.icon, this.rows);
  final String title;
  final IconData icon;
  final List<(String, String)> rows;
  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: Space.md),
      child: SectionCard(
        title: title,
        icon: icon,
        child: Column(children: [
          for (final (label, value) in rows)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: Text(label, style: TextStyle(color: farm.muted))),
                const SizedBox(width: Space.md),
                Flexible(child: Text(value, textAlign: TextAlign.right, style: const TextStyle(fontWeight: FontWeight.w600))),
              ]),
            ),
        ]),
      ),
    );
  }
}
