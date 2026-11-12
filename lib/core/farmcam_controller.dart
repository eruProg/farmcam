import 'dart:async';
import 'dart:math';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../data/app_state.dart';
import '../data/scan_state_model.dart';
import '../services/api_service.dart';
import 'discovery.dart';
import 'models.dart';

enum LinkState { idle, searching, connecting, online, reconnecting, notFound, demo }

class LinkInfo {
  const LinkInfo(this.state, {this.ip, this.port = 8000, this.message, this.progress});
  final LinkState state;
  final String? ip, message;
  final int port;
  final double? progress; // discovery progress 0..1 while searching

  bool get isOnline => state == LinkState.online || state == LinkState.demo;
  String get label => switch (state) {
        LinkState.online => 'Connected',
        LinkState.demo => 'Demo mode',
        LinkState.searching => 'Looking for FarmCam…',
        LinkState.connecting => 'Connecting…',
        LinkState.reconnecting => 'Reconnecting…',
        LinkState.notFound => 'FarmCam not found',
        LinkState.idle => 'Not connected',
      };
}

/// Single source of truth for the app. Owns the connection, auto-discovery
/// and ONE `/api/app/state` poll. Widgets listen to the narrow notifiers
/// they need, and `snapshot` only changes when the Pi's `rev` changes, so a
/// quiet poll redraws nothing.
class FarmCamController with WidgetsBindingObserver {
  FarmCamController({PiDiscovery? discovery, this.pollEvery = const Duration(seconds: 2)})
      : discovery = discovery ?? PiDiscovery();

  final PiDiscovery discovery;
  final Duration pollEvery;
  final AppState _endpoint = AppState(); // holds the base URL ApiService reads
  late final ApiService api = ApiService(_endpoint);
  final ScanStateModel clock = ScanStateModel();

  final link = ValueNotifier<LinkInfo>(const LinkInfo(LinkState.idle));
  final snapshot = ValueNotifier<AppSnapshot?>(null);
  final themeMode = ValueNotifier<ThemeMode>(ThemeMode.system);
  final found = ValueNotifier<List<FoundPi>>(const []);

  Timer? _timer;
  bool _polling = false, _paused = false, _disposed = false, _rediscovering = false, _observing = false;
  int _misses = 0;
  DateTime _lastRediscover = DateTime.fromMillisecondsSinceEpoch(0);
  Timer? _demoTimer;
  final _rng = Random(7);

  bool get isDemo => link.value.state == LinkState.demo;
  String get baseUrl => _endpoint.baseUrl;

  // ── Start-up ─────────────────────────────────────────────────

  /// Restores theme and last Pi, then connects or searches automatically.
  Future<void> start({bool autoConnect = true}) async {
    if (!_observing) {
      WidgetsBinding.instance.addObserver(this);
      _observing = true;
    }
    final prefs = await SharedPreferences.getInstance();
    themeMode.value = ThemeMode.values.firstWhere(
        (m) => m.name == prefs.getString('theme_mode'), orElse: () => ThemeMode.system);
    if (!autoConnect) return;
    final ip = prefs.getString('last_rpi_ip');
    final port = int.tryParse(prefs.getString('last_rpi_port') ?? '') ?? 8000;
    await findAndConnect(preferred: ip, port: port);
  }

  Future<void> setTheme(ThemeMode mode) async {
    themeMode.value = mode;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('theme_mode', mode.name);
  }

  // ── Connecting ───────────────────────────────────────────────

  /// Tries the last Pi first, then scans the hotspot. Connects to the first
  /// FarmCam found; with several, all are listed in [found].
  Future<bool> findAndConnect({String? preferred, int port = 8000}) async {
    stopDemo();
    link.value = LinkInfo(LinkState.searching, progress: 0, ip: preferred);
    final results = await discovery.scan(
        preferred: preferred,
        stopAtFirst: true,
        onProgress: (p) {
          if (!_disposed && link.value.state == LinkState.searching) {
            link.value = LinkInfo(LinkState.searching, progress: p, ip: preferred);
          }
        });
    if (_disposed) return false;
    found.value = results;
    if (results.isEmpty) {
      link.value = const LinkInfo(LinkState.notFound,
          message: 'Make sure the Pi is on and connected to the FarmCam hotspot.');
      return false;
    }
    return connect(results.first.ip, results.first.port);
  }

