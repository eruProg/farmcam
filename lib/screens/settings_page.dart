import 'dart:async';
import 'dart:io' show Platform;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../core/background_watch.dart';
import '../core/farmcam_controller.dart';
import '../core/notifications.dart';
import '../core/theme.dart';
import '../widgets/ui.dart';
import 'datasets_page.dart';

/// Connection (current Pi, search again, manual IP) and appearance.
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final _ip = TextEditingController();

  @override
  void dispose() {
    _ip.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(padding: const EdgeInsets.all(Space.lg), children: [
        ValueListenableBuilder<LinkInfo>(
          valueListenable: controller.link,
          builder: (context, link, _) => SectionCard(
            title: 'Connection',
            icon: Icons.wifi_rounded,
            trailing: StatusPill(link.label,
                color: link.isOnline ? farm.healthy : farm.fungus),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(link.state == LinkState.demo ? 'Demo mode: no Pi connected.' : 'Pi at ${link.ip ?? '–'}:${link.port}',
                  style: TextStyle(color: farm.muted)),
              const SizedBox(height: Space.md),
              FilledButton.icon(
                onPressed: () => controller.findAndConnect(preferred: link.ip),
                icon: const Icon(Icons.search_rounded),
                label: const Text('Search for FarmCam again'),
              ),
              const SizedBox(height: Space.md),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _ip,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(labelText: 'Connect to IP', isDense: true),
                  ),
                ),
                const SizedBox(width: Space.sm),
                OutlinedButton(
                  onPressed: () async {
                    final ok = await controller.connect(_ip.text.trim());
                    if (context.mounted) {
                      showMessage(context, ok ? 'Connected' : 'No FarmCam at that address', error: !ok);
                    }
                  },
                  child: const Text('Connect'),
                ),
              ]),
              const SizedBox(height: Space.sm),
              if (link.state == LinkState.demo)
                OutlinedButton(onPressed: controller.disconnect, child: const Text('Leave demo mode'))
              else
                TextButton(
                    onPressed: () async {
                      if (await confirmAction(context,
                          title: 'Disconnect?', message: 'You can search for the Pi again any time.',
                          confirmLabel: 'Disconnect')) {
                        controller.disconnect();
                        if (context.mounted) Navigator.of(context).popUntil((r) => r.isFirst);
                      }
                    },
                    child: const Text('Disconnect')),
            ]),
          ),
        ),
        const SizedBox(height: Space.lg),
        const PhoneAlertsCard(),
        const SizedBox(height: Space.lg),
        ValueListenableBuilder<ThemeMode>(
          valueListenable: controller.themeMode,
          builder: (context, mode, _) => SectionCard(
            title: 'Appearance',
            icon: Icons.palette_outlined,
            child: SegmentedButton<ThemeMode>(
              segments: const [
                ButtonSegment(value: ThemeMode.system, label: Text('Auto'), icon: Icon(Icons.brightness_auto_rounded)),
                ButtonSegment(value: ThemeMode.light, label: Text('Light'), icon: Icon(Icons.light_mode_rounded)),
                ButtonSegment(value: ThemeMode.dark, label: Text('Dark'), icon: Icon(Icons.dark_mode_rounded)),
              ],
              selected: {mode},
              onSelectionChanged: (v) => controller.setTheme(v.first),
            ),
          ),
        ),
        const SizedBox(height: Space.lg),
        SectionCard(
          title: 'About',
          icon: Icons.info_outline_rounded,
          child: Text(
            'FarmCam watches four lettuces one at a time (upper and lower part), detects '
            'whiteflies, worms and fungus on the Raspberry Pi, and sprays or sends an SMS when needed. '
            'The live camera view is on the machine\'s own screen; this app shows saved detection photos.',
            style: TextStyle(color: farm.muted, height: 1.45),
          ),
        ),
        const SizedBox(height: Space.lg),
        // For the developers only: collecting machine photos for retraining.
        SectionCard(
          title: 'Developer',
          icon: Icons.code_rounded,
          onTap: () => Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => Scaffold(appBar: AppBar(title: const Text('Training photos')), body: const DatasetsPage()))),
          child: Row(children: [
            Icon(Icons.photo_camera_outlined, color: farm.muted),
            const SizedBox(width: Space.md),
            const Expanded(child: Text('Take training photos', style: TextStyle(fontWeight: FontWeight.w600))),
            const Icon(Icons.chevron_right_rounded),
          ]),
        ),
      ]),
    );
  }
}

