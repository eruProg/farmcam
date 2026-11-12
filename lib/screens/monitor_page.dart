import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/live_card.dart';
import '../widgets/scan_progress.dart';
import '../widgets/station_card.dart';
import '../widgets/track_sheet.dart';
import '../widgets/ui.dart';
import 'station_detail_page.dart';

/// Live scan progress, the four lettuces, and the newest detections.
/// No camera feed: every photo here is a saved detection snapshot.
class MonitorPage extends StatelessWidget {
  const MonitorPage({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return RefreshIndicator(
      onRefresh: controller.refresh,
      child: CustomScrollView(slivers: [
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(Space.lg, Space.lg, Space.lg, 0),
          sliver: SliverList.list(children: [
            Text('Monitoring', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: Space.md),
            const ScanHero(),
            ValueListenableBuilder<AppSnapshot?>(
              valueListenable: controller.snapshot,
              builder: (context, snap, _) => snap?.live == null
                  ? const SizedBox.shrink()
                  : Padding(padding: const EdgeInsets.only(top: Space.md), child: LiveCard(live: snap!.live!)),
            ),
            const SizedBox(height: Space.xl),
            Text('LETTUCES', style: Theme.of(context).textTheme.labelSmall?.copyWith(color: farm.muted)),
            const SizedBox(height: Space.sm),
          ]),
        ),
        ValueListenableBuilder<AppSnapshot?>(
          valueListenable: controller.snapshot,
          builder: (context, snap, _) {
            if (snap == null) {
              return const SliverPadding(
                padding: EdgeInsets.symmetric(horizontal: Space.lg),
                sliver: SliverToBoxAdapter(child: SkeletonBox(height: 220)),
              );
            }
            return SliverPadding(
              padding: const EdgeInsets.symmetric(horizontal: Space.lg),
              sliver: SliverLayoutBuilder(builder: (context, constraints) {
                final scale = MediaQuery.textScalerOf(context).scale(1);
                // Width available per unit of text size decides the columns, so
                // large text on a phone gets one roomy column instead of two cramped ones.
                final room = constraints.crossAxisExtent / scale;
                final columns = room > 700 ? 4 : room > 300 ? 2 : 1;
                return SliverGrid.builder(
                  gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: columns,
                    mainAxisSpacing: Space.md,
                    crossAxisSpacing: Space.md,
                    mainAxisExtent: 250 + (scale - 1) * 120,
                  ),
                  itemCount: snap.stations.length,
                  itemBuilder: (context, i) {
                    final station = snap.stations[i];
                    return StationCard(
                      key: ValueKey('station_${station.id}'),
                      station: station,
                      live: snap.live,
                      onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => StationDetailPage(stationId: station.id))),
                    );
                  },
                );
              }),
            );
          },
        ),
        SliverPadding(
          padding: const EdgeInsets.all(Space.lg),
          sliver: SliverToBoxAdapter(
            child: ValueListenableBuilder<AppSnapshot?>(
              valueListenable: controller.snapshot,
              builder: (context, snap, _) => SectionCard(
                title: 'Latest detections',
                icon: Icons.notifications_active_outlined,
                child: snap == null || snap.recent.isEmpty
                    ? const EmptyState(
                        icon: Icons.eco_outlined,
                        title: 'No detections yet',
                        message: 'Confirmed pests and fungus appear here with their photo.')
                    : Column(children: [for (final t in snap.recent) TrackTile(track: t)]),
              ),
            ),
          ),
        ),
      ]),
    );
  }
}

/// Row for one detection track with its close-up photo. Tapping opens the
/// detection sheet (both photos, details, review).
class TrackTile extends StatelessWidget {
  const TrackTile({super.key, required this.track, this.onChanged});
  final Track track;
  /// Called after the detection was reviewed, so the list can reload.
  final VoidCallback? onChanged;
  @override
  Widget build(BuildContext context) {
    final api = FarmCamScope.of(context).api;
    final farm = FarmColors.of(context);
    final t = track;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      onTap: () => showTrackSheet(context, t, onChanged: onChanged),
      leading: SizedBox(
        width: 64,
        height: 48,
        child: SnapshotImage(t.thumbPath == null ? null : api.snapshotUrl(t.thumbPath!), radius: 8, zoomable: false),
      ),
      title: Row(children: [
        Flexible(
          child: Text(classLabel(t.cls),
              overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
        ),
        const SizedBox(width: Space.sm),
        StatusPill(trackAmount(t), color: farm.forClass(t.cls)),
      ]),
      subtitle: Text(trackSubtitle(t)),
      trailing: t.falsePositive
          ? Tooltip(message: 'False alarm', child: Icon(Icons.flag_outlined, color: farm.muted))
          : const Icon(Icons.chevron_right_rounded),
    );
  }
}
