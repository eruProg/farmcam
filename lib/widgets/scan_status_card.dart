/// scan_status_card.dart — Live scan phase progress widget
///
/// Displays the current 7-phase scan cycle status with:
///   - Station indicator (1-4 active)
///   - Phase label + position (Upper / Lower)
///   - Countdown progress bar (self-advancing every 250 ms)
///   - Phase step dots (visual stepper)
///
/// The card polls /api/system every 2 s for the server-authoritative
/// state dict, but drives its own smooth progress bar between polls
/// using a local 250 ms timer (no jitter visible to the user).
///
/// Import:
///   import '../widgets/scan_status_card.dart';
library;

import 'dart:async';
import '../data/scan_state_model.dart';

import 'package:flutter/material.dart';

import '../services/api_service.dart';

// ── Colors (must match dashboard palette) ───────────────────────
const _card = Color(0xFFFFFFFF);
const _border = Color(0xFFE4E4E7);
const _fg = Color(0xFF18181B);
const _muted = Color(0xFF71717A);
const _green = Color(0xFF16A34A);
const _amber = Color(0xFFD97706);
const _blue = Color(0xFF3B82F6);

// ── Phase ordering for the stepper dots ─────────────────────────
const _phaseOrder = [
  'DETECT_UPPER',
  'TRANSIT_TO_LOWER',
  'DETECT_LOWER',
  'TRANSIT_TO_UPPER',
  'MOVING_TO_STATION',
];

class ScanStatusCard extends StatefulWidget {
  final ApiService api;
  final bool demoMode;

  const ScanStatusCard({
    super.key,
    required this.api,
    this.demoMode = false,
  });

  @override
  State<ScanStatusCard> createState() => _ScanStatusCardState();
}

class _ScanStatusCardState extends State<ScanStatusCard>
    with SingleTickerProviderStateMixin {
  // Server-authoritative state
  Map<String, dynamic> _state = {};
  final _timing = ScanStateModel();
  bool _fetching = false;

  // Local smooth progress
  double _localProgress = 0.0;
  Timer? _smoothTimer;
  Timer? _pollTimer;

  // Demo mode
  int _demoPhaseIdx = 0;
  int _demoStation = 1;
  late AnimationController _demoAnim;

  @override
  void initState() {
    super.initState();
    _demoAnim = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 30),
    )..addListener(_onDemoTick);

    if (widget.demoMode) {
      _startDemo();
    } else {
      _startPoll();
    }
  }

  // ── Demo mode ────────────────────────────────────────────────────

  void _startDemo() {
    _demoState();
    _demoAnim.repeat();
  }

  void _onDemoTick() {
    final t = _demoAnim.value; // 0..1 over 30 s
    final elapsed = t * 30.0;
    final newPhaseIdx = (elapsed / 6).floor() % _phaseOrder.length;
    final inPhase = elapsed % 6;
    if (mounted) {
      setState(() {
        _demoPhaseIdx = newPhaseIdx;
        _localProgress = inPhase / 6.0;
        _demoStation = ((elapsed / 30).floor() % 4) + 1;
      });
    }
  }

  void _demoState() {
    _state = {
      'phase': _phaseOrder[_demoPhaseIdx],
      'phase_label': _phaseLabelFor(_phaseOrder[_demoPhaseIdx]),
      'position': _positionFor(_phaseOrder[_demoPhaseIdx]),
      'station': _demoStation,
      'scan_id': 'demo-scan',
      'remaining_s': (6.0 * (1 - _localProgress)).round(),
      'is_detecting': _phaseOrder[_demoPhaseIdx].startsWith('DETECT'),
      'phase_duration_s': 6.0,
    };
  }

  // ── Live poll ─────────────────────────────────────────────────────

  void _startPoll() {
    _pollTimer =
        Timer.periodic(const Duration(seconds: 2), (_) => _fetchState());
    _fetchState();
    _smoothTimer = Timer.periodic(const Duration(milliseconds: 250), (_) {
      if (mounted) setState(() => _localProgress = _timing.progress);
    });
  }

  Future<void> _fetchState() async {
    if (_fetching) return;
    _fetching = true;
    final sent = _timing.now;
    try {
      final data = await widget.api.getScanStatus();
      if (!mounted) return;
      if (data == null) throw const FormatException('Scan unavailable');
      setState(() {
        _timing.update(data, sent, _timing.now);
        _state = _timing.state;
        _localProgress = _timing.progress;
      });
    } catch (_) {
      if (mounted) setState(_timing.disconnect);
    } finally {
      _fetching = false;
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _smoothTimer?.cancel();
    _demoAnim.dispose();
    super.dispose();
  }

  // ── Build ─────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final phase = (widget.demoMode
            ? _phaseOrder[_demoPhaseIdx]
            : _state['phase'] as String?) ??
        'IDLE';
    final phaseLabel = (widget.demoMode
            ? _phaseLabelFor(phase)
            : _state['phase_label'] as String?) ??
        'Idle';
    final label =
        !widget.demoMode && !_timing.connected ? 'Disconnected' : phaseLabel;
    final position = (widget.demoMode
            ? _positionFor(phase)
            : _state['position'] as String?) ??
        'upper';
    final station = (widget.demoMode
            ? _demoStation
            : (_state['station'] as num?)?.toInt()) ??
        1;
    final remaining = (widget.demoMode
            ? (6.0 * (1 - _localProgress)).roundToDouble()
            : _timing.remaining)
        .clamp(0.0, 9999.0);
    final isDetecting = (widget.demoMode
            ? phase.startsWith('DETECT')
            : _state['is_detecting'] as bool?) ??
        false;

    final barColor =
        isDetecting ? _green : (phase.contains('TRANSIT') ? _amber : _blue);

    return Container(
      margin: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header row
          Row(
            children: [
              _PhaseDot(active: isDetecting),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  label,
                  style: const TextStyle(
                    color: _fg,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              _StationChips(activeStation: station),
            ],
          ),

          const SizedBox(height: 8),

          // ── Progress bar
          _SmoothProgressBar(progress: _localProgress, color: barColor),

          const SizedBox(height: 6),

          // ── Sub-row
          Row(
            children: [
              _InfoChip(
                icon: Icons.vertical_align_top_rounded,
                label: _capitalise(position),
                color: position == 'lower' ? _amber : _green,
              ),
              const SizedBox(width: 6),
              if (isDetecting)
                _InfoChip(
                    icon: Icons.timer_outlined,
                    label: '${remaining.toStringAsFixed(0)}s',
                    color: _muted),
              const Spacer(),
              // Phase step dots
              _PhaseStepper(currentPhase: phase),
            ],
          ),
        ],
      ),
    );
  }

  static String _phaseLabelFor(String phase) {
    const m = {
      'IDLE': 'Idle',
      'MOVING_TO_STATION': 'Moving to next station',
      'SETTLING': 'Settling…',
      'DETECT_UPPER': 'Detecting — Upper',
      'TRANSIT_TO_LOWER': 'Lowering camera',
      'TRANSIT_TO_UPPER': 'Raising camera',
      'DETECT_LOWER': 'Detecting — Lower',
    };
    return m[phase] ?? phase;
  }

  static String _positionFor(String phase) =>
      phase == 'DETECT_LOWER' ? 'lower' : 'upper';

  static String _capitalise(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

// ── Sub-widgets ───────────────────────────────────────────────────

class _PhaseDot extends StatefulWidget {
  final bool active;
  const _PhaseDot({required this.active});

  @override
  State<_PhaseDot> createState() => _PhaseDotState();
}

class _PhaseDotState extends State<_PhaseDot>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;
  late Animation<double> _opacity;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 900))
      ..repeat(reverse: true);
    _opacity = Tween<double>(begin: 0.3, end: 1.0).animate(_ctrl);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.active ? _green : _blue;
    return FadeTransition(
      opacity: _opacity,
      child: Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(color: color.withValues(alpha: 0.4), blurRadius: 4)
            ]),
      ),
    );
  }
}

