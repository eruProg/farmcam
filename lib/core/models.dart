/// Typed views of the Pi's `/api/app/state` response. Every field is read
/// defensively: an older or partly-wired server must never crash the app.
library;

double _d(Object? v, [double fallback = 0]) =>
    v is num ? v.toDouble() : double.tryParse('$v') ?? fallback;
int _i(Object? v, [int fallback = 0]) =>
    v is num ? v.toInt() : int.tryParse('$v') ?? fallback;
String _s(Object? v, [String fallback = '']) => v == null ? fallback : '$v';
Map<String, dynamic> _m(Object? v) =>
    v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<Map<String, dynamic>> _l(Object? v) =>
    v is List ? [for (final e in v) if (e is Map) Map<String, dynamic>.from(e)] : const [];

String classLabel(String cls) => switch (cls) {
      'whiteflies' || 'whitefly' => 'Whitefly',
      'worm' => 'Worm',
      'fungus' => 'Fungus',
      _ => cls.isEmpty ? 'Unknown' : cls[0].toUpperCase() + cls.substring(1),
    };

class Threat {
  const Threat({required this.cls, required this.count, required this.confidence,
      this.thumbPath, this.coveragePct = 0});
  final String cls;
  final int count;
  final double confidence;
  final String? thumbPath;
  final double coveragePct;

  factory Threat.fromJson(Map<String, dynamic> j) => Threat(
        cls: _s(j['class']),
        count: _i(j['count']),
        confidence: _d(j['confidence']),
        thumbPath: j['thumb_path'] as String?,
        coveragePct: _d(j['coverage_pct']),
      );
}

class Station {
  const Station({
    required this.id,
    required this.name,
    required this.status,
    required this.pestSeverity,
    required this.fungusSeverity,
    required this.severity,
    required this.active,
    this.pestCount = 0,
    this.whiteflyCount = 0,
    this.wormCount = 0,
    this.fungusCoverage = 0,
    this.confidence = 0,
    this.lastScanned,
    this.description = '',
    this.threats = const [],
  });

  final int id;
  final String name, status, pestSeverity, fungusSeverity, severity, description;
  final bool active;
  final int pestCount, whiteflyCount, wormCount;
  final double fungusCoverage, confidence;
  final DateTime? lastScanned;
  final List<Threat> threats;

  bool get hasPest => pestCount > 0 || threats.any((t) => t.cls != 'fungus');
  bool get hasFungus => fungusCoverage > 0 || threats.any((t) => t.cls == 'fungus');
  bool get needsAttention => hasPest || hasFungus;
  String? get latestThumb =>
      threats.map((t) => t.thumbPath).whereType<String>().firstOrNull;

  String get headline => hasPest && hasFungus
      ? 'Pest & fungus'
      : hasPest
          ? 'Pest detected'
          : hasFungus
              ? 'Fungus detected'
              : 'Healthy';

  factory Station.fromJson(Map<String, dynamic> j) {
    final scanned = j['last_scanned'];
    return Station(
      id: _i(j['id']),
      name: _s(j['name'], 'Lettuce ${_i(j['id'])}'),
      status: _s(j['status'], 'clear'),
      pestSeverity: _s(j['pest_severity_level'], 'none'),
      fungusSeverity: _s(j['fungus_severity_level'], 'none'),
      severity: _s(j['severity'], 'clear'),
      active: j['active'] == true,
      pestCount: _i(j['pest_count']),
      whiteflyCount: _i(j['whitefly_count']),
      wormCount: _i(j['worm_count']),
      fungusCoverage: _d(j['fungus_coverage_pct']),
      confidence: _d(j['confidence']),
      lastScanned: scanned is num
          ? DateTime.fromMillisecondsSinceEpoch((scanned * 1000).round())
          : null,
      description: _s(j['infestation_desc']),
      threats: [for (final t in _l(j['threats'])) Threat.fromJson(t)],
    );
  }
}

class Track {
  const Track({required this.id, required this.station, required this.position,
      required this.cls, required this.confidence, required this.firstSeen,
      required this.lastSeen, this.scansSeen = 1, this.status = 'active',
      this.thumbPath, this.snapshotPath, this.reviewed = false, this.falsePositive = false,
      this.coveragePct = 0});
  final String id, position, cls, status;
  final int station, scansSeen;
  final double confidence, coveragePct;
  final DateTime firstSeen, lastSeen;
  final String? thumbPath, snapshotPath;
  final bool reviewed, falsePositive;

  static DateTime _t(Object? v) =>
      DateTime.fromMillisecondsSinceEpoch((_d(v) * 1000).round());

