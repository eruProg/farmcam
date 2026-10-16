import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/screens/dashboard_screen.dart';

void main() {
  for (final releaseBeforeReply in [false, true]) {
    testWidgets(
        releaseBeforeReply
            ? 'release during pending start sends stop after acknowledgement'
            : 'holding confirms spray and release sends stop', (tester) async {
      tester.view.physicalSize = const Size(400, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final commands = <String>[];
      final hold = Completer<http.Response>();
      await http.runWithClient(() async {
        await tester.pumpWidget(const MaterialApp(
          home: DashboardScreen(rpiIp: '192.0.2.1'),
        ));
        await tester.pump();
        final button = find.text('Hold to Spray Pesticide');
        await tester.ensureVisible(button);
        await tester.pump();
        final gesture = await tester.startGesture(tester.getCenter(button));
        await tester.pump(const Duration(milliseconds: 600));
        expect(commands, ['hold']);
        expect(find.text('Connecting to pump…'), findsOneWidget);
        expect(find.text('SPRAYING — Release to Stop'), findsNothing);

        if (releaseBeforeReply) {
          await gesture.up();
          await tester.pump();
          expect(commands, ['hold']);
        }
        hold.complete(http.Response('{"ok":true,"active":true}', 200));
        await tester.pump();
        if (!releaseBeforeReply) {
          expect(find.text('SPRAYING — Release to Stop'), findsOneWidget);
          await tester.pump(const Duration(milliseconds: 600));
          expect(commands, contains('heartbeat'));
          await gesture.up();
          await tester.pump();
        }
        expect(commands.last, 'release');
        expect(commands.where((c) => c == 'release'), hasLength(1));
        expect(find.text('SPRAYING — Release to Stop'), findsNothing);
        await tester.pumpWidget(const SizedBox());
        await tester.pump(const Duration(seconds: 3));
      },
          () => MockClient((request) async {
                final path = request.url.path;
                if (request.method == 'POST' && path.startsWith('/api/pump/')) {
                  final action = path.split('/').last;
                  commands.add(action);
                  if (action == 'hold') return hold.future;
                  return http.Response(
                      '{"ok":true,"active":${action == 'heartbeat'}}', 200);
                }
                if (path == '/api/lettuce') return http.Response('[]', 200);
                return http.Response('{}', 200);
              }));
    });
  }
}
