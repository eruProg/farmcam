import 'package:flutter/material.dart';
import '../core/models.dart';
import '../core/theme.dart';
import 'ui.dart';

/// "Detecting now · Lettuce 2, upper part: 2 whiteflies, 1 worm". Shows what
/// the camera has confirmed (3 of 5 frames) during the current lettuce visit,
/// seconds after it happens, before the scan window ends and history is saved.
class LiveCard extends StatelessWidget {
  const LiveCard({super.key, required this.live});
  final LiveInfo live;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final found = !live.isEmpty;
    final color = found ? farm.pest : farm.healthy;
    return SectionCard(
      key: const ValueKey('live_card'),
      title: 'Detecting now',
      icon: Icons.sensors_rounded,
      trailing: StatusPill('LIVE', color: color),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('Lettuce ${live.station}, ${live.position} part',
            style: TextStyle(color: farm.muted, fontSize: 12.5)),
        const SizedBox(height: 2),
        Text(found ? live.summary : 'Nothing confirmed yet',
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, color: found ? color : null)),
        if (live.sprayReason != null) ...[
          const SizedBox(height: Space.sm),
          Row(children: [
            Icon(Icons.water_drop_rounded, size: 16, color: farm.info),
            const SizedBox(width: 6),
            Expanded(
              child: Text('Spray rule met: ${live.sprayReason}',
                  style: TextStyle(color: farm.info, fontWeight: FontWeight.w600, fontSize: 12.5)),
            ),
          ]),
        ],
      ]),
    );
  }
}
