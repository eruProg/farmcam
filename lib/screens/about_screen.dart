/// about_screen.dart — System configuration & hardware overview
///
/// Fetches /api/config on open and displays cycle timing, confidence
/// thresholds, verifier gate parameters, and hardware settings.
/// Read-only; no controls.
library;

import 'dart:math';
import 'package:flutter/material.dart';
import '../services/api_service.dart';

// ── Design tokens ────────────────────────────────────────────────
const _bg     = Color(0xFFFAFAF9);
const _card   = Color(0xFFFFFFFF);
const _border = Color(0xFFE4E4E7);
const _fg     = Color(0xFF18181B);
const _muted  = Color(0xFF71717A);
const _green  = Color(0xFF16A34A);
const _amber  = Color(0xFFD97706);
// ─────────────────────────────────────────────────────────────────

class AboutScreen extends StatefulWidget {
  final ApiService api;

  const AboutScreen({super.key, required this.api});

  @override
  State<AboutScreen> createState() => _AboutScreenState();
}

class _AboutScreenState extends State<AboutScreen> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic> _cfg = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final data = await widget.api.fetchConfig();
      if (mounted) {
        setState(() {
          _cfg = data ?? {};
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() { _loading = false; _error = e.toString(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _card,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded, color: _fg),
          onPressed: () => Navigator.pop(context),
        ),
        title: const Text(
          'System Overview',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: _fg),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded, color: _muted, size: 20),
            onPressed: _load,
          ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(1),
          child: Container(height: 1, color: _border),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.wifi_off_rounded, color: _muted, size: 36),
                      const SizedBox(height: 12),
                      Text('Could not load config', style: const TextStyle(color: _fg, fontWeight: FontWeight.w600)),
                      Text(_error!, style: const TextStyle(color: _muted, fontSize: 12)),
                      const SizedBox(height: 16),
                      TextButton(onPressed: _load, child: const Text('Retry')),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(20),
                  children: [
                    _Section(
                      title: 'Scan Cycle',
                      icon: Icons.timer_outlined,
                      children: [
                        _Row('Detect window',     '${_v("detect_window_secs")} s × 2'),
                        _Row('Motor transit down', '${_v("transit_down_secs")} s'),
                        _Row('Motor transit up',   '${_v("transit_up_secs")} s'),
                        _Row('Inter-station move', '${_v("move_secs")} s (×3)'),
                        _Row('Loop-back (St. 4)', '${_v("loop_after_4_secs")} s'),
                        _Row('Settle after move',  '${_v("settle_secs")} s'),
                        const Divider(height: 20, color: _border),
                        _Row('Full cycle time', _cycleSecs(), highlight: true),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Section(
                      title: 'Confidence Thresholds',
                      icon: Icons.tune_rounded,
                      children: [
                        for (final e in (_cfg['per_class_thresholds'] as Map? ?? {}).entries)
                          _Row(_capitalize(e.key), '${((e.value as num) * 100).round()}%'),
                        _Row('Fallback (other)', '${((_cfg['confidence_threshold'] as num? ?? 0.35) * 100).round()}%'),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Section(
                      title: 'Verifier Gates',
                      icon: Icons.filter_alt_outlined,
                      children: [
                        _Row('Skin rejection (σ_max)', '${((_cfg['sigma_max'] as num? ?? 0.3) * 100).round()}% bbox'),
                        _Row('Frame skin suppression', '${((_cfg['skin_frame_max_pct'] as num? ?? 0.12) * 100).round()}% of frame'),
                        _Row('Whitefly green ring', '≥${((_cfg['green_ratio_min'] as num? ?? 0.15) * 100).round()}%'),
                        _Row('Temporal confirm (K/N)', '${_v("track_k")} / ${_v("track_n")} frames'),
                        _Row('Temporal confirm (T_min)', '${_v("t_min_secs")} s'),
                        _Row('Centroid drift max', '${_v("centroid_drift_max")} px/s'),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Section(
                      title: 'Actuator Cooldowns',
                      icon: Icons.water_drop_outlined,
                      children: [
                        _Row('Pump cooldown',  '${_v("pump_cooldown_s")} s'),
                        _Row('Pump duration',  '${_v("pump_duration_s")} s'),
                        _Row('Spray min hits', '${_v("spray_min_hits")}'),
                        _Row('SMS cooldown',   '${_v("sms_cooldown_s")} s'),
                        _Row('SMS max retries','${_v("sms_max_retries")}'),
                        _Row('Buzzer interval','${_v("buzz_min_interval_s")} s'),
                        _Row('Buzzer class CD','${_v("buzz_class_cooldown_s")} s'),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Section(
                      title: 'Severity Thresholds',
                      icon: Icons.warning_amber_outlined,
                      children: [
                        _Row('Pest HIGH ≥',           '${_v("sev_pest_high")} insects'),
                        _Row('Pest MEDIUM ≥',         '${_v("sev_pest_medium")} insects'),
                        _Row('Fungus HIGH ≥',         '${_v("sev_fungus_high_pct")}% coverage'),
                        _Row('Fungus MEDIUM ≥',       '${_v("sev_fungus_med_pct")}% coverage'),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Section(
                      title: 'Hardware',
                      icon: Icons.settings_input_hdmi_outlined,
                      children: [
                        _Row('Buzzer GPIO (BCM)', '${_v("buzzer_pin")}'),
                        _Row('Pump GPIO (BCM)',   '${_v("pump_pin")}'),
                        _Row('ESP32 address',     '${_cfg["esp32_ip"] ?? "–"}:${_v("esp32_port")}'),
                        _Row('Snapshot retention','${_v("retention_max_gb")} GB / ${_v("retention_max_days")} days'),
                        _Row('Station order',     (_cfg['lettuce_sequence'] as List? ?? []).join(' → ')),
                      ],
                    ),
                    const SizedBox(height: 32),
                  ],
                ),
    );
  }

  String _v(String key) {
    final v = _cfg[key];
    if (v == null) return '–';
    if (v is double) {
      return v == v.truncateToDouble() ? v.toInt().toString() : v.toStringAsFixed(1);
    }
    return v.toString();
  }

  String _cycleSecs() {
    final s = (_cfg['cycle_time_secs'] as num?)?.toDouble() ?? 325.0;
    final m = (s / 60).floor();
    final sec = (s % 60).round();
    return '${s.toInt()} s (${m}m ${sec}s)';
  }

  static String _capitalize(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

// ── Section widget ────────────────────────────────────────────────

class _Section extends StatelessWidget {
  final String title;
  final IconData icon;
  final List<Widget> children;

  const _Section({required this.title, required this.icon, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
            child: Row(children: [
              Icon(icon, size: 15, color: _green),
              const SizedBox(width: 6),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 12, fontWeight: FontWeight.w700,
                  color: _fg, letterSpacing: 0.3,
                ),
              ),
            ]),
          ),
          const SizedBox(height: 8),
          ...children,
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String value;
  final bool highlight;

  const _Row(this.label, this.value, {this.highlight = false});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: const TextStyle(fontSize: 12, color: _muted),
            ),
          ),
          Text(
            value,
            style: TextStyle(
              fontSize: 12,
              fontWeight: highlight ? FontWeight.w700 : FontWeight.w500,
              color: highlight ? _green : _fg,
            ),
          ),
        ],
      ),
    );
  }
}
