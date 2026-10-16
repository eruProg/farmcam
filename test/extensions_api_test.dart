import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/data/app_state.dart';
import 'package:farmcam/services/api_service.dart';

void main() {
  final api = ApiService(AppState()..setConnection('192.0.2.1', 8000));
  test('mute requires confirmed server state', () async {
    final ok = await http.runWithClient(
        () => api.setBuzzerMute(true),
        () => MockClient((request) async {
              expect(jsonDecode(request.body), {'muted': true});
              return http.Response('{"ok":true,"muted":false}', 200);
            }));
    expect(ok, isFalse);
  });
  test('history errors do not become an empty successful result', () async {
    await http.runWithClient(() async {
      await expectLater(api.getTracksPage(), throwsException);
    },
        () => MockClient((_) async =>
            http.Response('{"detail":"Storage unavailable"}', 503)));
  });
  test('all logs omits status filter and phone is encoded', () async {
    await http.runWithClient(() async {
      await api.getTracksPage();
      await api.testSms('+639123456789');
    },
        () => MockClient((request) async {
              if (request.url.path.endsWith('logs')) {
                expect(
                    request.url.queryParameters.containsKey('status'), isFalse);
              } else {
                expect(request.url.queryParameters['phone'], '+639123456789');
              }
              return http.Response('{}', 200);
            }));
  });
}
