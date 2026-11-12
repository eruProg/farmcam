import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/ui.dart';

/// Full-resolution (4056×3040) photos for training data, tagged with the
/// lettuce and part they show. The Pi saves them on the external SSD
/// ("New Datasets/FarmCam captures"), or on its SD card when no SSD is plugged in.
class DatasetsPage extends StatefulWidget {
  const DatasetsPage({super.key});
  @override
  State<DatasetsPage> createState() => _DatasetsPageState();
}

class _DatasetsPageState extends State<DatasetsPage> {
  int _station = 1;
  String _position = 'lower';
  bool _followScan = true, _busy = false, _loading = true;
  int _burstCount = 5;
  double _burstInterval = 2;
  final _note = TextEditingController();
  List<Map<String, dynamic>> _items = [];
  int _total = 0, _page = 1, _pages = 1;
  String? _error, _lastBurstSeen;

  FarmCamController? _controller;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_controller == null) {
      _controller = FarmCamScope.of(context)..link.addListener(_onLink);
      _onLink();
    }
  }

  /// All tabs are built at start-up; load once the Pi is actually reachable
  /// (and again after a reconnect if the last load failed).
  void _onLink() {
    final link = _controller!.link.value;
    if (link.isOnline && (_error != null || _items.isEmpty)) _load();
  }

  @override
  void dispose() {
    _controller?.link.removeListener(_onLink);
    _note.dispose();
    super.dispose();
  }

  Future<void> _load({int? page}) async {
    final controller = FarmCamScope.of(context);
    if (controller.isDemo) {
      setState(() {
        _loading = false;
        _items = [];
        _error = 'Photos are taken on the real machine (not in demo mode).';
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
      if (page != null) _page = page;
    });
    try {
      final data = await controller.api.captures(page: _page);
      if (!mounted) return;
      setState(() {
        _items = (data['items'] as List).cast<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
        _total = data['total'] as int? ?? _items.length;
        _pages = data['pages'] as int? ?? 1;
        _loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Could not load photos. Is the Pi server up to date?';
        });
      }
    }
  }

  (int?, String?) _target(AppSnapshot? snap) {
    if (_followScan && snap != null && snap.scan.running) return (snap.scan.station, snap.scan.position);
    return (_station, _position);
  }

  Future<void> _capture({bool burst = false}) async {
    final controller = FarmCamScope.of(context);
    final (station, position) = _target(controller.snapshot.value);
    HapticFeedback.mediumImpact();
    setState(() => _busy = true);
    try {
      if (burst) {
        await controller.api.captureBurst(
            count: _burstCount, intervalS: _burstInterval, station: station, position: position, note: _note.text.trim());
        if (mounted) showMessage(context, 'Taking $_burstCount photos, one every ${_burstInterval.toStringAsFixed(0)} s');
      } else {
        final r = await controller.api.captureFull(station: station, position: position, note: _note.text.trim());
        if (mounted) showMessage(context, 'Saved ${r['name']}');
        await _load(page: 1);
      }
      controller.refresh();
    } catch (e) {
      if (mounted) showMessage(context, '$e'.replaceFirst('Exception: ', ''), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete(String name) async {
    final controller = FarmCamScope.of(context);
    if (!await confirmAction(context,
        title: 'Delete this photo?', message: '$name will be removed from the Pi.', confirmLabel: 'Delete',
        destructive: true)) {
      return;
    }
    final ok = await controller.api.deleteCapture(name);
    if (!mounted) return;
    showMessage(context, ok ? 'Photo deleted' : 'Could not delete the photo', error: !ok);
    if (ok) _load();
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final text = Theme.of(context).textTheme;
    return RefreshIndicator(
      onRefresh: () => _load(page: 1),
      child: ListView(padding: const EdgeInsets.all(Space.lg), children: [
        Text('Taking Datasets', style: text.headlineSmall),
        const SizedBox(height: Space.xs),
        Text('Full-resolution photos (4056×3040) for labelling and retraining.', style: TextStyle(color: farm.muted)),
        const SizedBox(height: Space.lg),
        ValueListenableBuilder<AppSnapshot?>(
          valueListenable: controller.snapshot,
          builder: (context, snap, _) {
            final scanning = snap?.scan.running == true;
            final capture = snap?.capture ?? const CaptureInfo();
            // Refresh the gallery once a burst finishes.
            if (!capture.running && capture.last != null && capture.last != _lastBurstSeen) {
              _lastBurstSeen = capture.last;
              WidgetsBinding.instance.addPostFrameCallback((_) => mounted ? _load(page: 1) : null);
            }
            final (station, position) = _target(snap);
            return SectionCard(
              title: 'What is the camera looking at?',
              icon: Icons.center_focus_strong_outlined,
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                if (capture.storage != null) ...[
                  _StorageLine(storage: capture.storage!),
                  const SizedBox(height: Space.md),
                ],
                if (scanning)
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    value: _followScan,
                    onChanged: (v) => setState(() => _followScan = v),
                    title: const Text('Use the scan position'),
                    subtitle: Text('Now: lettuce ${snap!.scan.station} ${snap.scan.position}'),
                  ),
                IgnorePointer(
                  ignoring: scanning && _followScan,
                  child: AnimatedOpacity(
                    duration: const Duration(milliseconds: 200),
                    opacity: scanning && _followScan ? 0.45 : 1,
                    child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                      SegmentedButton<int>(
                        segments: [for (var i = 1; i <= 4; i++) ButtonSegment(value: i, label: Text('L$i'))],
                        selected: {station ?? _station},
                        onSelectionChanged: (v) => setState(() => _station = v.first),
                      ),
                      const SizedBox(height: Space.sm),
                      SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'upper', label: Text('Upper'), icon: Icon(Icons.arrow_upward_rounded)),
                          ButtonSegment(value: 'lower', label: Text('Lower'), icon: Icon(Icons.arrow_downward_rounded)),
                        ],
                        selected: {position ?? _position},
                        onSelectionChanged: (v) => setState(() => _position = v.first),
                      ),
                    ]),
                  ),
                ),
                const SizedBox(height: Space.md),
                TextField(
                  controller: _note,
                  maxLength: 80,
                  decoration: const InputDecoration(
                      labelText: 'Note (optional)', hintText: 'e.g. glare, whitefly close-up, empty leaf'),
                ),
                const SizedBox(height: Space.sm),
                if (capture.running) ...[
                  Text('Capturing ${capture.done} of ${capture.total}…', style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: Space.sm),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                        value: capture.total == 0 ? null : capture.done / capture.total, minHeight: 6),
                  ),
                ] else ...[
                  FilledButton.icon(
                    key: const ValueKey('capture_one'),
                    onPressed: _busy || controller.isDemo ? null : () => _capture(),
                    icon: _busy
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.photo_camera_rounded),
                    label: const Text('Take photo'),
                  ),
                  const SizedBox(height: Space.md),
                  Row(children: [
                    Expanded(
                      child: Text('Burst: $_burstCount photos, every ${_burstInterval.toStringAsFixed(0)} s',
                          style: TextStyle(color: farm.muted)),
                    ),
                    OutlinedButton(
                      onPressed: _busy || controller.isDemo ? null : () => _capture(burst: true),
                      child: const Text('Start burst'),
                    ),
                  ]),
                  Slider(
                    value: _burstCount.toDouble(), min: 2, max: 30, divisions: 28,
                    label: '$_burstCount photos',
                    onChanged: (v) => setState(() => _burstCount = v.round()),
                  ),
                  Slider(
                    value: _burstInterval, min: 1, max: 10, divisions: 9,
                    label: 'every ${_burstInterval.toStringAsFixed(0)} s',
                    onChanged: (v) => setState(() => _burstInterval = v),
                  ),
                ],
                if (capture.error != null)
                  Text(capture.error!, style: TextStyle(color: farm.pest)),
              ]),
            );
          },
        ),
        const SizedBox(height: Space.lg),
        SectionCard(
          title: 'Photos on the Pi',
          icon: Icons.photo_library_outlined,
          trailing: Text('$_total', style: TextStyle(color: farm.muted)),
          child: _loading
              ? const SkeletonBox(height: 160)
              : _error != null
                  ? EmptyState(icon: Icons.photo_camera_outlined, title: _error!, actionLabel: 'Retry', onAction: _load)
                  : _items.isEmpty
                      ? const EmptyState(
                          icon: Icons.photo_camera_outlined,
                          title: 'No photos yet',
                          message: 'Take a photo or a burst above.')
                      : Column(children: [
                          GridView.builder(
                            shrinkWrap: true,
                            physics: const NeverScrollableScrollPhysics(),
                            gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                                maxCrossAxisExtent: 180, mainAxisSpacing: 8, crossAxisSpacing: 8, childAspectRatio: 4 / 3.6),
                            itemCount: _items.length,
                            itemBuilder: (context, i) => _CaptureTile(item: _items[i], onDelete: _delete),
                          ),
                          if (_pages > 1)
                            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                              IconButton(
                                  onPressed: _page > 1 ? () => _load(page: _page - 1) : null,
                                  icon: const Icon(Icons.chevron_left_rounded)),
                              Text('$_page / $_pages'),
                              IconButton(
                                  onPressed: _page < _pages ? () => _load(page: _page + 1) : null,
                                  icon: const Icon(Icons.chevron_right_rounded)),
                            ]),
                        ]),
        ),
        const SizedBox(height: Space.lg),
        SectionCard(
          title: 'Tips for good training photos',
          icon: Icons.lightbulb_outline_rounded,
          child: Text(
            '• Whiteflies: get close and steady; at full resolution their wings show, unlike glare.\n'
            '• Also take "nothing here" photos: glare, water drops, the casing, pots and healthy leaves. '
            'Upload them to Roboflow as Null.\n'
            '• Label every pest and lesion in a photo; an unlabelled one teaches the model it is not there.\n'
            '• Find the photos on the SSD in New Datasets/FarmCam captures/ (names start with field_full_).',
            style: TextStyle(color: farm.muted, height: 1.5),
          ),
        ),
      ]),
    );
  }
}