  factory Track.fromJson(Map<String, dynamic> j) => Track(
        id: _s(j['id']),
        station: _i(j['station']),
        position: _s(j['position']),
        cls: _s(j['class']),
        confidence: _d(j['confidence']),
        firstSeen: _t(j['first_seen']),
        lastSeen: _t(j['last_seen']),
        scansSeen: _i(j['scans_seen'], 1),
        status: _s(j['status'], 'active'),
        thumbPath: j['thumb_path'] as String?,
        snapshotPath: j['snapshot_path'] as String?,
        reviewed: _i(j['reviewed']) == 1,
        falsePositive: _i(j['is_fp']) == 1,
        coveragePct: _d(j['coverage_pct']),
      );

  bool get stillThere => status == 'active';
}

class Cooldown {
  Cooldown({this.active = false, this.remaining = 0, this.total = 300, DateTime? endsAt, this.lastAt})
      : endsAt = endsAt ?? DateTime.now().add(Duration(milliseconds: (remaining * 1000).round()));
  final bool active;
  final double remaining, total;
  /// When the cooldown runs out on this phone's clock; widgets count down
  /// to it locally between polls.
  final DateTime endsAt;
  /// When it last fired (e.g. the last spray).
  final DateTime? lastAt;

  double get secondsLeft => (endsAt.difference(DateTime.now()).inMilliseconds / 1000).clamp(0, total).toDouble();

  factory Cooldown.fromJson(Map<String, dynamic> j) => Cooldown(
      active: j['active'] == true,
      remaining: _d(j['remaining_s']),
      total: _d(j['total_s'], 300),
      lastAt: j['last_triggered_at'] is num
          ? DateTime.fromMillisecondsSinceEpoch((_d(j['last_triggered_at']) * 1000).round())
          : null);
}

class Esp32Info {
  const Esp32Info({this.ip = '', this.reachable = false, this.scanning = false, this.error = ''});
  final String ip, error;
  final bool reachable, scanning;
  factory Esp32Info.fromJson(Map<String, dynamic> j) => Esp32Info(
      ip: _s(j['ip']),
      reachable: j['reachable'] == true,
      scanning: j['scanning'] == true,
      error: _s(j['last_error']));
}

class CaptureInfo {
  const CaptureInfo({this.running = false, this.done = 0, this.total = 0, this.last, this.error, this.storage});
  final bool running;
  final int done, total;
  final String? last, error;
  final StorageInfo? storage;
  factory CaptureInfo.fromJson(Map<String, dynamic> j) => CaptureInfo(
      running: j['running'] == true,
      done: _i(j['done']),
      total: _i(j['total']),
      last: j['last'] as String?,
      error: j['error'] as String?,
      storage: j['storage'] is Map ? StorageInfo.fromJson(_m(j['storage'])) : null);
}

/// Where the Pi saves dataset photos: the external SSD, or the SD card when no SSD is mounted.
class StorageInfo {
  const StorageInfo({required this.path, required this.onSsd, this.freeGb});
  final String path;
  final bool onSsd;
  final double? freeGb;
  factory StorageInfo.fromJson(Map<String, dynamic> j) => StorageInfo(
      path: _s(j['path']),
      onSsd: j['on_ssd'] == true,
      freeGb: j['free_gb'] is num ? _d(j['free_gb']) : null);
}

class Health {
  const Health({this.cameraOk = false, this.detectionOk = false, this.ready = false,
      this.fault, this.devMode = false, this.models = const []});
  final bool cameraOk, detectionOk, ready, devMode;
  final String? fault;
  final List<String> models;
  factory Health.fromJson(Map<String, dynamic> j) => Health(
      cameraOk: j['camera_ok'] == true,
      detectionOk: j['detection_ok'] == true,
      ready: j['ready'] == true,
      fault: j['fault'] as String?,
      devMode: j['dev_mode'] == true,
      models: [for (final m in (j['models'] as List? ?? const [])) '$m']);
}

/// The scan as the Pi reports it. Countdown values are ticked locally by
/// ScanStateModel; this object only changes when the Pi's state changes.
class ScanInfo {
  const ScanInfo({required this.raw});
  final Map<String, dynamic> raw;
  bool get running => raw['running'] == true;
  String get phase => _s(raw['phase'], 'IDLE');
  String get label => _s(raw['phase_label'], running ? 'Scanning' : 'Idle');
  int get station => _i(raw['station'], 1);
  String get position => _s(raw['position']);
  bool get detecting => raw['is_detecting'] == true;
  bool get hasClock =>
      raw['server_time'] is num && raw['phase_started_at'] is num && raw['phase_duration_s'] is num;
  Map<String, dynamic> get settings => _m(raw['settings']);
}

