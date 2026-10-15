import 'dart:async';
import 'dart:math';
import 'package:flutter/material.dart';

// ─── Data Models ─────────────────────────────────────────────────

enum LettuceStatus { clear, pest, fungus, both, unknown }

class Detection {
  final String label;
  final double confidence;
  final DateTime timestamp;
  final LettuceStatus status;

  Detection({
    required this.label,
    required this.confidence,
    required this.timestamp,
    required this.status,
  });

  factory Detection.fromJson(Map<String, dynamic> json) {
    return Detection(
      label: json['label'] ?? 'Unknown',
      confidence: (json['confidence'] ?? 0.0).toDouble(),
      timestamp: json['timestamp'] != null
          ? DateTime.parse(json['timestamp'])
          : DateTime.now(),
      status: parseStatus(json['status'] ?? json['label'] ?? ''),
    );
  }

  static LettuceStatus parseStatus(String s) {
    final lower = s.toLowerCase();
    if (lower.contains('both')) {
      return LettuceStatus.both;
    }
    if (lower.contains('whitefl') || lower.contains('worm')) {
      return LettuceStatus.pest;
    }
    if (lower.contains('fungus') || lower.contains('infestation')) {
      return LettuceStatus.fungus;
    }
    if (lower.contains('clear') || lower.contains('healthy') || lower.contains('none')) {
      return LettuceStatus.clear;
    }
    return LettuceStatus.unknown;
  }
}

class LettuceStation {
  final int id;
  final String name;
  LettuceStatus status;
  double confidence;
  DateTime? lastCheck;
  int pestCount;
  int fungusCount;
  List<Detection> history;
  // ── Severity & informative fields (new) ──
  // BUG FIX: pestSeverityLevel and fungusSeverityLevel are now tracked
  // separately. severityLevel is kept only as a legacy combined fallback —
  // prefer the two independent fields below wherever you display severity,
  // since one problem being "high" must never hide the other's own level.
  String severityLevel;       // legacy combined 'none'|'low'|'medium'|'high'
  String pestSeverityLevel;   // independent pest (whiteflies/worm) severity
  String fungusSeverityLevel; // independent fungus severity
  double fungusCoveragePct;   // 0.0 – 100.0
  List<String> pestTypes;     // e.g. ['Whiteflies', 'Worm']
  String infestationDesc;     // Human-readable description

  LettuceStation({
    required this.id,
    required this.name,
    this.status = LettuceStatus.unknown,
    this.confidence = 0.0,
    this.lastCheck,
    this.pestCount = 0,
    this.fungusCount = 0,
    List<Detection>? history,
    this.severityLevel = 'none',
    this.pestSeverityLevel = 'none',
    this.fungusSeverityLevel = 'none',
    this.fungusCoveragePct = 0.0,
    List<String>? pestTypes,
    this.infestationDesc = '',
  }) : history = history ?? [],
       pestTypes = pestTypes ?? [];

  factory LettuceStation.fromJson(Map<String, dynamic> json) {
    // Parse pest_types — stored as comma-separated string or JSON list
    List<String> types = [];
    final rawTypes = json['pest_types'];
    if (rawTypes is List) {
      types = rawTypes.map((e) => e.toString()).toList();
    } else if (rawTypes is String && rawTypes.isNotEmpty) {
      types = rawTypes.split(',').map((e) => e.trim()).where((e) => e.isNotEmpty).toList();
    }

    return LettuceStation(
      id: json['id'] ?? 0,
      name: json['name'] ?? 'Lettuce',
      status: Detection.parseStatus(json['status'] ?? ''),
      confidence: (json['confidence'] ?? 0.0).toDouble(),
      lastCheck: json['last_check'] != null ? DateTime.parse(json['last_check']) : null,
      pestCount: json['pest_count'] ?? 0,
      fungusCount: json['fungus_count'] ?? 0,
      severityLevel: json['severity_level'] ?? 'none',
      pestSeverityLevel: json['pest_severity_level'] ?? json['severity_level'] ?? 'none',
      fungusSeverityLevel: json['fungus_severity_level'] ?? 'none',
      fungusCoveragePct: (json['fungus_coverage_pct'] ?? 0.0).toDouble(),
      pestTypes: types,
      infestationDesc: json['infestation_desc'] ?? '',
    );
  }

  Color get statusColor {
    switch (status) {
      case LettuceStatus.clear:   return const Color(0xFF16A34A);
      case LettuceStatus.pest:    return const Color(0xFFDC2626);
      case LettuceStatus.fungus:  return const Color(0xFFD97706);
      case LettuceStatus.both:    return const Color(0xFFDC2626);
      case LettuceStatus.unknown: return const Color(0xFF71717A);
    }
  }

  String get statusLabel {
    switch (status) {
      case LettuceStatus.clear:   return 'Clear';
      case LettuceStatus.pest:    return 'Pest';
      case LettuceStatus.fungus:  return 'Fungus';
      case LettuceStatus.both:    return 'Pest & Fungus';
      case LettuceStatus.unknown: return 'Checking…';
    }
  }

