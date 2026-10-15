import 'dart:math';
import 'package:flutter/material.dart';
import '../data/app_state.dart';
import '../services/api_service.dart';

const _bg      = Color(0xFFFAFAF9);
const _card    = Color(0xFFFFFFFF);
const _border  = Color(0xFFE4E4E7);
const _fg      = Color(0xFF18181B);
const _muted   = Color(0xFF71717A);
const _green   = Color(0xFF16A34A);
const _greenBg = Color(0xFFDCFCE7);
const _amber   = Color(0xFFD97706);
const _amberBg = Color(0xFFFEF3C7);
const _red     = Color(0xFFDC2626);
const _redBg   = Color(0xFFFEE2E2);

class LettuceDetailScreen extends StatefulWidget {
  final LettuceStation station;
  final bool demoMode;
  final ApiService? api;

  const LettuceDetailScreen({super.key, required this.station, this.demoMode = false, this.api});

  @override
  State<LettuceDetailScreen> createState() => _LettuceDetailScreenState();
}

class _LettuceDetailScreenState extends State<LettuceDetailScreen> {
  List<Detection> _history = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadHistory();
  }

  Future<void> _loadHistory() async {
    if (widget.demoMode) {
      setState(() { _history = _generateDemoHistory(); _isLoading = false; });
      return;
    }
    if (widget.api != null) {
      final history = await widget.api!.fetchHistory(widget.station.id);
      if (mounted) setState(() { _history = history; _isLoading = false; });
    } else {
      setState(() { _history = widget.station.history; _isLoading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.station;
    final hasAlert = s.status == LettuceStatus.pest || s.status == LettuceStatus.fungus || s.status == LettuceStatus.both;

    Color accent = _green;
    Color accentBg = _greenBg;
    if (s.status == LettuceStatus.pest)   { accent = _red;   accentBg = _redBg; }
    if (s.status == LettuceStatus.fungus) { accent = _amber; accentBg = _amberBg; }
    if (s.status == LettuceStatus.both)   { accent = _red;   accentBg = _redBg; }

    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        elevation: 0,
        scrolledUnderElevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_rounded, size: 18, color: _fg),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(s.name, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: _fg)),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(color: accentBg, borderRadius: BorderRadius.circular(6), border: Border.all(color: accent.withOpacity(0.3))),
              child: Text(s.statusLabel, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: accent)),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [

          // ── Status card
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(14), border: Border.all(color: _border)),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Container(
                    width: 36, height: 36,
                    decoration: BoxDecoration(color: accentBg, borderRadius: BorderRadius.circular(10)),
                    child: Icon(s.statusIcon, size: 18, color: accent),
                  ),
                  const SizedBox(width: 12),
                  Expanded(child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(s.statusLabel, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: accent)),
                      Text('Station ${s.id}', style: const TextStyle(fontSize: 12, color: _muted)),
                    ],
                  )),
                ]),

                // Infestation description (if any)
                if (hasAlert && s.infestationDesc.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  const Divider(color: _border, height: 1),
                  const SizedBox(height: 12),
                  Text(s.infestationDesc, style: const TextStyle(fontSize: 13, color: _fg, height: 1.5)),
                ],
              ],
            ),
          ),

          const SizedBox(height: 12),

          // ── Metrics row
          Row(
            children: [
              Expanded(child: _Metric(label: 'Pests Found', value: '${s.pestCount}', color: _red)),
              const SizedBox(width: 10),
              Expanded(child: _Metric(label: 'Fungus Found', value: '${s.fungusCount}', color: _amber)),
            ],
          ),
          const SizedBox(height: 10),
          // BUG FIX: this used to be one shared "Severity" metric fed by
          // severityLevel (worst-of-both) — a HIGH fungus reading could
          // hide a separate MEDIUM pest reading (or vice versa) behind a
          // single number. Pest and fungus severity are now shown as two
          // independent metrics.
          Row(
            children: [
              Expanded(child: _Metric(
                label: 'Pest Severity',
                value: s.pestSeverityLevel.isEmpty || s.pestSeverityLevel == 'none' ? '—' : s.pestSeverityLevel.toUpperCase(),
                color: s.pestSeverityLevel == 'high' ? _red : s.pestSeverityLevel == 'medium' ? _amber : _green,
              )),
              const SizedBox(width: 10),
              Expanded(child: _Metric(
                label: 'Fungus Severity',
                value: s.fungusSeverityLevel.isEmpty || s.fungusSeverityLevel == 'none' ? '—' : s.fungusSeverityLevel.toUpperCase(),
                color: s.fungusSeverityLevel == 'high' ? _red : s.fungusSeverityLevel == 'medium' ? _amber : _green,
              )),
            ],
          ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: _Metric(
              label: 'Last Check',
              value: s.lastCheck != null ? _timeAgo(s.lastCheck!) : '—',
              color: const Color(0xFF2563EB),
            ),
          ),

          // ── Fungus coverage bar
          if ((s.status == LettuceStatus.fungus || s.status == LettuceStatus.both) && s.fungusCoveragePct > 0) ...[
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12), border: Border.all(color: _border)),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                    const Text('Leaf Coverage', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: _fg)),
                    Text('${s.fungusCoveragePct.toStringAsFixed(1)}%', style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: _amber)),
                  ]),
                  const SizedBox(height: 8),
                  LinearProgressIndicator(
                    value: (s.fungusCoveragePct / 100).clamp(0.0, 1.0),
                    backgroundColor: _amberBg,
                    valueColor: const AlwaysStoppedAnimation<Color>(_amber),
                    minHeight: 6,
                    borderRadius: BorderRadius.circular(3),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    s.fungusCoveragePct >= 40 ? 'High fungal coverage — treatment recommended'
                        : s.fungusCoveragePct >= 15 ? 'Moderate fungal growth — monitor closely'
                        : 'Minor fungal spots — watch closely',
                    style: const TextStyle(fontSize: 11, color: _amber),
                  ),
                ],
              ),
            ),
          ],

          // ── Pest types
          if (hasAlert && s.pestTypes.isNotEmpty) ...[
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12), border: Border.all(color: _border)),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Detected Threats', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: _fg)),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8, runSpacing: 6,
                    children: s.pestTypes.map((t) => Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(color: _redBg, borderRadius: BorderRadius.circular(6), border: Border.all(color: _red.withOpacity(0.3))),
                      child: Text(t, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: _red)),
                    )).toList(),
                  ),
                ],
              ),
            ),
          ],

          const SizedBox(height: 20),

          // ── History header
          const Row(children: [
            Text('Detection History', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: _fg)),
          ]),
          const SizedBox(height: 10),

          if (_isLoading)
            const Center(child: Padding(padding: EdgeInsets.all(32), child: CircularProgressIndicator(color: _green, strokeWidth: 2)))
          else if (_history.isEmpty)
            Container(
              padding: const EdgeInsets.all(32),
              decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12), border: Border.all(color: _border)),
              child: const Center(
                child: Column(children: [
                  Icon(Icons.history_rounded, size: 28, color: Color(0xFFD4D4D8)),
                  SizedBox(height: 8),
                  Text('No detections recorded yet', style: TextStyle(color: _muted, fontSize: 13)),
                ]),
              ),
            )
          else
            ...(_history.map((d) => _DetectionRow(detection: d))),
        ],
      ),
    );
  }

  String _timeAgo(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inMinutes < 1) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }

  List<Detection> _generateDemoHistory() {
    final rng    = Random(widget.station.id);
    final labels = ['Whiteflies', 'Worm', 'Fungus', 'Clear', 'Whiteflies', 'Clear'];
    return List.generate(6, (i) {
      final label = labels[i % labels.length];
      return Detection(
        label: label,
        confidence: 0.7 + rng.nextDouble() * 0.29,
        timestamp: DateTime.now().subtract(Duration(hours: i * 2 + rng.nextInt(3))),
        status: Detection.parseStatus(label),
      );
    });
  }
}

