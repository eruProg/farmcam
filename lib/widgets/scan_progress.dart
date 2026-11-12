import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import 'ui.dart';

/// Odd lettuces are scanned lower → upper, even ones upper → lower, so the
/// camera never makes an extra trip (matches the Pi's scan_state.py).
List<String> visitOrder(int station) => station.isOdd ? const ['lower', 'upper'] : const ['upper', 'lower'];

String nextStep(ScanInfo scan) {
  final s = scan.station;
  final order = visitOrder(s);
  switch (scan.phase) {
    case 'INITIAL_DELAY':
      return 'Next: scan lettuce 1 lower';
    case 'DETECT_LOWER':
    case 'DETECT_UPPER':
      final current = scan.phase == 'DETECT_LOWER' ? 'lower' : 'upper';
      if (current == order.first) return 'Next: ${order.first == 'lower' ? 'raise' : 'lower'} the camera';
      return s >= 4 ? 'Next: return to lettuce 1' : 'Next: move to lettuce ${s + 1}';
    case 'TRANSIT_TO_UPPER':
      return 'Next: scan upper part';
    case 'TRANSIT_TO_LOWER':
      return 'Next: scan lower part';
    case 'MOVING_TO_STATION':
      return 'Next: scan lettuce $s ${order.first}';
    case 'RETURNING':
      return 'Next: start again at lettuce 1';
    default:
      return 'Start a scan from Controls';
  }
}

/// Rebuilds only itself on every frame while a scan runs, so the countdown
/// and ring move smoothly without redrawing the page around it.
class _Ticking extends StatefulWidget {
  const _Ticking({required this.builder});
  final WidgetBuilder builder;
  @override
  State<_Ticking> createState() => _TickingState();
}

class _TickingState extends State<_Ticking> with SingleTickerProviderStateMixin {
  late final Ticker _ticker;

  @override
  void initState() {
    super.initState();
    // Started here, not lazily: a lazy ticker was only created in dispose(),
    // so the countdown never moved.
    _ticker = createTicker((_) => setState(() {}))..start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.builder(context);
}

/// Big scan status block for the Monitoring page.
class ScanHero extends StatelessWidget {
  const ScanHero({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    return ValueListenableBuilder<AppSnapshot?>(
      valueListenable: controller.snapshot,
      builder: (context, snap, _) {
        if (snap == null) return const SectionCard(child: SkeletonBox(height: 120));
        final scan = snap.scan;
        final farm = FarmColors.of(context);
        final text = Theme.of(context).textTheme;
        return SectionCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              SizedBox(
                width: 96,
                height: 96,
                child: scan.running
                    ? _Ticking(
                        builder: (context) => _Ring(
                            progress: controller.clock.progress,
                            color: scan.detecting ? farm.healthy : farm.info,
                            track: farm.subtle,
                            label: mmss(controller.clock.remaining)))
                    : _Ring(progress: 0, color: farm.muted, track: farm.subtle, label: '–'),
              ),
              const SizedBox(width: Space.lg),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  StatusPill(scan.running ? (scan.detecting ? 'Detecting' : 'Moving') : 'Idle',
                      color: scan.running ? (scan.detecting ? farm.healthy : farm.info) : farm.muted,
                      icon: scan.running ? Icons.radar_rounded : Icons.pause_circle_outline_rounded),
                  const SizedBox(height: Space.sm),
                  Text(scan.running ? 'Lettuce ${scan.station}' : 'Scan not running',
                      style: text.headlineSmall),
                  const SizedBox(height: 2),
                  Text(scan.running ? scan.label : 'Start the scan in Controls when the motor is in position.',
                      style: TextStyle(color: farm.muted)),
                ]),
              ),
            ]),
            const SizedBox(height: Space.lg),
            StationStepper(scan: scan),
            if (scan.running) ...[
              const SizedBox(height: Space.md),
              Row(children: [
                Icon(Icons.arrow_forward_rounded, size: 16, color: farm.muted),
                const SizedBox(width: 6),
                Expanded(child: Text(nextStep(scan), style: TextStyle(color: farm.muted))),
              ]),
            ],
          ]),
        );
      },
    );
  }
}

