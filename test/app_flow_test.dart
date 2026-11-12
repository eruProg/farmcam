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
import 'package:farmcam/screens/history_page.dart';
import 'helpers.dart';

/// Several frames, so sheets finish sliding in or out (pumpAndSettle never
/// settles while the app polls).
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('discovery finds the Pi on the phone hotspot subnet', () async {
    final probed = <String>[];
    final found = await http.runWithClient(() {
      final discovery = PiDiscovery(
          localAddresses: () async => ['10.229.135.1', '127.0.0.1'],
          timeout: const Duration(milliseconds: 200));
      return discovery.scan(stopAtFirst: true);
    }, () => MockClient((request) async {
          probed.add(request.url.host);
          if (request.url.host == '10.229.135.224' && request.url.path == '/api/hello') {
            return http.Response(jsonEncode({'app': 'farmcam', 'hostname': 'aipest'}), 200);
          }
          return http.Response('not farmcam', 404);
        }));
    expect(found.map((p) => p.ip), ['10.229.135.224']);
    expect(found.first.hostname, 'aipest');
    expect(probed.every((h) => h.startsWith('10.229.135.')), isTrue);
    expect(PiDiscovery.isPrivate('172.20.10.4') && !PiDiscovery.isPrivate('8.8.8.8'), isTrue);
  });

  testWidgets('a poll with the same rev redraws nothing; a new rev does', (tester) async {
    final paths = <String>[];
    var rev = 'r1';
    await http.runWithClient(() async {
      final controller = FarmCamController(pollEvery: const Duration(seconds: 2));
      var notified = 0;
      controller.snapshot.addListener(() => notified++);
      await controller.connect('192.0.2.1');
      await tester.pump(const Duration(milliseconds: 100));
      expect(notified, 1);
      await tester.pump(const Duration(seconds: 2));
      await tester.pump(const Duration(seconds: 2));
      expect(paths.where((p) => p == '/api/app/state').length, greaterThanOrEqualTo(3));
      expect(notified, 1);                       // same rev: no redraw
      rev = 'r2';
      await tester.pump(const Duration(seconds: 2));
      expect(notified, 2);
      expect(controller.snapshot.value!.stations, hasLength(4));
      controller.dispose();
    }, () => fakePi(paths, state: () => fakeAppState(rev: rev)));
  });

  testWidgets('all four sections open, no video is requested, offline shows a banner', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    var online = true;
    await http.runWithClient(() async {
      final controller = FarmCamController(
          discovery: PiDiscovery(localAddresses: () async => [], timeout: const Duration(milliseconds: 10)));
      await tester.pumpWidget(FarmCamApp(controller: controller, autoConnect: false));
      await tester.pump();
      await controller.connect('192.0.2.1');
      await tester.pump(const Duration(milliseconds: 400));    // Connect screen fades out
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.text('Monitoring'), findsWidgets);           // Home section card
      expect(find.text('Taking Datasets'), findsNothing);          // not for the client
      expect(find.textContaining('lettuces need attention: L2, L3'), findsOneWidget);

      final nav = find.byType(NavigationBar);
      expect(find.descendant(of: nav, matching: find.text('Datasets')), findsNothing);
      for (final label in ['Monitor', 'History', 'Controls', 'Home']) {
        await tester.tap(find.descendant(of: nav, matching: find.text(label)));
        await tester.pump(const Duration(milliseconds: 400));
        expect(tester.takeException(), isNull, reason: label);
      }

      // History: detections grouped by day; a tap opens the detection sheet.
      await tester.tap(find.descendant(of: nav, matching: find.text('History')));
      await tester.pump(const Duration(milliseconds: 400));
      final row = find.textContaining('Lettuce 2 · upper part · 3 scans');
      await tester.scrollUntilVisible(row, 300,
          scrollable: find.descendant(of: find.byType(HistoryPage), matching: find.byType(Scrollable)).first);
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(row.first);
      await settle(tester);
      expect(find.text('Close-up'), findsOneWidget);
      expect(find.text('Not a pest (false alarm)'), findsOneWidget);
      await tester.tapAt(const Offset(200, 40));                     // close the sheet
      await settle(tester);

      await tester.tap(find.descendant(of: nav, matching: find.text('Controls')));
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.text('Manual Pump'), findsOneWidget);

      // Power: choose Restart, confirm, and the Pi is asked to restart.
      await tester.scrollUntilVisible(find.text('Power'), 300,
          scrollable: find.descendant(of: find.byType(ControlsPage), matching: find.byType(Scrollable)).first);
      await tester.tap(find.text('Power'));
      await settle(tester);
      expect(find.text('Shut down'), findsOneWidget);
      await tester.tap(find.text('Restart'));
      await settle(tester);
      expect(find.text('Restart FarmCam?'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Restart'));
      await settle(tester);
      expect(paths, contains('/api/system/power'));
      await tester.pump(const Duration(seconds: 4));                 // snackbar goes away

      online = false;
      for (var i = 0; i < 10; i++) {
        await tester.pump(const Duration(seconds: 2));
      }
      expect(find.text('Lost contact with the Pi. Retrying…'), findsOneWidget);
      expect(paths.where((p) => p.contains('stream') || p.contains('video')), isEmpty);
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 5));
    }, () => fakePi(paths, online: () => online));
  });
}
