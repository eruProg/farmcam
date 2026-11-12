import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:farmcam/core/discovery.dart';
import 'package:farmcam/core/farmcam_controller.dart';
import 'package:farmcam/main.dart';
import 'helpers.dart';

/// Every section at phone and tablet width, normal and large text, light
/// and dark: no overflow or exceptions. Screenshots go to .verification/ui/.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('Inter')..addFont(rootBundle.load('assets/fonts/Inter.ttf'))).load();
    await (FontLoader('MaterialIcons')..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
  });

  for (final size in [const Size(360, 800), const Size(768, 1024)]) {
    for (final scale in [1.0, 1.5]) {
      final name = '${size.width.toInt()}-$scale';
      testWidgets('spraying pop-up at $name', (tester) async {
        SharedPreferences.setMockInitialValues({'theme_mode': 'light'});
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        tester.platformDispatcher.textScaleFactorTestValue = scale;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
        await http.runWithClient(() async {
          final controller = FarmCamController(
              discovery: PiDiscovery(localAddresses: () async => [], timeout: const Duration(milliseconds: 10)));
          final boundary = GlobalKey();
          await tester.pumpWidget(RepaintBoundary(
              key: boundary, child: FarmCamApp(controller: controller, autoConnect: false)));
          await tester.pump();
          await controller.start(autoConnect: false);
          await controller.connect('192.0.2.1');
          for (var i = 0; i < 8; i++) {
            await tester.pump(const Duration(milliseconds: 100));
          }
          expect(find.text('Spraying pesticide'), findsOneWidget);
          expect(tester.takeException(), isNull, reason: 'pop-up at $name');
          await tester.runAsync(() async {
            final image = await (boundary.currentContext!.findRenderObject() as RenderRepaintBoundary).toImage();
            final data = await image.toByteData(format: ui.ImageByteFormat.png);
            await Directory('.verification/ui').create(recursive: true);
            await File('.verification/ui/popup-$name.png').writeAsBytes(data!.buffer.asUint8List());
            image.dispose();
          });
          await tester.pumpWidget(const SizedBox());
          await tester.pump(const Duration(seconds: 5));
        }, () => fakePi([], state: () => {
              ...fakeAppState(),
              'activity': {'kind': 'spray', 'title': 'Spraying pesticide', 'body': 'All lettuces are being sprayed.'},
            }));
      });
    }
  }

  for (final size in [const Size(360, 800), const Size(768, 1024)]) {
    for (final scale in [1.0, 1.5]) {
      for (final dark in [false, true]) {
        final name = '${size.width.toInt()}-$scale-${dark ? 'dark' : 'light'}';
        testWidgets('all sections at $name', (tester) async {
          SharedPreferences.setMockInitialValues({'theme_mode': dark ? 'dark' : 'light'});
          tester.view.physicalSize = size;
          tester.view.devicePixelRatio = 1;
          tester.platformDispatcher.textScaleFactorTestValue = scale;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
          final paths = <String>[];
          await http.runWithClient(() async {
            final controller = FarmCamController(
                discovery: PiDiscovery(localAddresses: () async => [], timeout: const Duration(milliseconds: 10)));
            final boundary = GlobalKey();
            await tester.pumpWidget(RepaintBoundary(
                key: boundary, child: FarmCamApp(controller: controller, autoConnect: false)));
            await tester.pump();
            await controller.start(autoConnect: false);
            await controller.connect('192.0.2.1');
            await tester.pump(const Duration(milliseconds: 500));
            final nav = find.byType(NavigationBar);
            for (final tab in ['Home', 'Monitor', 'History', 'Controls']) {
              await tester.tap(find.descendant(of: nav, matching: find.text(tab)));
              await tester.pump(const Duration(milliseconds: 500));
              expect(tester.takeException(), isNull, reason: '$tab at $name');
              await tester.runAsync(() async {
                final image =
                    await (boundary.currentContext!.findRenderObject() as RenderRepaintBoundary).toImage();
                final data = await image.toByteData(format: ui.ImageByteFormat.png);
                await Directory('.verification/ui').create(recursive: true);
                await File('.verification/ui/${tab.toLowerCase()}-$name.png').writeAsBytes(data!.buffer.asUint8List());
                image.dispose();
              });
            }
            await tester.pumpWidget(const SizedBox());
            await tester.pump(const Duration(seconds: 5));
          }, () => fakePi(paths));
        });
      }
    }
  }
}
