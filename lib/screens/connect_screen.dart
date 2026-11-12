import 'package:flutter/material.dart';
import '../core/discovery.dart';
import '../core/farmcam_controller.dart';
import '../core/theme.dart';
import '../widgets/ui.dart';

/// First screen: finds the FarmCam Pi on the hotspot by itself.
/// Manual IP and demo mode stay available as fallbacks.
class ConnectScreen extends StatefulWidget {
  const ConnectScreen({super.key});
  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  final _ip = TextEditingController();
  final _port = TextEditingController(text: '8000');
  bool _manual = false;

  @override
  void dispose() {
    _ip.dispose();
    _port.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: ListView(padding: const EdgeInsets.all(Space.xl), children: [
              const SizedBox(height: Space.xl),
              Row(children: [
                ClipRRect(
                    borderRadius: BorderRadius.circular(14),
                    child: Image.asset('assets/logo_192.png', width: 52, height: 52,
                        errorBuilder: (_, __, ___) => Icon(Icons.eco_rounded, size: 52, color: farm.healthy))),
                const SizedBox(width: Space.md),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('FarmCam', style: text.headlineSmall),
                    Text('Lettuce pest & fungus monitoring', style: TextStyle(color: farm.muted)),
                  ]),
                ),
              ]),
              const SizedBox(height: Space.xxl),
              ValueListenableBuilder<LinkInfo>(
                valueListenable: controller.link,
                builder: (context, link, _) => _StatusCard(link: link),
              ),
              const SizedBox(height: Space.lg),
              ValueListenableBuilder<List<FoundPi>>(
                valueListenable: controller.found,
                builder: (context, found, _) => found.length < 2
                    ? const SizedBox.shrink()
                    : SectionCard(
                        title: 'Several FarmCams found',
                        icon: Icons.devices_other_rounded,
                        child: Column(children: [
                          for (final pi in found)
                            ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: const Icon(Icons.memory_rounded),
                              title: Text(pi.hostname.isEmpty ? pi.ip : pi.hostname),
                              subtitle: Text('${pi.ip}:${pi.port} · ${pi.build}'),
                              trailing: const Icon(Icons.chevron_right_rounded),
                              onTap: () => controller.connect(pi.ip, pi.port),
                            ),
                        ]),
                      ),
              ),
              const SizedBox(height: Space.lg),
              AnimatedCrossFade(
                duration: const Duration(milliseconds: 220),
                crossFadeState: _manual ? CrossFadeState.showSecond : CrossFadeState.showFirst,
                firstChild: Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () => setState(() => _manual = true),
                    icon: const Icon(Icons.keyboard_rounded),
                    label: const Text('Enter the IP address instead'),
                  ),
                ),
                secondChild: SectionCard(
                  title: 'Connect by IP',
                  icon: Icons.lan_outlined,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    Row(children: [
                      Expanded(
                        flex: 3,
                        child: TextField(
                          key: const ValueKey('manual_ip'),
                          controller: _ip,
                          keyboardType: const TextInputType.numberWithOptions(decimal: true),
                          decoration: const InputDecoration(labelText: 'Pi IP address', hintText: '10.229.135.224'),
                        ),
                      ),
                      const SizedBox(width: Space.sm),
                      Expanded(
                        child: TextField(
                          controller: _port,
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(labelText: 'Port'),
                        ),
                      ),
                    ]),
                    const SizedBox(height: Space.md),
                    FilledButton(
                      onPressed: () => controller.connect(
                          _ip.text.trim(), int.tryParse(_port.text.trim()) ?? 8000),
                      child: const Text('Connect'),
                    ),
                  ]),
                ),
              ),
              const SizedBox(height: Space.lg),
              SectionCard(
                title: 'First time on a new phone?',
                icon: Icons.wifi_tethering_rounded,
                child: Text(
                  'Turn on this phone\'s hotspot named "FarmCam" with password "farmcam1". '
                  'The Pi and the ESP32 join it automatically after they power on; '
                  'then tap Search again.',
                  style: TextStyle(color: farm.muted, height: 1.45),
                ),
              ),
              const SizedBox(height: Space.lg),
              Center(
                child: TextButton(
                  onPressed: controller.startDemo,
                  child: const Text('Try demo mode (no machine needed)'),
                ),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.link});
  final LinkInfo link;
  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final busy = link.state == LinkState.searching || link.state == LinkState.connecting;
    final failed = link.state == LinkState.notFound;
    return SectionCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Icon(failed ? Icons.wifi_find_rounded : Icons.radar_rounded,
              color: failed ? farm.fungus : farm.healthy),
          const SizedBox(width: Space.sm),
          Expanded(child: Text(busy ? link.label : failed ? 'FarmCam not found' : 'Find your FarmCam',
              style: Theme.of(context).textTheme.titleMedium)),
        ]),
        const SizedBox(height: Space.sm),
        Text(
          busy
              ? 'Checking this phone\'s hotspot for the Pi…'
              : link.message ?? 'The app looks for the Pi on this phone\'s hotspot. No IP address needed.',
          style: TextStyle(color: farm.muted, height: 1.4),
        ),
        const SizedBox(height: Space.md),
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 200),
          child: busy
              ? ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                      key: const ValueKey('progress'), minHeight: 6, value: link.progress))
              : FilledButton.icon(
                  key: const ValueKey('search'),
                  onPressed: () => controller.findAndConnect(preferred: link.ip),
                  icon: const Icon(Icons.search_rounded),
                  label: Text(failed ? 'Search again' : 'Search for FarmCam'),
                ),
        ),
      ]),
    );
  }
}
