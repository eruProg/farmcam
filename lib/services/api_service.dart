import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../data/app_state.dart';

class PumpCommandResult {
  final bool ok;
  final String? error;
  /// Seconds left when the Pi refused a manual spray because the automatic
  /// spray cooldown is running (HTTP 409 "cooldown"); null otherwise.
  final double? cooldownLeft;
  const PumpCommandResult(this.ok, [this.error, this.cooldownLeft]);
  bool get blockedByCooldown => cooldownLeft != null;
}

/// HTTP API client for FarmCam RPi5 server.
class ApiService {
  final AppState _appState;

  ApiService(this._appState);

  String get _base => _appState.baseUrl;

  // ─── Connectivity ──────────────────────────────────────────────

  /// Quick connectivity check.
  Future<Map<String, dynamic>> scanRequest(String action,
      {Map<String, dynamic>? body}) async {
    final uri = Uri.parse('$_base/api/scan/$action');
    final response = await (body == null
            ? http.get(uri)
            : http.post(uri,
                headers: {'Content-Type': 'application/json'},
                body: jsonEncode(body)))
        .timeout(const Duration(seconds: 6));
    if (response.statusCode == 404 || response.statusCode == 405) {
      throw Exception('Update the Pi server to use adjustable scan timing.');
    }
    if (response.statusCode != 200) {
      throw Exception(
          'Scan request failed (${response.statusCode}): ${response.body}');
    }
    return Map<String, dynamic>.from(jsonDecode(response.body));
  }

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
          rawJson: json is Map<String, dynamic> ? json : null,
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

  /// A 2.5 s manual spray. [override]: the user confirmed spraying during the
  /// automatic-spray cooldown (the Pi refuses with 409 otherwise).
  Future<PumpCommandResult> triggerPump({bool override = false}) =>
      _pumpCommand('trigger', override: override);

  Future<PumpCommandResult> _pumpCommand(String action,
      {bool requireActive = false, bool override = false}) async {
    http.Response? resp;
    try {
      // The Pi answers within ~4s even when the ESP32 is offline.
      resp = await http
          .post(Uri.parse('$_base/api/pump/$action${override ? '?override=1' : ''}'))
          .timeout(Duration(seconds: action == 'heartbeat' ? 2 : 8));
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      if (resp.statusCode == 409 && data['error'] == 'cooldown') {
        return PumpCommandResult(false, data['message']?.toString() ?? 'Automatic spraying is paused.',
            (data['remaining_s'] as num?)?.toDouble() ?? 0);
      }
      if (resp.statusCode == 200 &&
          data['ok'] == true &&
          (!requireActive || data['active'] == true)) {
        return const PumpCommandResult(true);
      }
      return PumpCommandResult(
          false,
          data['error']?.toString() ??
              'Pump command was not confirmed. Check the Pi-to-ESP32 connection.');
    } on TimeoutException {
      return const PumpCommandResult(false,
          'Raspberry Pi did not answer in time. The ESP32 pump controller may be offline.');
    } on FormatException {
      return PumpCommandResult(false,
          'Unexpected reply from the Raspberry Pi (HTTP ${resp?.statusCode}). Is the server up to date?');
    } catch (_) {
      return PumpCommandResult(
          false, 'Cannot reach the Raspberry Pi at $_base. Check the Wi-Fi and IP.');
    }
  }

  Future<PumpCommandResult> pumpHold({bool override = false}) =>
      _pumpCommand('hold', requireActive: true, override: override);

  Future<PumpCommandResult> pumpRelease() => _pumpCommand('release');

  Future<PumpCommandResult> pumpHeartbeat() =>
      _pumpCommand('heartbeat', requireActive: true);

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
  /// Creates a "Test notification" event on the Pi; the phone's background
  /// watcher turns it into a notification within ~30 s.
  Future<Map<String, dynamic>> sendTestEvent() => _json('/api/events/test', post: true);

  /// Shut down ('shutdown') or restart ('restart') the Pi. The Pi stops the
  /// scan and the pump first.
  /// null when the Pi accepted; otherwise why not (e.g. no permission to
  /// power off, which the Pi now says instead of silently staying on).
  Future<String?> systemPower(String action) async {
    try {
      final data = await _json('/api/system/power', post: true, body: {'action': action});
      return data['ok'] == true ? null : 'FarmCam did not accept the request.';
    } catch (e) {
      final text = '$e';
      return text.startsWith('Exception: ')
          ? text.substring('Exception: '.length)
          : 'FarmCam did not answer. Check the connection and try again.';
    }
  }
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

  // ─── Redesigned app: one-call state, discovery, datasets, reports ──

  String get baseUrl => _base;

  /// Everything the app shows, in one request. Throws on failure so the
  /// controller can count misses and reconnect.
  Future<Map<String, dynamic>> appState() =>
      _json('/api/app/state').timeout(const Duration(seconds: 5));

  /// Single full-resolution photo for datasets.
  Future<Map<String, dynamic>> captureFull(
          {int? station, String? position, String note = ''}) =>
      _json('/api/capture/full', post: true, body: {
        if (station != null) 'station': station,
        if (position != null) 'position': position,
        if (note.isNotEmpty) 'note': note,
      }).timeout(const Duration(seconds: 15));

  Future<Map<String, dynamic>> captureBurst(
          {required int count,
          required double intervalS,
          int? station,
          String? position,
          String note = ''}) =>
      _json('/api/capture/burst', post: true, body: {
        'count': count,
        'interval_s': intervalS,
        if (station != null) 'station': station,
        if (position != null) 'position': position,
        'note': note,
      });

  Future<Map<String, dynamic>> captures({int page = 1, int pageSize = 30}) =>
      _json('/api/captures?page=$page&page_size=$pageSize');

  Future<bool> deleteCapture(String name) async {
    try {
      final resp = await http
          .delete(Uri.parse('$_base/api/captures/${Uri.encodeComponent(name)}'))
          .timeout(const Duration(seconds: 6));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  String captureUrl(String name) => '$_base/api/captures/${Uri.encodeComponent(name)}';
  String captureThumbUrl(String name) => '${captureUrl(name)}/thumb';

  Future<Map<String, dynamic>> reportSummary({int days = 14}) =>
      _json('/api/reports/summary?days=$days');

  String get reportCsvUrl => '$_base/api/reports/export.csv';

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
