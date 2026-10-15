/// cooldown_chip.dart — Animated cooldown status chip
///
/// Shows remaining cooldown time for pump and SMS as a tiny pill chip
/// with a draining arc. When cooldown expires it turns green.
///
/// Usage:
///   CooldownChip(label: 'Pump', remainingS: 240, totalS: 300)
library;

import 'dart:math';
import 'package:flutter/material.dart';

const _fg     = Color(0xFFFAFAFA);
const _muted  = Color(0xFF71717A);
const _green  = Color(0xFF22C55E);
const _amber  = Color(0xFFF59E0B);
const _red    = Color(0xFFEF4444);
const _border = Color(0xFF27272A);
const _card   = Color(0xFF18181B);

class CooldownChip extends StatefulWidget {
  final String label;
  final double remainingS;
  final double totalS;
  final bool   compact;

  const CooldownChip({
    super.key,
    required this.label,
    required this.remainingS,
    required this.totalS,
    this.compact = false,
  });

  @override
  State<CooldownChip> createState() => _CooldownChipState();
}

class _CooldownChipState extends State<CooldownChip>
    with SingleTickerProviderStateMixin {

  late AnimationController _arcCtrl;
  late Animation<double> _arcAnim;
  double _displayRemaining = 0.0;

  @override
  void initState() {
    super.initState();
    _displayRemaining = widget.remainingS;
    _arcCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    );
    _arcAnim = Tween<double>(begin: _fraction(), end: _fraction()).animate(
      CurvedAnimation(parent: _arcCtrl, curve: Curves.easeOut),
    );
    _arcCtrl.forward();
  }

  @override
  void didUpdateWidget(CooldownChip old) {
    super.didUpdateWidget(old);
    if (old.remainingS != widget.remainingS) {
      _arcAnim = Tween<double>(
        begin: _arcAnim.value,
        end: _fraction(),
      ).animate(CurvedAnimation(parent: _arcCtrl, curve: Curves.easeOut));
      _displayRemaining = widget.remainingS;
      _arcCtrl.forward(from: 0);
    }
  }

  double _fraction() =>
      widget.totalS <= 0 ? 0 : (1 - widget.remainingS / widget.totalS).clamp(0.0, 1.0);

  Color get _arcColor {
    if (widget.remainingS <= 0) return _green;
    final ratio = widget.remainingS / max(widget.totalS, 1);
    if (ratio > 0.5) return _amber;
    return _red;
  }

  bool get _ready => widget.remainingS <= 0;

  @override
  void dispose() { _arcCtrl.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    final String timeStr = _ready ? 'Ready' : _formatS(widget.remainingS);

    return AnimatedBuilder(
      animation: _arcAnim,
      builder: (context, _) => Container(
        padding: widget.compact
            ? const EdgeInsets.symmetric(horizontal: 8, vertical: 4)
            : const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: _ready ? _green.withOpacity(0.10) : _card,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: _ready ? _green.withOpacity(0.3) : _border,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Arc clock
            SizedBox(
              width: 14, height: 14,
              child: CustomPaint(
                painter: _ArcPainter(
                  fraction: _arcAnim.value,
                  color: _arcColor,
                  ready: _ready,
                ),
              ),
            ),
            const SizedBox(width: 5),
            Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  widget.label,
                  style: const TextStyle(fontSize: 9, color: _muted,
                      fontWeight: FontWeight.w600, letterSpacing: 0.5),
                ),
                Text(
                  timeStr,
                  style: TextStyle(
                    fontSize: 11,
                    color: _ready ? _green : _fg,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  static String _formatS(double s) {
    final m = (s / 60).floor();
    final sec = (s % 60).round();
    if (m > 0) return '${m}m ${sec.toString().padLeft(2, '0')}s';
    return '${s.round()}s';
  }
}

class _ArcPainter extends CustomPainter {
  final double fraction;
  final Color  color;
  final bool   ready;

  const _ArcPainter({required this.fraction, required this.color, required this.ready});

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final r  = min(cx, cy) - 1;

    // Track
    final trackPaint = Paint()
      ..color = color.withOpacity(0.15)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.8
      ..strokeCap = StrokeCap.round;
    canvas.drawCircle(Offset(cx, cy), r, trackPaint);

    // Arc
    final arcPaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.8
      ..strokeCap = StrokeCap.round;

    // Draw consumed portion (clockwise from top)
    canvas.drawArc(
      Rect.fromCircle(center: Offset(cx, cy), radius: r),
      -pi / 2,
      2 * pi * fraction,
      false,
      arcPaint,
    );

    // Center dot
    if (ready) {
      canvas.drawCircle(Offset(cx, cy), 2, Paint()..color = color);
    }
  }

  @override
  bool shouldRepaint(_ArcPainter old) =>
      old.fraction != fraction || old.color != color || old.ready != ready;
}
