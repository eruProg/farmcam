import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/screens/dashboard_screen.dart';

void main() {
  testWidgets(
      'dashboard keeps existing controls without video or replacement cards',
      (tester) async {
    final paths = <String>[];
    var online = true;
    await http.runWithClient(() async {
      await tester.pumpWidget(
          const MaterialApp(home: DashboardScreen(rpiIp: '192.0.2.1')));
      await tester.pump();
      await tester.pump(const Duration(seconds: 1));
      expect(find.byKey(const ValueKey('scan_status_summary')), findsNothing);
      expect(find.byKey(const ValueKey('live_stream_container')), findsNothing);
      expect(find.byKey(const ValueKey('mjpeg_viewer')), findsNothing);
      expect(find.text('Manual Pump'), findsOneWidget);
      expect(find.text('Scan timing / Start / Stop'), findsOneWidget);
      for (var i = 0; i < 5; i++) {
        await tester.pump(const Duration(seconds: 1));
      }
      online = false;
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(seconds: 1));
      }
      expect(paths, contains('/api/system'));
      expect(paths.where((p) => p.contains('stream') || p.contains('video')),
          isEmpty);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 8));
    },
        () => MockClient((request) async {
              paths.add(request.url.path);
              if (request.url.path == '/api/system') {
                return online
                    ? http.Response(
                        jsonEncode({
                          'camera_active': true,
                          'scan_state': {
                            'phase': 'DETECT_LOWER',
                            'station': 3,
                            'position': 'lower',
                            'phase_started_at': 100.0,
                            'phase_duration_s': 60.0,
                            'server_time': 110.0,
                            'remaining_s': 50.0,
                          },
                        }),
                        200)
                    : http.Response('{}', 503);
              }
              if (request.url.path == '/api/lettuce') {
                return http.Response('[]', 200);
              }
              return http.Response('{}', 200);
            }));
  });
}
