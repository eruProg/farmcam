import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/data/app_state.dart';
import 'package:farmcam/services/api_service.dart';

void main() {
  final state = AppState()..setConnection('192.0.2.1', 8000);
  final api = ApiService(state);

  Future<PumpCommandResult> hold(int status, Map<String, dynamic> body) =>
      http.runWithClient(
          () => api.pumpHold(),
          () =>
              MockClient((_) async => http.Response(jsonEncode(body), status)));

  test('HTTP 200 with failed device acknowledgement is not success', () async {
    expect((await hold(200, {'ok': false, 'active': true})).ok, isFalse);
  });
  test('hold requires acknowledgement and active state', () async {
    expect((await hold(200, {'ok': true, 'active': false})).ok, isFalse);
    expect((await hold(200, {'ok': true, 'active': true})).ok, isTrue);
  });
  test('ESP32 error is shown to the caller', () async {
    final result = await hold(503, {'ok': false, 'error': 'ESP32 unreachable'});
    expect(result.ok, isFalse);
    expect(result.error, 'ESP32 unreachable');
  });
  test('idle heartbeat stops being treated as a live pump', () async {
    final result = await http.runWithClient(
        () => api.pumpHeartbeat(),
        () => MockClient(
            (_) async => http.Response('{"ok":true,"active":false}', 200)));
    expect(result.ok, isFalse);
  });
  test('confirmed release succeeds with active false', () async {
    final result = await http.runWithClient(
        () => api.pumpRelease(),
        () => MockClient(
            (_) async => http.Response('{"ok":true,"active":false}', 200)));
    expect(result.ok, isTrue);
  });
}