/// Phone notifications ("Whitefly detected", "Error: …") even when the app
/// is closed: on/off, which alerts, and a test.
class PhoneAlertsCard extends StatefulWidget {
  const PhoneAlertsCard({super.key, this.supported});
  /// Null: Android only. Tests pass true/false.
  final bool? supported;
  @override
  State<PhoneAlertsCard> createState() => _PhoneAlertsCardState();
}

class _PhoneAlertsCardState extends State<PhoneAlertsCard> {
  bool _enabled = false, _busy = false, _loaded = false;
  Map<String, bool> _kinds = {for (final k in NotifyPrefs.keys.keys) k: true};
  DateTime? _lastCheck;
  Timer? _timer;

  bool get _supported => widget.supported ?? Platform.isAndroid;

  static const _labels = {
    'whitefly': ('Whitefly detected', Icons.bug_report_outlined),
    'worm': ('Worm detected', Icons.pest_control_outlined),
    'fungus': ('Fungus detected', Icons.coronavirus_outlined),
    'sprays': ('Pesticide sprayed', Icons.water_drop_outlined),
    'errors': ('Errors (pump/SMS unit, SMS, scan)', Icons.error_outline_rounded),
  };

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _refreshStatus());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _enabled = prefs.getBool('notify_enabled') ?? false;
      _kinds = {for (final e in NotifyPrefs.keys.entries) e.key: prefs.getBool(e.value) ?? true};
      _loaded = true;
    });
    await _refreshStatus();
  }

  Future<void> _refreshStatus() async {
    if (!_enabled || !_supported) return;
    final last = await BackgroundWatch.lastCheck();
    if (mounted) setState(() => _lastCheck = last);
  }

  Future<void> _toggle(bool on) async {
    setState(() => _busy = true);
    String? error;
    if (on) {
      error = await BackgroundWatch.enable();
    } else {
      await BackgroundWatch.disable();
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _enabled = on && error == null;
    });
    if (error != null) showMessage(context, error, error: true);
    if (on && error == null) showMessage(context, 'FarmCam alerts are on. They also work when the app is closed.');
  }

  Future<void> _setKind(String kind, bool value) async {
    setState(() => _kinds = {..._kinds, kind: value});
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(NotifyPrefs.keys[kind]!, value);
  }

  Future<void> _test() async {
    final controller = FarmCamScope.of(context);
    try {
      await controller.api.sendTestEvent();
      if (mounted) showMessage(context, 'Test sent. The notification appears within 30 seconds.');
    } catch (e) {
      if (mounted) showMessage(context, '$e'.replaceFirst('Exception: ', ''), error: true);
    }
  }

  String _status() {
    if (!_enabled) return 'Off';
    final last = _lastCheck;
    if (last == null) return 'On · waiting for the first check';
    final s = DateTime.now().difference(last).inSeconds;
    return s < 120 ? 'On · checked the Pi ${s}s ago' : 'On · Pi not reachable since ${last.hour}:${'${last.minute}'.padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    final demo = controller.isDemo;
    final usable = _supported && !demo && _loaded;
    return SectionCard(
      title: 'Phone notifications',
      icon: Icons.notifications_active_outlined,
      trailing: _enabled ? StatusPill('On', color: farm.healthy) : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SwitchListTile(
          key: const ValueKey('alerts_switch'),
          contentPadding: EdgeInsets.zero,
          title: const Text('Alert me on this phone'),
          subtitle: Text(!_supported
              ? 'Android phones only'
              : demo
                  ? 'Not available in demo mode'
                  : 'Whitefly, worm and fungus detected, sprays and errors, even when the app is closed. '
                      'The phone must be on the FarmCam hotspot.'),
          value: _enabled,
          onChanged: !usable || _busy ? null : _toggle,
        ),
        if (_enabled) ...[
          Text(_status(), style: TextStyle(color: farm.muted, fontSize: 12)),
          const Divider(height: Space.xl),
          for (final e in _labels.entries)
            CheckboxListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              secondary: Icon(e.value.$2, size: 20),
              title: Text(e.value.$1),
              value: _kinds[e.key] ?? true,
              onChanged: (v) => _setKind(e.key, v ?? true),
            ),
          const SizedBox(height: Space.sm),
          OutlinedButton.icon(
            onPressed: _test,
            icon: const Icon(Icons.notification_add_outlined),
            label: const Text('Send test notification'),
          ),
          const SizedBox(height: Space.sm),
          Text('Android keeps one silent "FarmCam alerts are on" entry while this runs; '
              'you can swipe it away or hide it. Away from the farm, the SMS still reaches the farmer.',
              style: TextStyle(color: farm.muted, fontSize: 12, height: 1.4)),
        ],
      ]),
    );
  }
}
