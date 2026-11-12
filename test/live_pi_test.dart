import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:farmcam/core/discovery.dart';
import 'package:farmcam/core/farmcam_controller.dart';
import 'package:farmcam/core/models.dart';

/// End-to-end against a REAL FarmCam server (the Pi, or the PC dev mode:
/// `FARMCAM_DEV=1 py main.py`). Skipped unless FARMCAM_LIVE_IP is set:
///
///   $env:FARMCAM_LIVE_IP='127.0.0.1'; flutter test test/live_pi_test.dart
///
/// Uses real HTTP, so it must not initialise the widget test binding.
void main() {
  final ip = Platform.environment['FARMCAM_LIVE_IP'];
  final skip = ip == null ? 'Set FARMCAM_LIVE_IP to run against a live server' : null;

  test('discover, connect, poll state, captures and reports on a live server', () async {
    SharedPreferences.setMockInitialValues({}); // phone storage, in memory
    final discovery = PiDiscovery(localAddresses: () async => [], timeout: const Duration(seconds: 2));
    final hit = await discovery.probe(ip!);
    expect(hit, isNotNull, reason: 'GET /api/hello on $ip');

    final controller = FarmCamController(discovery: discovery);
    final api = controller.api;
    expect(await controller.connect(ip), isTrue);
    await controller.refresh();
    final snap = controller.snapshot.value!;
    expect(snap.stations, hasLength(4));
    expect(snap.health.cameraOk, isTrue);
    expect(snap.esp32.ip, isNotEmpty);

    // A full-resolution photo, tagged, then listed and deleted again.
    final shot = await api.captureFull(station: 3, position: 'lower', note: 'live test');
    expect(shot['ok'], isTrue);
    final list = await api.captures();
    final item = (list['items'] as List).cast<Map>().firstWhere((e) => e['name'] == shot['name']);
    expect(item['station'], 3);
    expect(item['position'], 'lower');
    expect(await api.deleteCapture(shot['name'] as String), isTrue);

    final report = await api.reportSummary(days: 7);
    expect(report['days'], hasLength(7));

    // Start a short scan, see the state change, stop it.
    final timings = {'scan_s': 20.0, 'initial_delay_s': 0.0, 'raise_s': 2.0, 'lower_s': 2.0,
        'travel_1_2_s': 3.0, 'travel_2_3_s': 3.0, 'travel_3_4_s': 3.0, 'return_s': 5.0};
    final started = await api.scanRequest('start', body: timings);
    expect(started['running'], isTrue);
    await controller.refresh();
    final running = controller.snapshot.value!.scan;
    expect(running.running, isTrue);
    expect(running.station, 1);
    expect(controller.clock.remaining, greaterThan(0));
    expect((await api.scanRequest('stop', body: {}))['running'], isFalse);
    await controller.refresh();
    expect(controller.snapshot.value!.scan.running, isFalse);
    expect(AppSnapshot.fromJson(await api.appState()).rev, isNotEmpty);
    controller.dispose();
  }, skip: skip, timeout: const Timeout(Duration(minutes: 2)));
}