// ─── Widgets ─────────────────────────────────────────────────────

class _Metric extends StatelessWidget {
  final String label;
  final String value;
  final Color color;
  const _Metric({required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12), border: Border.all(color: _border)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: color, letterSpacing: -0.5)),
          const SizedBox(height: 2),
          Text(label, style: const TextStyle(fontSize: 11, color: _muted, fontWeight: FontWeight.w500)),
        ],
      ),
    );
  }
}

class _DetectionRow extends StatelessWidget {
  final Detection detection;
  const _DetectionRow({required this.detection});

  @override
  Widget build(BuildContext context) {
    Color accent = _green;
    if (detection.status == LettuceStatus.pest)   accent = _red;
    if (detection.status == LettuceStatus.fungus) accent = _amber;
    if (detection.status == LettuceStatus.both)   accent = _red;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(10), border: Border.all(color: _border)),
      child: Row(
        children: [
          Container(
            width: 28, height: 28,
            decoration: BoxDecoration(color: accent.withOpacity(0.1), borderRadius: BorderRadius.circular(7)),
            child: Icon(_icon(detection.status), size: 14, color: accent),
          ),
          const SizedBox(width: 12),
          Expanded(child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(detection.label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: _fg)),
              Text(_formatTime(detection.timestamp), style: const TextStyle(fontSize: 11, color: _muted)),
            ],
          )),
          Text('${(detection.confidence * 100).toStringAsFixed(0)}%', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: accent)),
        ],
      ),
    );
  }

  IconData _icon(LettuceStatus s) {
    switch (s) {
      case LettuceStatus.clear:   return Icons.check_circle_outline_rounded;
      case LettuceStatus.pest:    return Icons.bug_report_outlined;
      case LettuceStatus.fungus:  return Icons.coronavirus_outlined;
      case LettuceStatus.both:    return Icons.report_problem_outlined;
      case LettuceStatus.unknown: return Icons.help_outline_rounded;
    }
  }

  String _formatTime(DateTime dt) {
    final h = dt.hour.toString().padLeft(2, '0');
    final m = dt.minute.toString().padLeft(2, '0');
    return '${dt.month}/${dt.day}  $h:$m';
  }
}
