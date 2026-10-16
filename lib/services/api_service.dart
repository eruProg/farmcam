import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import '../data/app_state.dart';

class PumpCommandResult {
  final bool ok;
  final String? error;
  const PumpCommandResult(this.ok, [this.error]);
}

/// HTTP API client for FarmCam RPi5 server.
class ApiService {
  final AppState _appState;

  ApiService(this._appState);

  String get _base => _appState.baseUrl;

  // ─── Connectivity ──────────────────────────────────────────────

  /// Quick connectivity check.
  Future<bool> ping() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/'))
          .timeout(const Duration(seconds: 4));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ─── Stations / Lettuce ────────────────────────────────────────

  Future<List<LettuceStation>> fetchStations() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/lettuce'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final List<dynamic> data = jsonDecode(resp.body);
        return data.map((e) => LettuceStation.fromJson(e)).toList();
      }
    } catch (_) {}
    return [];
  }

  Future<LettuceStation?> fetchStation(int id) async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/lettuce/$id'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return LettuceStation.fromJson(jsonDecode(resp.body));
      }
    } catch (_) {}
    return null;
  }

  // ─── System Info ───────────────────────────────────────────────

  Future<SystemInfo?> fetchSystemInfo() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/system'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final json = jsonDecode(resp.body);
        return SystemInfo(
          uptime: json['uptime'] ?? '--',
          cameraActive: json['camera_active'] ?? false,
          activeStationId: json['active_station'] as int?,
        );
      }
    } catch (_) {}
    return null;
  }

  // ─── Status (full dashboard poll) ──────────────────────────────

  Future<Map<String, dynamic>?> fetchStatus() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/status'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body);
      }
    } catch (_) {}
    return null;
  }

  // ─── Detection History ─────────────────────────────────────────

  Future<List<Detection>> fetchHistory(int stationId) async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/history/$stationId'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final List<dynamic> data = jsonDecode(resp.body);
        return data.map((e) => Detection.fromJson(e)).toList();
      }
    } catch (_) {}
    return [];
  }

  // ─── Pump Control ──────────────────────────────────────────────

  Future<Map<String, dynamic>?> fetchPumpStatus() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/pump/status'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final json = jsonDecode(resp.body);
        return {
          'pump_active': json['active'] ?? json['running'] ?? false,
          'pump_cooldown': json['cooldown'] ?? 'Ready',
          'pump_ready': !(json['active'] ?? false),
        };
      }
    } catch (_) {}
    return null;
  }

  /// Trigger a timed pump spray (auto-detection driven).
  Future<bool> triggerPump() async {
    try {
      final resp = await http
          .post(Uri.parse('$_base/api/pump/trigger'))
          .timeout(const Duration(seconds: 5));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<PumpCommandResult> _pumpCommand(String action,
      {bool requireActive = false}) async {
    try {
      final resp = await http
          .post(Uri.parse('$_base/api/pump/$action'))
          .timeout(Duration(seconds: action == 'heartbeat' ? 2 : 6));
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      if (resp.statusCode == 200 &&
          data['ok'] == true &&
          (!requireActive || data['active'] == true)) {
        return const PumpCommandResult(true);
      }
      return PumpCommandResult(
          false,
          data['error']?.toString() ??
              'Pump command was not confirmed. Check the Pi-to-ESP32 connection.');
    } catch (_) {
      return const PumpCommandResult(false,
          'Cannot confirm the pump command. Check the Raspberry Pi connection.');
    }
  }

  Future<PumpCommandResult> pumpHold() =>
      _pumpCommand('hold', requireActive: true);

  Future<PumpCommandResult> pumpRelease() => _pumpCommand('release');

  Future<PumpCommandResult> pumpHeartbeat() =>
      _pumpCommand('heartbeat', requireActive: true);

  // ─── Image Detection ──────────────────────────────────────────

  Future<Map<String, dynamic>?> detectImage(File imageFile) async {
    try {
      final request =
          http.MultipartRequest('POST', Uri.parse('$_base/api/detect'));
      request.files
          .add(await http.MultipartFile.fromPath('image', imageFile.path));
      final streamedResp =
          await request.send().timeout(const Duration(seconds: 30));
      final resp = await http.Response.fromStream(streamedResp);
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body);
      }
    } catch (_) {}
    return null;
  }

  // ─── Scan Status (phase / countdown) ─────────────────────────

  /// Returns the server-authoritative scan state dict from /api/system.
  /// Keys: phase, phase_label, position, station, scan_id,
  ///       remaining_s, is_detecting, phase_duration_s, progress,
  ///       phase_started_at, server_time.
  Future<Map<String, dynamic>?> getScanStatus() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/scan/state'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        // Extract nested scan_state dict if present
        if (data.containsKey('scan_state')) {
          return data['scan_state'] as Map<String, dynamic>?;
        }
        return data;
      }
    } catch (_) {}
    return null;
  }

  // ─── Detection Track Logs ─────────────────────────────────────

  /// Paginated track list from /api/logs.
  /// Returns {"items": [...], "total": n, "pages": n, "page": n}.
  Future<Map<String, dynamic>> getTracksPage({
    int? station,
    String? cls,
    String? status,
    int page = 1,
    int pageSize = 20,
    bool? isFalsePositive,
    DateTime? dateFrom,
    DateTime? dateTo,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (station != null) params['station'] = station.toString();
    if (cls != null) params['class'] = cls;
    if (status != null) params['status'] = status;
    if (isFalsePositive != null) params['is_fp'] = isFalsePositive ? '1' : '0';
    if (dateFrom != null) {
      params['date_from'] = (dateFrom.millisecondsSinceEpoch / 1000).toString();
    }
    if (dateTo != null) {
      params['date_to'] = (dateTo.millisecondsSinceEpoch / 1000).toString();
    }

    return _json('/api/logs?${Uri(queryParameters: params).query}');
  }

  Future<Map<String, dynamic>> _json(String route,
      {bool post = false, Map<String, dynamic>? body}) async {
    final uri = Uri.parse('$_base$route');
    final response = await (post
            ? http.post(uri,
                headers: {'Content-Type': 'application/json'},
                body: jsonEncode(body ?? {}))
            : http.get(uri))
        .timeout(const Duration(seconds: 6));
    final data = jsonDecode(response.body);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception(
          data is Map ? data['detail'] ?? 'Request failed' : 'Request failed');
    }
    if (data is! Map<String, dynamic>) {
      throw const FormatException('Invalid server response');
    }
    return data;
  }

  Future<Map<String, dynamic>> fetchConfig() => _json('/api/config');
  Future<Map<String, dynamic>> fetchPrecision() => _json('/api/precision');
  Future<Map<String, dynamic>> testSms([String? phone]) => _json(
      '/api/sms/test${phone == null ? '' : '?phone=${Uri.encodeQueryComponent(phone)}'}',
      post: true);
  Future<bool> setBuzzerMute(bool muted) async {
    try {
      final data =
          await _json('/api/buzzer/mute', post: true, body: {'muted': muted});
      return data['ok'] == true && data['muted'] == muted;
    } catch (_) {
      return false;
    }
  }

  Future<bool> markReviewed(String id) async {
    try {
      return (await _json('/api/logs/${Uri.encodeComponent(id)}/review',
              post: true))['ok'] ==
          true;
    } catch (_) {
      return false;
    }
  }

  String snapshotUrl(String relativePath) =>
      '$_base/api/snapshots/${relativePath.split('/').map(Uri.encodeComponent).join('/')}';

  /// Mark a track as a false positive.  Returns true on success.
  Future<bool> markFalsePositive(String trackId) async {
    try {
      final resp = await http
          .post(Uri.parse('$_base/api/logs/$trackId/false-positive'))
          .timeout(const Duration(seconds: 6));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ─── Cooldown Status ─────────────────────────────────────────

  /// Returns {"pump": {...}, "sms": {...}} with remaining_s and total_s.
  Future<Map<String, dynamic>?> getCooldownStatus() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/cooldowns'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        return data['pump'] is Map && data['sms'] is Map ? data : null;
      }
    } catch (_) {}
    return null;
  }

  // ─── SMS Status ──────────────────────────────────────────────

  /// Returns last SMS delivery status from /api/sms/status.
  Future<Map<String, dynamic>?> getSmsStatus() async {
    try {
      final resp = await http
          .get(Uri.parse('$_base/api/sms/status'))
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return null;
  }
}