  IconData get statusIcon {
    switch (status) {
      case LettuceStatus.clear:   return Icons.check_circle_outline_rounded;
      case LettuceStatus.pest:    return Icons.bug_report_outlined;
      case LettuceStatus.fungus:  return Icons.coronavirus_outlined;
      case LettuceStatus.both:    return Icons.report_problem_outlined;
      case LettuceStatus.unknown: return Icons.help_outline_rounded;
    }
  }
}

class SystemInfo {
  final double cpuTemp;
  final double cpuUsage;
  final double memUsage;
  final String uptime;
  final bool buzzerActive;
  final bool cameraActive;
  final int? activeStationId;

  SystemInfo({
    this.cpuTemp = 0,
    this.cpuUsage = 0,
    this.memUsage = 0,
    this.uptime = '--',
    this.buzzerActive = false,
    this.cameraActive = false,
    this.activeStationId,
  });

  factory SystemInfo.fromJson(Map<String, dynamic> json) {
    return SystemInfo(
      cpuTemp: (json['cpu_temp'] ?? 0).toDouble(),
      cpuUsage: (json['cpu_usage'] ?? 0).toDouble(),
      memUsage: (json['mem_usage'] ?? 0).toDouble(),
      uptime: json['uptime'] ?? '--',
      buzzerActive: json['buzzer_active'] ?? false,
      cameraActive: json['camera_active'] ?? false,
      activeStationId: json['active_station_id'],
    );
  }
}

// ─── App State ───────────────────────────────────────────────────

class AppState extends ChangeNotifier {
  // Connection
  String _rpiIp = '';
  int _rpiPort = 8000;
  bool _isConnected = false;
  bool _isConnecting = false;
  String _connectionError = '';
  bool _demoMode = false;

  // Data
  List<LettuceStation> stations = [];
  SystemInfo systemInfo = SystemInfo();
  Timer? _pollTimer;

  // Pump state (from /api/pump/status - Single Pump system)
  bool pumpActive = false;
  String pumpCooldown = 'Ready';
  bool pumpReady = true;
  // Legacy dual-pump aliases for backwards compatibility
  bool pump1Active = false;
  bool pump2Active = false;
  String pump1Cooldown = 'Ready';
  String pump2Cooldown = 'Ready';
  bool pump1Ready = true;
  bool pump2Ready = true;

  // Getters
  String get rpiIp => _rpiIp;
  int get rpiPort => _rpiPort;
  bool get isConnected => _isConnected;
  bool get isConnecting => _isConnecting;
  String get connectionError => _connectionError;
  bool get demoMode => _demoMode;
  String get streamUrl => 'http://$_rpiIp:$_rpiPort/api/stream';
  String get baseUrl => 'http://$_rpiIp:$_rpiPort';

  int get clearCount => stations.where((s) => s.status == LettuceStatus.clear).length;
  int get alertCount => stations.where((s) => s.status == LettuceStatus.pest || s.status == LettuceStatus.fungus || s.status == LettuceStatus.both).length;

  AppState() {
    _initStations();
  }

  void _initStations() {
    stations = List.generate(4, (i) => LettuceStation(
      id: i + 1,
      name: 'Lettuce ${i + 1}',
    ));
  }

  void setConnection(String ip, int port) {
    _rpiIp = ip;
    _rpiPort = port;
    notifyListeners();
  }

  void setConnected(bool connected) {
    _isConnected = connected;
    _isConnecting = false;
    _connectionError = '';
    if (connected) _startPolling();
    notifyListeners();
  }

  void setConnecting() {
    _isConnecting = true;
    _connectionError = '';
    notifyListeners();
  }

  void setError(String error) {
    _connectionError = error;
    _isConnecting = false;
    _isConnected = false;
    notifyListeners();
  }

  void disconnect() {
    _isConnected = false;
    _pollTimer?.cancel();
    _initStations();
    notifyListeners();
  }

  // ─── Demo Mode ─────────────────────────────────────────────────

  void enableDemoMode() {
    _demoMode = true;
    _isConnected = true;
    _rpiIp = 'demo';
    _generateDemoData();
    _startPolling();
    notifyListeners();
  }