  /// Connects to a known address (manual entry or a found Pi).
  Future<bool> connect(String ip, [int port = 8000]) async {
    stopDemo();
    link.value = LinkInfo(LinkState.connecting, ip: ip, port: port);
    final hit = await discovery.probe(ip, port: port);
    if (_disposed) return false;
    if (hit == null) {
      link.value = LinkInfo(LinkState.notFound, ip: ip, port: port,
          message: 'No FarmCam answered at $ip:$port.');
      return false;
    }
    _endpoint.setConnection(ip, port);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('last_rpi_ip', ip);
    await prefs.setString('last_rpi_port', '$port');
    _misses = 0;
    link.value = LinkInfo(LinkState.online, ip: ip, port: port);
    _schedule(Duration.zero);
    return true;
  }

  void disconnect() {
    _timer?.cancel();
    stopDemo();
    snapshot.value = null;
    clock.disconnect();
    link.value = const LinkInfo(LinkState.idle);
  }

  // ── Polling ──────────────────────────────────────────────────

  void _schedule(Duration delay) {
    _timer?.cancel();
    if (_disposed || _paused || isDemo) return;
    _timer = Timer(delay, _tick);
  }

  /// Fetch now (e.g. right after a command) instead of waiting for the poll.
  Future<void> refresh() async {
    if (isDemo) return;
    _timer?.cancel();
    await _tick();
  }

  Future<void> _tick() async {
    if (_polling || _disposed || link.value.ip == null) return;
    _polling = true;
    final sent = clock.now;
    try {
      final json = await api.appState();
      final received = clock.now;
      final snap = AppSnapshot.fromJson(json);
      _misses = 0;
      if (link.value.state != LinkState.online) {
        link.value = LinkInfo(LinkState.online, ip: link.value.ip, port: link.value.port);
      }
      if (snap.scan.running && snap.scan.hasClock) {
        clock.update(snap.scan.raw, sent, received);
      } else {
        clock.disconnect();
      }
      if (snapshot.value?.rev != snap.rev || snap.rev.isEmpty) snapshot.value = snap;
      _polling = false;
      _schedule(pollEvery);
    } catch (_) {
      _polling = false;
      _misses++;
      if (_misses >= 3 && link.value.state == LinkState.online) {
        link.value = LinkInfo(LinkState.reconnecting, ip: link.value.ip, port: link.value.port,
            message: 'Lost contact with the Pi. Retrying…');
      }
      if (_misses >= 3) unawaited(_rediscover());
      // Back off 2 → 4 → 8 s while the Pi is away.
      _schedule(pollEvery * min(4, 1 << min(_misses - 1, 2)));
    }
  }

