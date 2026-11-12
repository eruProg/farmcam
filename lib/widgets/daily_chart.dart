import 'package:flutter/material.dart';
import '../core/models.dart';
import '../core/theme.dart';

/// Stacked bars per day (worm, whitefly, fungus) for the History page.
/// [days] items look like {"date": "2026-10-22", "classes": {"worm": 2}}.
class DailyChart extends StatelessWidget {
  const DailyChart({super.key, required this.days, this.height = 160});
  final List<Map<String, dynamic>> days;
  final double height;

  static const classes = ['worm', 'whiteflies', 'fungus'];

  static Color colorFor(FarmColors farm, String cls) => switch (cls) {
        'worm' => farm.pest,
        'whiteflies' || 'whitefly' => farm.info,
        _ => farm.fungus,
      };

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final text = TextStyle(color: farm.muted, fontSize: 11);
    final counts = [
      for (final d in days)
        {
          for (final c in classes)
            c: ((d['classes'] as Map?)?[c] as num? ?? 0).toInt() +
                (c == 'whiteflies' ? ((d['classes'] as Map?)?['whitefly'] as num? ?? 0).toInt() : 0)
        }
    ];
    final maxTotal = counts.fold<int>(0, (m, c) => c.values.fold(0, (a, b) => a + b) > m ? c.values.fold(0, (a, b) => a + b) : m);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(
        height: height,
        child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          for (var i = 0; i < counts.length; i++)
            Expanded(
              child: Tooltip(
                message: '${days[i]['date']}: '
                    '${classes.map((c) => '${counts[i][c]} ${classLabel(c).toLowerCase()}').join(', ')}',
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 2),
                  child: _Bar(counts: counts[i], max: maxTotal, farm: farm),
                ),
              ),
            ),
        ]),
      ),
      const SizedBox(height: 6),
      Row(children: [
        Text(_short(days.isEmpty ? '' : days.first['date'] as String), style: text),
        const Spacer(),
        Text(maxTotal == 0 ? 'No detections' : 'Max $maxTotal / day', style: text),
        const Spacer(),
        Text(_short(days.isEmpty ? '' : days.last['date'] as String), style: text),
      ]),
      const SizedBox(height: Space.sm),
      Wrap(spacing: Space.md, children: [
        for (final c in classes)
          Row(mainAxisSize: MainAxisSize.min, children: [
            Container(width: 10, height: 10, decoration: BoxDecoration(color: colorFor(farm, c), borderRadius: BorderRadius.circular(3))),
            const SizedBox(width: 4),
            Text(classLabel(c), style: text),
          ]),
      ]),
    ]);
  }

  static String _short(String date) => date.length >= 10 ? '${date.substring(8, 10)}/${date.substring(5, 7)}' : date;
}

class _Bar extends StatelessWidget {
  const _Bar({required this.counts, required this.max, required this.farm});
  final Map<String, int> counts;
  final int max;
  final FarmColors farm;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final total = counts.values.fold(0, (a, b) => a + b);
        if (total == 0 || max == 0) {
          return Align(
              alignment: Alignment.bottomCenter,
              child: Container(height: 3, decoration: BoxDecoration(color: farm.subtle, borderRadius: BorderRadius.circular(2))));
        }
        // Leave room for the 1 px gap above each stacked segment.
        final segments = counts.values.where((n) => n > 0).length;
        final full = (c.maxHeight - segments).clamp(0.0, c.maxHeight) * total / max;
        return Column(mainAxisAlignment: MainAxisAlignment.end, children: [
          for (final cls in DailyChart.classes.reversed)
            if (counts[cls]! > 0)
              // Draws at full height at once, animates only when values change
              // (a grow-from-zero animation stalls on tabs that start hidden).
              AnimatedContainer(
                duration: const Duration(milliseconds: 400),
                curve: Curves.easeOutCubic,
                height: full * counts[cls]! / total,
                margin: const EdgeInsets.only(top: 1),
                decoration: BoxDecoration(color: DailyChart.colorFor(farm, cls), borderRadius: BorderRadius.circular(3)),
              ),
        ]);
      });
}
