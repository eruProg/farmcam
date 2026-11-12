import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:farmcam/widgets/scan_timing_card.dart';
import 'package:farmcam/services/api_service.dart';
import 'package:farmcam/core/theme.dart';
import 'package:farmcam/data/app_state.dart';

class ScanApi extends ApiService {
  ScanApi() : super(AppState());
  bool running = false;
  bool fail = false;
  int starts = 0;
  Map<String, dynamic>? applied;
  Map<String, dynamic> get state => {
        'phase': running ? 'INITIAL_DELAY' : 'IDLE',
        'running': running,
        'phase_label': 'Starting scan',
        'station': 1,
        'server_time': 100.0,
        'phase_started_at': 100.0,
        'phase_duration_s': running ? 5.0 : 0.0,
        'settings': applied,
      };
  @override
  Future<Map<String, dynamic>> scanRequest(String action,
      {Map<String, dynamic>? body}) async {
    if (fail) {
      throw Exception('Update the Pi server to use adjustable scan timing.');
    }
    if (action == 'settings') return {'state': state};
    if (action == 'start') {
      starts++;
      applied = body;
      running = true;
    }
    if (action == 'stop') running = false;
    return state;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('Inter')
          ..addFont(rootBundle.load('assets/fonts/Inter.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('timing editor renders in the dark theme', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final boundary = GlobalKey();
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.dark(),
      home: Scaffold(
          body: RepaintBoundary(
              key: boundary,
              child: const SingleChildScrollView(
                  padding: EdgeInsets.all(16), child: ScanTimingCard()))),
    ));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('return_s')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.runAsync(() async {
      final image = await (boundary.currentContext!.findRenderObject()
              as RenderRepaintBoundary)
          .toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final file = File('.verification/timing/timing-dark.png');
      await file.parent.create(recursive: true);
      await file.writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
    await tester.pumpWidget(const SizedBox());
  });
  Widget page({ApiService? api, double scale = 1, Key? boundary}) =>
      MaterialApp(
        theme: ThemeData(
            fontFamily: 'Inter',
            colorScheme: ColorScheme.fromSeed(seedColor: Colors.green),
            useMaterial3: true),
        home: MediaQuery(
            data: MediaQueryData(textScaler: TextScaler.linear(scale)),
            child: Scaffold(
                body: RepaintBoundary(
                    key: boundary,
                    child: SingleChildScrollView(
                        padding: const EdgeInsets.all(16),
                        child: ScanTimingCard(api: api))))),
      );

  testWidgets('draft persists offline and incomplete return prevents start',
      (tester) async {
    await tester.pumpWidget(page());
    await tester.pumpAndSettle();
    final input = find.byKey(const ValueKey('return_s'));
    await tester.ensureVisible(input);
    await tester.enterText(input, '12.4');
    await tester.pumpAndSettle();
    final prefs = await SharedPreferences.getInstance();
    expect(
        jsonDecode(prefs.getString('scan_timing_draft')!)['return_s'], '12.4');
    await tester.pumpWidget(const SizedBox());
    final api = ScanApi();
    await tester.pumpWidget(page(api: api));
    await tester.pumpAndSettle();
    expect(tester.widget<TextFormField>(input).controller!.text, '12.4');
    await tester.ensureVisible(input);
    await tester.enterText(input, '');
    final start = find.text('Apply timings & start scan');
    await tester.ensureVisible(start);
    await tester.tap(start);
    await tester.pump();
    expect(api.starts, 0);
    await tester.ensureVisible(input);
    await tester.enterText(input, '12.4');
    await tester.ensureVisible(start);
    await tester.tap(start);
    await tester.pump();
    await tester.pump();
    expect(api.starts, 1);
    expect(api.applied!['travel_1_2_s'], 8.5);
    expect(api.applied!['return_s'], 12.4);
    final stop = find.text('Stop scan');
    await tester.ensureVisible(stop);
    await tester.tap(stop);
    await tester.pump();
    expect(api.running, false);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'reconnection preserves running scan; old server shows update message',
      (tester) async {
    final api = ScanApi()..running = true;
    await tester.pumpWidget(page(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Stop scan'), findsOneWidget);
    expect(api.starts, 0);
    api.fail = true;
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.textContaining('Update the Pi server'), findsOneWidget);
    expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
        isNull);
    await tester.pumpWidget(const SizedBox());
  });

  for (final width in [360.0, 768.0]) {
    for (final scale in [1.0, 1.5]) {
      testWidgets('timing layout $width $scale', (tester) async {
        tester.view.physicalSize = Size(width, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final key = GlobalKey();
        await tester.pumpWidget(page(scale: scale, boundary: key));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.runAsync(() async {
          final image = await (key.currentContext!.findRenderObject()
                  as RenderRepaintBoundary)
              .toImage();
          final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
          final file = File('.verification/timing/timing-$width-$scale.png');
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes!.buffer.asUint8List());
          image.dispose();
        });
        await tester.ensureVisible(find.byKey(const ValueKey('return_s')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
      });
    }
  }
}
