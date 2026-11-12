import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/ui.dart';
import 'monitor_page.dart';

/// One lettuce: live status, the photo of each active threat, and its
/// detections (tap one for its photos and the Correct / false alarm review).
class StationDetailPage extends StatefulWidget {
  const StationDetailPage({super.key, required this.stationId});
  final int stationId;
  @override
  State<StationDetailPage> createState() => _StationDetailPageState();
}

class _StationDetailPageState extends State<StationDetailPage> {
  List<Track> _tracks = [];
  bool _loading = true;
  String? _error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_loading && _tracks.isEmpty && _error == null) _load();
  }

  Future<void> _load() async {
    final controller = FarmCamScope.of(context);
    if (controller.isDemo) {
      setState(() {
        _loading = false;
        _tracks = controller.snapshot.value?.recent.where((t) => t.station == widget.stationId).toList() ?? [];
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await controller.api.getTracksPage(station: widget.stationId, status: null, pageSize: 30);
      if (!mounted) return;
      setState(() {
        _tracks = [for (final t in (page['items'] as List)) Track.fromJson(Map<String, dynamic>.from(t))];
        _loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Could not load this lettuce\'s detections.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return Scaffold(
      appBar: AppBar(title: Text('Lettuce ${widget.stationId}'), actions: [
        IconButton(tooltip: 'Refresh', onPressed: _load, icon: const Icon(Icons.refresh_rounded)),
      ]),
      body: ValueListenableBuilder<AppSnapshot?>(
        valueListenable: controller.snapshot,
        builder: (context, snap, _) {
          final station = snap?.stations.where((s) => s.id == widget.stationId).firstOrNull;
          return RefreshIndicator(
            onRefresh: _load,
            child: ListView(padding: const EdgeInsets.all(Space.lg), children: [
              if (station != null) _Summary(station: station),
              const SizedBox(height: Space.lg),
              if (station != null && station.threats.isNotEmpty) ...[
                Text('ACTIVE THREATS', style: Theme.of(context).textTheme.labelSmall?.copyWith(color: farm.muted)),
                const SizedBox(height: Space.sm),
                for (final threat in station.threats)
                  Padding(
                    padding: const EdgeInsets.only(bottom: Space.md),
                    child: SectionCard(
                      padding: EdgeInsets.zero,
                      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                        SnapshotImage(
                          threat.thumbPath == null ? null : controller.api.snapshotUrl(threat.thumbPath!),
                          fullUrl: threat.thumbPath == null
                              ? null
                              : controller.api.snapshotUrl(threat.thumbPath!.replaceAll('latest_thumb', 'latest_full')),
                          height: 190,
                          radius: 0,
                        ),
                        Padding(
                          padding: const EdgeInsets.all(Space.md),
                          child: Row(children: [
                            Expanded(
                              child: Text('${threat.count} × ${classLabel(threat.cls)}',
                                  style: Theme.of(context).textTheme.titleMedium),
                            ),
                            if (threat.cls == 'fungus')
                              StatusPill('${threat.coveragePct.toStringAsFixed(1)}% coverage', color: farm.fungus),
                            const SizedBox(width: Space.sm),
                            StatusPill('${(threat.confidence * 100).round()}%', color: farm.forClass(threat.cls)),
                          ]),
                        ),
                      ]),
                    ),
                  ),
              ],
              SectionCard(
                title: 'Detections',
                icon: Icons.timeline_rounded,
                child: _loading
                    ? const Column(children: [SkeletonBox(height: 56), SizedBox(height: 8), SkeletonBox(height: 56)])
                    : _error != null
                        ? EmptyState(icon: Icons.wifi_off_rounded, title: _error!, actionLabel: 'Retry', onAction: _load)
                        : _tracks.isEmpty
                            ? const EmptyState(icon: Icons.eco_outlined, title: 'Nothing detected on this lettuce')
                            : Column(children: [
                                for (final t in _tracks) TrackTile(track: t, onChanged: _load),
                              ]),
              ),
            ]),
          );
        },
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.station});
  final Station station;
  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final s = station;
    final color = s.hasPest ? farm.pest : s.hasFungus ? farm.fungus : farm.healthy;
    Widget metric(String label, String value, Color c) => Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(value, style: Theme.of(context).textTheme.titleLarge?.copyWith(color: c)),
            Text(label, style: TextStyle(color: farm.muted, fontSize: 12)),
          ]),
        );
    return SectionCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          StatusPill(s.headline, color: color),
          const Spacer(),
          if (s.active) StatusPill('Camera here', color: farm.healthy, icon: Icons.videocam_outlined),
        ]),
        const SizedBox(height: Space.md),
        Row(children: [
          metric('Worms', '${s.wormCount}', s.wormCount > 0 ? farm.pest : farm.muted),
          metric('Whiteflies', '${s.whiteflyCount}', s.whiteflyCount > 0 ? farm.pest : farm.muted),
          metric('Fungus', '${s.fungusCoverage.toStringAsFixed(1)}%', s.fungusCoverage > 0 ? farm.fungus : farm.muted),
        ]),
        if (s.fungusCoverage > 0) ...[
          const SizedBox(height: Space.md),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
                value: (s.fungusCoverage / 30).clamp(0, 1), minHeight: 6, color: farm.fungus, backgroundColor: farm.subtle),
          ),
        ],
        const SizedBox(height: Space.md),
        Text(s.description.isEmpty ? 'Checked ${timeAgo(s.lastScanned)}' : '${s.description} · checked ${timeAgo(s.lastScanned)}',
            style: TextStyle(color: farm.muted)),
      ]),
    );
  }
}
