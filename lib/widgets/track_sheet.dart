import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import 'ui.dart';

/// "Lettuce 2 · upper part"
String trackPlace(Track t) => 'Lettuce ${t.station} · ${t.position.isEmpty ? '–' : t.position} part';

/// One line for lists: "Lettuce 2 · upper part · 3 scans · 2 h ago".
String trackSubtitle(Track t) =>
    '${trackPlace(t)} · ${t.scansSeen} scan${t.scansSeen == 1 ? '' : 's'} · ${timeAgo(t.lastSeen)}';

/// Confidence for pests, leaf coverage for fungus.
String trackAmount(Track t) =>
    t.cls == 'fungus' && t.coveragePct > 0 ? '${t.coveragePct.toStringAsFixed(1)}% of leaf' : '${(t.confidence * 100).round()}%';

/// One detection: its close-up and full photo, what and where, and the
/// Correct / Not a pest review. Used by History, Monitor and the lettuce page.
Future<void> showTrackSheet(BuildContext context, Track t, {VoidCallback? onChanged}) {
  final controller = FarmCamScope.of(context);
  final api = controller.api;
  final farm = FarmColors.of(context);
  final closeUp = t.thumbPath == null ? null : api.snapshotUrl(t.thumbPath!);
  final full = t.snapshotPath == null ? null : api.snapshotUrl(t.snapshotPath!);

  Future<void> review(BuildContext sheet, bool falsePositive) async {
    Navigator.pop(sheet);
    final ok = controller.isDemo ||
        (falsePositive ? await api.markFalsePositive(t.id) : await api.markReviewed(t.id));
    if (!context.mounted) return;
    showMessage(context,
        ok ? (falsePositive ? 'Marked as a false alarm' : 'Marked as correct') : 'Could not save. Try again.',
        error: !ok);
    if (ok) {
      onChanged?.call();
      controller.refresh();
    }
  }

  Widget fact(IconData icon, String text) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(children: [
          Icon(icon, size: 18, color: farm.muted),
          const SizedBox(width: Space.sm),
          Expanded(child: Text(text)),
        ]),
      );

  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (sheet) => SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(Space.xl, 0, Space.xl, Space.xl),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Expanded(child: Text(classLabel(t.cls), style: Theme.of(sheet).textTheme.headlineSmall)),
            StatusPill(trackAmount(t), color: farm.forClass(t.cls)),
          ]),
          const SizedBox(height: Space.xs),
          Text(trackPlace(t), style: TextStyle(color: farm.muted)),
          const SizedBox(height: Space.lg),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: _Photo(label: 'Close-up', url: closeUp, fullUrl: full)),
            const SizedBox(width: Space.sm),
            Expanded(child: _Photo(label: 'Whole photo', url: full)),
          ]),
          const SizedBox(height: Space.xs),
          Text('Tap a photo to zoom.', style: TextStyle(color: farm.muted, fontSize: 12)),
          const SizedBox(height: Space.lg),
          fact(Icons.schedule_rounded, 'First seen ${shortDateTime(t.firstSeen)}'),
          fact(Icons.update_rounded, 'Last seen ${shortDateTime(t.lastSeen)} (${timeAgo(t.lastSeen)})'),
          fact(Icons.repeat_rounded, 'Seen in ${t.scansSeen} scan${t.scansSeen == 1 ? '' : 's'}'),
          fact(t.stillThere ? Icons.circle : Icons.check_circle_outline_rounded,
              t.stillThere ? 'Still there at the last check' : 'Gone: not seen in the latest scan'),
          const SizedBox(height: Space.lg),
          if (t.falsePositive)
            Align(
                alignment: Alignment.centerLeft,
                child: StatusPill('Marked as a false alarm', color: farm.muted, icon: Icons.flag_outlined))
          else ...[
            if (t.reviewed)
              Padding(
                padding: const EdgeInsets.only(bottom: Space.sm),
                child: Align(
                    alignment: Alignment.centerLeft,
                    child: StatusPill('Marked as correct', color: farm.healthy, icon: Icons.check_rounded)),
              ),
            Row(children: [
              Expanded(
                child: FilledButton(
                    onPressed: t.reviewed ? null : () => review(sheet, false), child: const Text('Correct')),
              ),
              const SizedBox(width: Space.md),
              Expanded(
                child: OutlinedButton(
                    onPressed: () => review(sheet, true), child: const Text('Not a pest (false alarm)', textAlign: TextAlign.center)),
              ),
            ]),
          ],
        ]),
      ),
    ),
  );
}

class _Photo extends StatelessWidget {
  const _Photo({required this.label, required this.url, this.fullUrl});
  final String label;
  final String? url, fullUrl;
  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        AspectRatio(aspectRatio: 4 / 3, child: SnapshotImage(url, fullUrl: fullUrl, radius: 10)),
        const SizedBox(height: 4),
        Text(label, style: TextStyle(color: FarmColors.of(context).muted, fontSize: 12)),
      ]);
}