  void _generateDemoData() {
    final rng = Random();
    final statuses = [
      LettuceStatus.clear,
      LettuceStatus.pest,
      LettuceStatus.fungus,
      LettuceStatus.both,
    ];
    statuses.shuffle();

    for (int i = 0; i < 4; i++) {
      final s = statuses[i];
      stations[i].status    = s;
      stations[i].confidence = 0.75 + rng.nextDouble() * 0.24;
      stations[i].lastCheck = DateTime.now().subtract(Duration(minutes: rng.nextInt(30)));
      stations[i].pestCount  = (s == LettuceStatus.pest || s == LettuceStatus.both) ? rng.nextInt(5) + 1 : 0;
      stations[i].fungusCount = (s == LettuceStatus.fungus || s == LettuceStatus.both) ? rng.nextInt(3) + 1 : 0;
      // Severity & coverage demo data — pest and fungus are computed and
      // shown independently, exactly like the real API response, so a
      // demo "both" station can show e.g. Pest: MEDIUM and Fungus: HIGH
      // at the same time instead of one hiding the other.
      stations[i].pestSeverityLevel = 'none';
      stations[i].fungusSeverityLevel = 'none';
      stations[i].fungusCoveragePct = 0.0;
      stations[i].pestTypes = [];
      final descParts = <String>[];
      if (s == LettuceStatus.pest || s == LettuceStatus.both) {
        final count = stations[i].pestCount;
        stations[i].pestSeverityLevel = count >= 5 ? 'high' : count >= 2 ? 'medium' : 'low';
        stations[i].pestTypes = rng.nextBool() ? ['Whiteflies'] : ['Whiteflies', 'Worm'];
        descParts.add('${stations[i].pestTypes.join(' & ')} detected — $count insect(s) found (${stations[i].pestSeverityLevel.toUpperCase()})');
      }
      if (s == LettuceStatus.fungus || s == LettuceStatus.both) {
        final cov = 5.0 + rng.nextDouble() * 55;
        stations[i].fungusSeverityLevel = cov >= 15 ? 'high' : cov >= 5 ? 'medium' : 'low';
        stations[i].fungusCoveragePct = cov;
        descParts.add('Fungal disease — ${cov.toStringAsFixed(1)}% leaf coverage (${stations[i].fungusSeverityLevel.toUpperCase()})');
      }
      // Legacy combined field, kept only for anything still reading it.
      const order = {'none': 0, 'low': 1, 'medium': 2, 'high': 3};
      stations[i].severityLevel = order[stations[i].pestSeverityLevel]! >= order[stations[i].fungusSeverityLevel]!
          ? stations[i].pestSeverityLevel
          : stations[i].fungusSeverityLevel;
      stations[i].infestationDesc = descParts.join(' | ');
    }

    systemInfo = SystemInfo(
      cpuTemp: 45 + rng.nextDouble() * 15,
      cpuUsage: 30 + rng.nextDouble() * 40,
      memUsage: 40 + rng.nextDouble() * 30,
      uptime: '2h 34m',
      buzzerActive: stations.any((s) => s.status == LettuceStatus.pest || s.status == LettuceStatus.fungus || s.status == LettuceStatus.both),
      cameraActive: true,
      activeStationId: rng.nextBool() ? rng.nextInt(4) + 1 : null,
    );
  }

  void _startPolling() {
    _pollTimer?.cancel();
    if (_demoMode) {
      _pollTimer = Timer.periodic(const Duration(seconds: 8), (_) {
        _generateDemoData();
        notifyListeners();
      });
    }
  }

  void updateStations(List<LettuceStation> newStations) {
    stations = newStations;
    notifyListeners();
  }

  void updateSystemInfo(SystemInfo info) {
    systemInfo = info;
    notifyListeners();
  }

  void updatePumpStatus(Map<String, dynamic> data) {
    _applyPumpStatus(data);
    notifyListeners();
  }

  void _applyPumpStatus(Map<String, dynamic> data) {
    pumpActive = data['pump_active'] ?? data['pump1_active'] ?? false;
    pumpCooldown = data['pump_cooldown'] ?? data['pump1_cooldown'] ?? 'Ready';
    pumpReady = data['pump_ready'] ?? data['pump1_ready'] ?? true;
    pump1Active = pumpActive;
    pump2Active = false;
    pump1Cooldown = pumpCooldown;
    pump2Cooldown = 'N/A';
    pump1Ready = pumpReady;
    pump2Ready = true;
  }

  /// Applies a full poll cycle's results (stations + system info + pump)
  /// in one shot with a SINGLE notifyListeners() call.
  ///
  /// Previously the dashboard's poll loop called updateStations(),
  /// updateSystemInfo() and updatePumpStatus() separately — 3 separate
  /// notifyListeners() calls — AND wrapped all three in its own setState()
  /// on top of that. Since the whole dashboard tree (video feed, station
  /// grid, banners) sits under one ListenableBuilder listening to this
  /// object, that meant the ENTIRE screen was rebuilt up to 4 times every
  /// single 2-second poll tick — the main cause of the dashboard feeling
  /// like it "refreshes" constantly. Now it's built once, notified once.
  void applyPollResult({
    List<LettuceStation>? newStations,
    SystemInfo? info,
    Map<String, dynamic>? pumpData,
  }) {
    if (newStations != null && newStations.isNotEmpty) stations = newStations;
    if (info != null) systemInfo = info;
    if (pumpData != null) _applyPumpStatus(pumpData);
    notifyListeners();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }
}
