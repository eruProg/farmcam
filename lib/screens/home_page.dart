import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/live_card.dart';
import '../widgets/scan_progress.dart';
import '../widgets/ui.dart';
import 'settings_page.dart';
import 'shell.dart';

/// Start page: machine health at a glance and one card per section.
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final text = Theme.of(context).textTheme;
    return ValueListenableBuilder<AppSnapshot?>(
      valueListenable: controller.snapshot,
      builder: (context, snap, _) => RefreshIndicator(
        onRefresh: controller.refresh,
        child: ListView(padding: const EdgeInsets.all(Space.lg), children: [
          Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('FarmCam', style: text.headlineSmall),
                ValueListenableBuilder<LinkInfo>(
                  valueListenable: controller.link,
                  builder: (context, link, _) => Text(
                      link.state == LinkState.demo ? 'Demo mode' : '${link.label} · ${link.ip ?? ''}',
                      style: TextStyle(color: farm.muted)),
                ),
              ]),
            ),
            IconButton(
              tooltip: 'Settings',
              icon: const Icon(Icons.settings_outlined),
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SettingsPage())),
            ),
          ]),
          const SizedBox(height: Space.lg),
          _HealthRow(snap: snap),
          if (snap?.live != null) ...[
            const SizedBox(height: Space.md),
            LiveCard(live: snap!.live!),
          ],
          if (snap != null && snap.alertCount > 0) ...[
            const SizedBox(height: Space.md),
            Material(
              color: farm.pestBg,
              borderRadius: BorderRadius.circular(14),
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => Shell.of(context)?.open(1),
                child: Padding(
                  padding: const EdgeInsets.all(Space.md),
                  child: Row(children: [
                    Icon(Icons.warning_amber_rounded, color: farm.pest),
                    const SizedBox(width: Space.sm),
                    Expanded(
                      child: Text(
                        '${snap.alertCount} lettuce${snap.alertCount == 1 ? ' needs' : 's need'} attention: '
                        '${snap.stations.where((s) => s.needsAttention).map((s) => 'L${s.id}').join(', ')}',
                        style: TextStyle(color: farm.pest, fontWeight: FontWeight.w600),
                      ),
                    ),
                    Icon(Icons.chevron_right_rounded, color: farm.pest),
                  ]),
                ),
              ),
            ),
          ],
          const SizedBox(height: Space.lg),
          _SectionTile(
            tab: 1,
            icon: Icons.radar_rounded,
            title: 'Monitoring',
            color: farm.healthy,
            subtitle: ScanLine(style: TextStyle(color: farm.muted)),
          ),
          _SectionTile(
            tab: 2,
            icon: Icons.insights_rounded,
            title: 'History & Reports',
            color: farm.fungus,
            subtitle: Text(
                snap == null || snap.recent.isEmpty
                    ? 'Detections, charts and CSV export'
                    : 'Latest: ${classLabel(snap.recent.first.cls)} at L${snap.recent.first.station}, '
                        '${timeAgo(snap.recent.first.lastSeen)}',
                style: TextStyle(color: farm.muted)),
          ),
          _SectionTile(
            tab: 3,
            icon: Icons.tune_rounded,
            title: 'Controls',
            color: Theme.of(context).colorScheme.primary,
            subtitle: Text(
                snap == null
                    ? 'Scan, pump, buzzer and SMS'
                    : 'Pump ${snap.pumpActive ? 'running' : snap.pumpCooldown.active ? 'cooling down' : 'ready'} · '
                        'SMS ${snap.smsCooldown.active ? 'cooling down' : 'ready'}',
                style: TextStyle(color: farm.muted)),
          ),
        ]),
      ),
    );
  }
}

class _HealthRow extends StatelessWidget {
  const _HealthRow({required this.snap});
  final AppSnapshot? snap;
  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final s = snap;
    Widget item(IconData icon, String label, bool? ok, {String? detail}) => Expanded(
          child: Container(
            padding: const EdgeInsets.all(Space.md),
            decoration: BoxDecoration(
                color: farm.card, borderRadius: BorderRadius.circular(14), border: Border.all(color: farm.border)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(icon, size: 18, color: ok == null ? farm.muted : ok ? farm.healthy : farm.pest),
                const Spacer(),
                Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                        shape: BoxShape.circle, color: ok == null ? farm.border : ok ? farm.healthy : farm.pest)),
              ]),
              const SizedBox(height: Space.sm),
              Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
              Text(detail ?? (ok == null ? '–' : ok ? 'OK' : 'Problem'),
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: farm.muted, fontSize: 12)),
            ]),
          ),
        );
    return Row(children: [
      item(Icons.memory_rounded, 'Pi', s?.health.ready, detail: s == null ? null : s.health.devMode ? 'Dev mode' : 'Online'),
      const SizedBox(width: Space.sm),
      item(Icons.photo_camera_outlined, 'Camera', s?.health.cameraOk),
      const SizedBox(width: Space.sm),
      item(Icons.router_outlined, 'ESP32', s?.esp32.reachable,
          detail: s == null ? null : s.esp32.reachable ? 'Pump & SMS' : s.esp32.scanning ? 'Searching…' : 'Not found'),
    ]);
  }
}

class _SectionTile extends StatelessWidget {
  const _SectionTile({required this.tab, required this.icon, required this.title, required this.color,
      required this.subtitle});
  final int tab;
  final IconData icon;
  final String title;
  final Color color;
  final Widget subtitle;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: Space.md),
        child: SectionCard(
          onTap: () => Shell.of(context)?.open(tab),
          child: Row(children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(color: color.withOpacity(0.13), borderRadius: BorderRadius.circular(14)),
              child: Icon(icon, color: color),
            ),
            const SizedBox(width: Space.md),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                subtitle,
              ]),
            ),
            Icon(Icons.chevron_right_rounded, color: FarmColors.of(context).muted),
          ]),
        ),
      );
}