/// What the camera has CONFIRMED during the current lettuce visit (seen in 3
/// of 5 frames), before the scan window ends and history is written.
class LiveInfo {
  const LiveInfo({required this.station, required this.position, this.counts = const {},
      this.fungusPct = 0, this.sprayReason});
  final int station;
  final String position;
  final Map<String, int> counts;
  final double fungusPct;
  /// Set once the client's spray rule is met (e.g. "3 whiteflies on Lettuce 2").
  final String? sprayReason;

  bool get isEmpty => counts.values.every((n) => n == 0);

  /// "2 whiteflies, 1 worm, fungus 3.0%"
  String get summary {
    final parts = <String>[
      for (final cls in const ['whiteflies', 'worm'])
        if ((counts[cls] ?? 0) > 0)
          '${counts[cls]} ${cls == 'worm' ? (counts[cls] == 1 ? 'worm' : 'worms') : (counts[cls] == 1 ? 'whitefly' : 'whiteflies')}',
      if ((counts['fungus'] ?? 0) > 0) 'fungus ${fungusPct.toStringAsFixed(1)}%',
    ];
    return parts.isEmpty ? 'nothing confirmed yet' : parts.join(', ');
  }

  factory LiveInfo.fromJson(Map<String, dynamic> j) => LiveInfo(
      station: _i(j['station']),
      position: _s(j['position']),
      counts: {for (final e in _m(j['counts']).entries) e.key: _i(e.value)},
      fungusPct: _d(j['fungus_pct']),
      sprayReason: j['spray_reason'] as String?);
}

/// What the machine is doing that gets the full-screen pop-up: spraying
/// pesticide ("spray") or moving to a lettuce ("moving"). Never detections.
class Activity {
  const Activity({required this.kind, required this.title, this.body = ''});
  final String kind, title, body;

  factory Activity.fromJson(Map<String, dynamic> j) =>
      Activity(kind: _s(j['kind']), title: _s(j['title']), body: _s(j['body']));

  @override
  bool operator ==(Object other) =>
      other is Activity && other.kind == kind && other.title == title && other.body == body;
  @override
  int get hashCode => Object.hash(kind, title, body);
}

class AppSnapshot {
  const AppSnapshot({
    required this.rev,
    required this.build,
    required this.stations,
    required this.scan,
    required this.pumpCooldown,
    required this.smsCooldown,
    required this.buzzerMuted,
    required this.pumpActive,
    required this.esp32,
    required this.health,
    required this.capture,
    required this.recent,
    required this.modelColors,
    this.live,
    this.activity,
  });

  final String rev, build, modelColors;
  /// Confirmed right now at the lettuce being scanned (null when not detecting).
  final LiveInfo? live;
  /// Spraying / moving to a lettuce right now (null otherwise).
  final Activity? activity;
  final List<Station> stations;
  final ScanInfo scan;
  final Cooldown pumpCooldown, smsCooldown;
  final bool? buzzerMuted;
  final bool pumpActive;
  final Esp32Info esp32;
  final Health health;
  final CaptureInfo capture;
  final List<Track> recent;

  int get alertCount => stations.where((s) => s.needsAttention).length;

  factory AppSnapshot.fromJson(Map<String, dynamic> j) {
    final cooldowns = _m(j['cooldowns']);
    return AppSnapshot(
      rev: _s(j['rev']),
      build: _s(j['build']),
      modelColors: _s(j['model_colors']),
      stations: [for (final s in _l(j['stations'])) Station.fromJson(s)],
      scan: ScanInfo(raw: _m(j['scan'])),
      pumpCooldown: Cooldown.fromJson(_m(cooldowns['pump'])),
      smsCooldown: Cooldown.fromJson(_m(cooldowns['sms'])),
      buzzerMuted: cooldowns['buzzer_muted'] as bool?,
      pumpActive: _m(j['pump'])['active'] == true,
      esp32: Esp32Info.fromJson(_m(j['esp32'])),
      health: Health.fromJson(_m(j['health'])),
      capture: CaptureInfo.fromJson(_m(j['capture'])),
      recent: [for (final t in _l(j['recent'])) Track.fromJson(t)],
      live: j['live'] is Map ? LiveInfo.fromJson(_m(j['live'])) : null,
      activity: j['activity'] is Map && _s(_m(j['activity'])['title']).isNotEmpty
          ? Activity.fromJson(_m(j['activity']))
          : null,
    );
  }
}