/// "Saving to the external SSD · 412 GB free", or a warning when the Pi
/// fell back to its SD card because no SSD is mounted.
class _StorageLine extends StatelessWidget {
  const _StorageLine({required this.storage});
  final StorageInfo storage;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final color = storage.onSsd ? farm.healthy : farm.warning;
    final free = storage.freeGb == null ? '' : ' · ${storage.freeGb!.toStringAsFixed(1)} GB free';
    return Container(
      key: const ValueKey('storage_line'),
      padding: const EdgeInsets.symmetric(horizontal: Space.md, vertical: Space.sm),
      decoration: BoxDecoration(
        color: (storage.onSsd ? farm.healthyBg : farm.warning.withOpacity(0.12)),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(children: [
        Icon(storage.onSsd ? Icons.save_rounded : Icons.sd_card_alert_outlined, color: color, size: 20),
        const SizedBox(width: Space.sm),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(storage.onSsd ? 'Saving to the external SSD$free' : 'No SSD found: saving to the Pi\'s SD card$free',
                style: TextStyle(color: color, fontWeight: FontWeight.w600)),
            Text(storage.onSsd ? storage.path : 'Plug in the SSD and restart the Pi to save there.',
                maxLines: 2, overflow: TextOverflow.ellipsis,
                style: TextStyle(color: farm.muted, fontSize: 12)),
          ]),
        ),
      ]),
    );
  }
}