class _Ring extends StatelessWidget {
  const _Ring({required this.progress, required this.color, required this.track, required this.label});
  final double progress;
  final Color color, track;
  final String label;
  @override
  Widget build(BuildContext context) => CustomPaint(
        painter: _RingPainter(progress, color, track),
        child: Center(
          child: Text(label,
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontFeatures: const [FontFeature.tabularFigures()])),
        ),
      );
}

class _RingPainter extends CustomPainter {
  _RingPainter(this.progress, this.color, this.track);
  final double progress;
  final Color color, track;
  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final stroke = size.width * 0.09;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.round;
    final r = rect.deflate(stroke / 2);
    canvas.drawArc(r, 0, 2 * pi, false, paint..color = track);
    if (progress > 0) canvas.drawArc(r, -pi / 2, 2 * pi * progress, false, paint..color = color);
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.progress != progress || old.color != color || old.track != track;
}

/// Lettuce 1-4 with their two parts in visit order: done, current, upcoming.
class StationStepper extends StatelessWidget {
  const StationStepper({super.key, required this.scan});
  final ScanInfo scan;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final current = scan.running ? scan.station : 0;
    final detectingPart = scan.phase == 'DETECT_LOWER'
        ? 'lower'
        : scan.phase == 'DETECT_UPPER'
            ? 'upper'
            : null;
    return Row(children: [
      for (var s = 1; s <= 4; s++) ...[
        Expanded(
          child: Column(children: [
            Row(children: [
              for (final (i, part) in visitOrder(s).indexed) ...[
                Expanded(
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    height: 8,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(4),
                      color: _partColor(farm, s, current, part, i, detectingPart),
                    ),
                  ),
                ),
                if (i == 0) const SizedBox(width: 3),
              ],
            ]),
            const SizedBox(height: 6),
            Text('L$s',
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: s == current ? FontWeight.w700 : FontWeight.w500,
                    color: s == current ? Theme.of(context).colorScheme.onSurface : farm.muted)),
          ]),
        ),
        if (s < 4) const SizedBox(width: Space.sm),
      ],
    ]);
  }

  Color _partColor(FarmColors farm, int s, int current, String part, int index, String? detecting) {
    if (current == 0) return farm.subtle;
    if (s < current) return farm.healthy.withOpacity(0.55);
    if (s > current) return farm.subtle;
    final order = visitOrder(s);
    final firstDone = scan.phase != 'MOVING_TO_STATION' && scan.phase != 'INITIAL_DELAY' &&
        (detecting == order.last || scan.phase.startsWith('TRANSIT'));
    if (part == detecting) return farm.healthy;
    if (index == 0 && firstDone) return farm.healthy.withOpacity(0.55);
    return farm.border;
  }
}

/// One-line live status for the Home card ("Lettuce 2 · upper · 0:41 left").
class ScanLine extends StatelessWidget {
  const ScanLine({super.key, this.style});
  final TextStyle? style;
  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    return ValueListenableBuilder<AppSnapshot?>(
      valueListenable: controller.snapshot,
      builder: (context, snap, _) {
        final scan = snap?.scan;
        if (scan == null || !scan.running) {
          return Text(snap == null ? 'Waiting for the Pi…' : 'Scan idle', style: style);
        }
        return _Ticking(
          builder: (_) => Text(
            'Lettuce ${scan.station} · ${scan.position.isEmpty ? scan.label : scan.position} · '
            '${mmss(controller.clock.remaining)} left',
            style: style?.copyWith(fontFeatures: const [FontFeature.tabularFigures()]),
          ),
        );
      },
    );
  }
}
