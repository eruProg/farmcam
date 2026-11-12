import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:farmcam/core/discovery.dart';
import 'package:farmcam/core/farmcam_controller.dart';
import 'package:farmcam/main.dart';
import 'package:farmcam/screens/controls_page.dart';
import 'helpers.dart';

Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('spraying / moving pop-up covers the app; idle and tap hide it', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    Map<String, dynamic>? activity;
    var rev = 0;
    await http.runWithClient(() async {
      final controller = FarmCamController(
          discovery: PiDiscovery(localAddresses: () async => [], timeout: const Duration(milliseconds: 10)));
      await tester.pumpWidget(FarmCamApp(controller: controller, autoConnect: false));
      await tester.pump();
      await controller.connect('192.0.2.1');
      await settle(tester);
      expect(find.text('Spraying pesticide'), findsNothing);

      activity = {'kind': 'spray', 'title': 'Spraying pesticide', 'body': 'All lettuces are being sprayed.'};
      rev++;
      await tester.pump(const Duration(seconds: 2));
      await settle(tester);
      expect(find.text('Spraying pesticide'), findsOneWidget);
      expect(find.text('All lettuces are being sprayed.'), findsOneWidget);

      await tester.tap(find.text('Spraying pesticide'));    // tap hides it
      await settle(tester);
      expect(find.text('Spraying pesticide'), findsNothing);

      activity = {'kind': 'moving', 'title': 'Moving to Lettuce 3', 'body': 'The camera is moving to the next lettuce.'};
      rev++;
      await tester.pump(const Duration(seconds: 2));
      await settle(tester);
      expect(find.text('Moving to Lettuce 3'), findsOneWidget);
      expect(tester.takeException(), isNull);

      activity = null;                                         // scanning again: gone
      rev++;
      await tester.pump(const Duration(seconds: 2));
      await settle(tester);
      expect(find.text('Moving to Lettuce 3'), findsNothing);
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 5));
    }, () => fakePi(paths, state: () => {...fakeAppState(rev: 'r$rev'), if (activity != null) 'activity': activity}));
  });

  testWidgets('power shows the Pi\'s reason when it is not allowed to shut down', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    final pi = fakePi(paths);
    await http.runWithClient(() async {
      final controller = FarmCamController(
          discovery: PiDiscovery(localAddresses: () async => [], timeout: const Duration(milliseconds: 10)));
      await tester.pumpWidget(FarmCamApp(controller: controller, autoConnect: false));
      await tester.pump();
      await controller.connect('192.0.2.1');
      await settle(tester);
      await tester.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Controls')));
      await settle(tester);
      await tester.scrollUntilVisible(find.text('Power'), 300,
          scrollable: find.descendant(of: find.byType(ControlsPage), matching: find.byType(Scrollable)).first);
      await tester.tap(find.text('Power'));
      await settle(tester);
      await tester.tap(find.text('Shut down'));
      await settle(tester);
      await tester.tap(find.widgetWithText(FilledButton, 'Shut down'));
      await settle(tester);
      expect(find.textContaining('no permission to shut down'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 5));
    }, () => MockClient((request) async {
          if (request.url.path == '/api/system/power') {
            return http.Response(jsonEncode({'detail': 'FarmCam has no permission to shut down or restart the Pi. '
                'Run install_update.sh again on the Pi.'}), 503);
          }
          final copy = http.Request(request.method, request.url)
            ..headers.addAll(request.headers)
            ..bodyBytes = request.bodyBytes;
          return pi.send(copy).then(http.Response.fromStream);
        }));
  });
}