  /// The hotspot may have given the Pi a new IP: look for it again,
  /// at most every 20 s, without dropping the last snapshot.
  Future<void> _rediscover() async {
    if (_rediscovering || DateTime.now().difference(_lastRediscover).inSeconds < 20) return;
    _rediscovering = true;
    _lastRediscover = DateTime.now();
    try {
      final results = await discovery.scan(preferred: link.value.ip, stopAtFirst: true);
      if (_disposed || results.isEmpty) return;
      final pi = results.first;
      if (pi.ip != link.value.ip || pi.port != link.value.port) {
        _endpoint.setConnection(pi.ip, pi.port);
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('last_rpi_ip', pi.ip);
        link.value = LinkInfo(LinkState.reconnecting, ip: pi.ip, port: pi.port,
            message: 'Found the Pi at a new address (${pi.ip}).');
      }
      _schedule(Duration.zero);
    } finally {
      _rediscovering = false;
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _paused = state != AppLifecycleState.resumed;
    if (_paused) {
      _timer?.cancel();
    } else if (!isDemo && link.value.ip != null) {
      _schedule(Duration.zero);
    }
  }

  // ── Demo mode (no Pi needed) ─────────────────────────────────

  void startDemo() {
    _timer?.cancel();
    link.value = const LinkInfo(LinkState.demo, ip: 'demo');
    _demoStart = clock.now;
    _demoTick();
    _demoTimer?.cancel();
    _demoTimer = Timer.periodic(const Duration(seconds: 2), (_) => _demoTick());
  }

  void stopDemo() {
    _demoTimer?.cancel();
    _demoTimer = null;
  }

  double _demoStart = 0;
  static const _demoPhases = [
    ('DETECT_LOWER', 'Detecting (Lower)', 'lower', 20.0),
    ('TRANSIT_TO_UPPER', 'Raising', 'upper', 4.0),
    ('DETECT_UPPER', 'Detecting (Upper)', 'upper', 20.0),
    ('MOVING_TO_STATION', 'Moving', 'upper', 6.0),
  ];

  void _demoTick() {
    final t = clock.now - _demoStart;
    const cycle = 50.0;
    final station = (t ~/ cycle) % 4 + 1;
    var into = t % cycle, started = t - into;
    var phase = _demoPhases.first;
    for (final p in _demoPhases) {
      if (into < p.$4) {
        phase = p;
        break;
      }
      into -= p.$4;
      started += p.$4;
    }
    final scan = {
      'running': true, 'run_id': 'demo', 'phase': phase.$1, 'phase_label': phase.$2,
      'station': station, 'position': phase.$3, 'is_detecting': phase.$1.startsWith('DETECT'),
      'server_time': t, 'phase_started_at': started, 'phase_duration_s': phase.$4,
    };
    Map<String, dynamic> st(int id, {int pests = 0, double fungus = 0}) => {
          'id': id, 'name': 'Lettuce $id', 'active': id == station,
          'pest_count': pests, 'worm_count': pests, 'fungus_coverage_pct': fungus,
          'pest_severity_level': pests >= 3 ? 'high' : pests > 0 ? 'medium' : 'none',
          'fungus_severity_level': fungus >= 15 ? 'high' : fungus > 0 ? 'medium' : 'none',
          'threats': [
            if (pests > 0) {'class': 'worm', 'count': pests, 'confidence': .82},
            if (fungus > 0) {'class': 'fungus', 'count': 1, 'confidence': .64, 'coverage_pct': fungus},
          ],
          'last_scanned': DateTime.now().millisecondsSinceEpoch / 1000 - id * 600,
        };
    final json = {
      'rev': 'demo-$station-${phase.$1}',
      'build': 'demo', 'model_colors': 'split', 'scan': scan,
      'stations': [st(1), st(2, pests: 2), st(3, fungus: 6.5), st(4)],
      'cooldowns': {
        'pump': {'active': false, 'remaining_s': 0, 'total_s': 300},
        'sms': {'active': false, 'remaining_s': 0, 'total_s': 300},
        'buzzer_muted': false,
      },
      'pump': {'active': false},
      // The full-screen pop-up while moving on to the next lettuce.
      if (phase.$1 == 'MOVING_TO_STATION')
        'activity': {
          'kind': 'moving', 'title': 'Moving to Lettuce ${station % 4 + 1}',
          'body': 'The camera is moving to the next lettuce.',
        },
      'esp32': {'ip': 'demo', 'reachable': true},
      'health': {'camera_ok': true, 'detection_ok': true, 'ready': true, 'models': ['DET', 'SEG']},
      'capture': {
        'running': false, 'done': 0, 'total': 0,
        'storage': {'path': '/media/farmcam/SSD/New Datasets/FarmCam captures', 'on_ssd': true, 'free_gb': 412.3},
      },
      'recent': [
        {'id': 'demo-1', 'station': 2, 'position': 'upper', 'class': 'worm',
         'confidence': .82 + _rng.nextDouble() * .1, 'first_seen': DateTime.now().millisecondsSinceEpoch / 1000 - 900,
         'last_seen': DateTime.now().millisecondsSinceEpoch / 1000 - 60, 'scans_seen': 3},
      ],
    };
    // Sent/received are local clock times; server_time is the demo's own clock.
    clock.update(scan, clock.now, clock.now);
    final snap = AppSnapshot.fromJson(json);
    if (snapshot.value?.rev != snap.rev) snapshot.value = snap;
  }

  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _demoTimer?.cancel();
    if (_observing) WidgetsBinding.instance.removeObserver(this);
    link.dispose();
    snapshot.dispose();
    themeMode.dispose();
    found.dispose();
  }
}

/// Gives every page access to the one controller.
class FarmCamScope extends InheritedWidget {
  const FarmCamScope({super.key, required this.controller, required super.child});
  final FarmCamController controller;

  static FarmCamController of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<FarmCamScope>()!.controller;

  @override
  bool updateShouldNotify(FarmCamScope oldWidget) => controller != oldWidget.controller;
}