class _SmoothProgressBar extends StatelessWidget {
  final double progress;
  final Color color;
  const _SmoothProgressBar({required this.progress, required this.color});

  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: TweenAnimationBuilder<double>(
          tween: Tween<double>(begin: progress, end: progress),
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
          builder: (_, v, __) => LinearProgressIndicator(
            value: v.clamp(0.0, 1.0),
            minHeight: 5,
            backgroundColor: _border,
            valueColor: AlwaysStoppedAnimation<Color>(color),
          ),
        ),
      );
}

class _StationChips extends StatelessWidget {
  final int activeStation;
  const _StationChips({required this.activeStation});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(4, (i) {
        final n = i + 1;
        final isActive = n == activeStation;
        return AnimatedContainer(
          duration: const Duration(milliseconds: 250),
          margin: const EdgeInsets.only(left: 4),
          width: isActive ? 22 : 16,
          height: 16,
          decoration: BoxDecoration(
            color: isActive ? _green : _border,
            borderRadius: BorderRadius.circular(3),
          ),
          child: Center(
            child: Text(
              '$n',
              style: TextStyle(
                fontSize: 9,
                fontWeight: FontWeight.w700,
                color: isActive ? Colors.black : _muted,
              ),
            ),
          ),
        );
      }),
    );
  }
}

class _PhaseStepper extends StatelessWidget {
  final String currentPhase;
  const _PhaseStepper({required this.currentPhase});

  @override
  Widget build(BuildContext context) {
    final idx = _phaseOrder.indexOf(currentPhase);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(_phaseOrder.length, (i) {
        Color c;
        if (i < idx) {
          c = _green.withValues(alpha: 0.5);
        } else if (i == idx) {
          c = _green;
        } else {
          c = _border;
        }
        return Container(
          margin: const EdgeInsets.only(left: 3),
          width: 6,
          height: 6,
          decoration: BoxDecoration(color: c, shape: BoxShape.circle),
        );
      }),
    );
  }
}

class _InfoChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;
  const _InfoChip(
      {required this.icon, required this.label, required this.color});

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 11, color: color),
          const SizedBox(width: 3),
          Text(label,
              style: TextStyle(
                  fontSize: 11, color: color, fontWeight: FontWeight.w500)),
        ],
      );
}
