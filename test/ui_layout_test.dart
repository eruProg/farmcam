import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/data/app_state.dart';
import 'package:farmcam/services/api_service.dart';
import 'package:farmcam/screens/dashboard_screen.dart';
import 'package:farmcam/screens/about_screen.dart';
import 'package:farmcam/screens/logs_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    final loader = FontLoader('Inter')
      ..addFont(rootBundle.load('assets/fonts/Inter.ttf'));
    await loader.load();
    final icons = FontLoader('MaterialIcons')
      ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
    await icons.load();
  });
  for (final size in [const Size(360, 800), const Size(768, 1024)]) {
    for (final scale in [1.0, 1.5]) {
      for (final screen in ['dashboard', 'logs', 'about']) {
        testWidgets('$screen at ${size.width} and text $scale', (tester) async {
          tester.view.physicalSize = size;
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          final api = ApiService(AppState()..setConnection('192.0.2.1', 8000));
          final boundary = GlobalKey();
          await http.runWithClient(() async {
            final child = screen == 'dashboard'
                ? const DashboardScreen(rpiIp: '192.0.2.1')
                : screen == 'logs'
                    ? LogsScreen(api: api)
                    : AboutScreen(api: api);
            await tester.pumpWidget(MaterialApp(
                theme: ThemeData(
                    fontFamily: 'Inter',
                    useMaterial3: true,
                    scaffoldBackgroundColor: const Color(0xFFFAFAF9),
                    colorScheme: ColorScheme.fromSeed(
                        seedColor: const Color(0xFF176344))),
                builder: (context, child) => MediaQuery(
                    data: MediaQuery.of(context)
                        .copyWith(textScaler: TextScaler.linear(scale)),
                    child: RepaintBoundary(key: boundary, child: child!)),
                home: child));
            await tester.pump();
            await tester.pump(const Duration(seconds: 1));
            expect(tester.takeException(), isNull);
            if (screen == 'logs') {
              expect(find.text('No detections'), findsOneWidget);
            }
            if (screen == 'dashboard') {
              expect(find.text('Disconnected'), findsOneWidget);
            }
            if (screen == 'about') {
              await tester.tap(find.text('Send test SMS'));
              await tester.pump();
              expect(find.textContaining('SMS unavailable'), findsOneWidget);
            }
            await tester.runAsync(() async {
              final image = await (boundary.currentContext!.findRenderObject()
                      as RenderRepaintBoundary)
                  .toImage();
              final data =
                  await image.toByteData(format: ui.ImageByteFormat.png);
              await Directory('.verification/ui').create(recursive: true);
              await File(
                      '.verification/ui/$screen-${size.width.toInt()}-$scale.png')
                  .writeAsBytes(data!.buffer.asUint8List());
              image.dispose();
            });
            await tester.pumpWidget(const SizedBox());
            await tester.pump(const Duration(seconds: 8));
          },
              () => MockClient((request) async {
                    switch (request.url.path) {
                      case '/api/lettuce':
                        return http.Response('[]', 200);
                      case '/api/logs':
                        return http.Response(
                            '{"items":[],"total":0,"pages":1}', 200);
                      case '/api/scan/state':
                        return http.Response('{"detail":"Offline"}', 503);
                      case '/api/sms/test':
                        return http.Response(
                            '{"detail":"SMS unavailable"}', 503);
                      case '/api/config':
                        return http.Response(
                            '{"cycle_time_secs":333,"buzzer_pin":26,"esp32_ip":"192.0.2.1","esp32_port":80}',
                            200);
                      default:
                        return http.Response('{}', 200);
                    }
                  }));
        });
      }
    }
  }
}