class _CaptureTile extends StatelessWidget {
  const _CaptureTile({required this.item, required this.onDelete});
  final Map<String, dynamic> item;
  final void Function(String) onDelete;
  @override
  Widget build(BuildContext context) {
    final api = FarmCamScope.of(context).api;
    final farm = FarmColors.of(context);
    final name = item['name'] as String;
    final time = DateTime.fromMillisecondsSinceEpoch(((item['time'] as num? ?? 0) * 1000).round());
    final where = [
      if (item['station'] != null) 'L${item['station']}',
      if (item['position'] != null) '${item['position']}',
    ].join(' ');
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Expanded(
        child: Stack(fit: StackFit.expand, children: [
          SnapshotImage(api.captureThumbUrl(name), fullUrl: api.captureUrl(name), radius: 10),
          Positioned(
            right: 2,
            top: 2,
            child: IconButton.filledTonal(
              iconSize: 18,
              visualDensity: VisualDensity.compact,
              tooltip: 'Delete',
              onPressed: () => onDelete(name),
              icon: const Icon(Icons.delete_outline_rounded),
            ),
          ),
        ]),
      ),
      const SizedBox(height: 4),
      Text(where.isEmpty ? timeAgo(time) : '$where · ${timeAgo(time)}',
          maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, color: farm.muted)),
    ]);
  }
}
