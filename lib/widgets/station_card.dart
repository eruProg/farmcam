import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import 'ui.dart';

/// One lettuce: health, pest and fungus levels, and its latest detection
/// photo. Glows when the camera is at this lettuce right now.
class StationCard extends StatelessWidget {
  const StationCard({super.key, required this.station, this.onTap, this.live});
  final Station station;
  final VoidCallback? onTap;
  /// Confirmed right now during this lettuce's scan (shown as a LIVE badge).
  final LiveInfo? live;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final api = FarmCamScope.of(context).api;
    final s = station;
    final color = s.hasPest ? farm.pest : s.hasFungus ? farm.fungus : farm.healthy;
    final thumb = s.latestThumb;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 350),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(18),
        boxShadow: s.active
            ? [BoxShadow(color: farm.healthy.withOpacity(0.28), blurRadius: 14, spreadRadius: 1)]
            : const [],
      ),
      child: Card(
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: s.active ? farm.healthy : farm.border, width: s.active ? 1.6 : 1)),
        child: InkWell(
          onTap: onTap,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            // The photo takes whatever height the text leaves, so the card
            // fits its grid cell at any width and text size.
            Expanded(
              child: Stack(fit: StackFit.expand, children: [
                thumb != null
                    ? SnapshotImage(api.snapshotUrl(thumb), radius: 0, zoomable: false)
                    : Container(
                        color: farm.subtle,
                        child: Icon(s.needsAttention ? Icons.bug_report_outlined : Icons.eco_outlined,
                            color: color.withOpacity(0.7), size: 34),
                      ),
                Positioned(
                  left: 8,
                  top: 8,
                  right: s.active ? 44 : 8,
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: StatusPill(s.headline, color: color, background: farm.card.withOpacity(0.92)),
                  ),
                ),
                if (live != null && live!.station == s.id && !live!.isEmpty)
                  Positioned(
                    left: 8,
                    right: 8,
                    bottom: 8,
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: StatusPill('LIVE: ${live!.summary}',
                          key: const ValueKey('live_badge'), color: farm.pest, background: farm.card.withOpacity(0.92)),
                    ),
                  ),
                if (s.active)
                  Positioned(
                    right: 8,
                    top: 8,
                    child: Tooltip(
                      message: 'The camera is at this lettuce now',
                      child: Container(
                        padding: const EdgeInsets.all(5),
                        decoration: BoxDecoration(color: farm.healthy, shape: BoxShape.circle),
                        child: const Icon(Icons.videocam_rounded, size: 16, color: Colors.white),
                      ),
                    ),
                  ),
              ]),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(Space.md, Space.md, Space.md, Space.md),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(s.name, style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: Space.sm),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  _Level('Pest', s.pestSeverity, farm),
                  _Level('Fungus', s.fungusSeverity, farm),
                ]),
                const SizedBox(height: Space.sm),
                Text(
                  [
                    if (s.wormCount > 0) '${s.wormCount} worm${s.wormCount == 1 ? '' : 's'}',
                    if (s.whiteflyCount > 0) '${s.whiteflyCount} whitefl${s.whiteflyCount == 1 ? 'y' : 'ies'}',
                    if (s.fungusCoverage > 0) '${s.fungusCoverage.toStringAsFixed(1)}% fungus',
                    'checked ${timeAgo(s.lastScanned)}',
                  ].join(' · '),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: farm.muted, fontSize: 12.5),
                ),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Level extends StatelessWidget {
  const _Level(this.name, this.level, this.farm);
  final String name, level;
  final FarmColors farm;
  @override
  Widget build(BuildContext context) {
    final none = level == 'none' || level.isEmpty;
    return StatusPill('$name: ${none ? 'none' : level}',
        color: none ? farm.muted : farm.forSeverity(level),
        background: none ? farm.subtle : null);
  }
}
