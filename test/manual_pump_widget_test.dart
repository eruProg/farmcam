import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:farmcam/core/theme.dart';
import 'package:farmcam/data/app_state.dart';
import 'package:farmcam/services/api_service.dart';
import 'package:farmcam/widgets/pump_button.dart';

/// The pump safety rules carried over unchanged from the old dashboard:
/// exactly one release, sent after /hold answers even if released early.
void main() {
  for (final releaseBeforeReply in [false, true]) {
    testWidgets(
        releaseBeforeReply
            ? 'release during pending start sends stop after acknowledgement'
            : 'holding confirms spray and release sends stop', (tester) async {
      final commands = <String>[];
      final hold = Completer<http.Response>();
      final api = ApiService(AppState()..setConnection('192.0.2.1', 8000));
      await http.runWithClient(() async {
        await tester.pumpWidget(MaterialApp(
          theme: AppTheme.light(),
          home: Scaffold(body: Center(child: PumpButton(api: api))),
        ));
        final button = find.text('Manual Pump');
        final gesture = await tester.startGesture(tester.getCenter(button));
        await tester.pump(const Duration(milliseconds: 600));
        expect(commands, ['hold']);
        expect(find.text('Connecting…'), findsOneWidget);
        expect(find.text('SPRAYING — Stop'), findsNothing);

        if (releaseBeforeReply) {
          await gesture.up();
          await tester.pump();
          expect(commands, ['hold']);
        }
        hold.complete(http.Response('{"ok":true,"active":true}', 200));
        await tester.pump();
        if (!releaseBeforeReply) {
          expect(find.text('SPRAYING — Stop'), findsOneWidget);
          await tester.pump(const Duration(milliseconds: 600));
          expect(commands, contains('heartbeat'));
          await gesture.up();
          await tester.pump();
        }
        expect(commands.last, 'release');
        expect(commands.where((c) => c == 'release'), hasLength(1));
        expect(find.text('SPRAYING — Stop'), findsNothing);
        await tester.pumpWidget(const SizedBox());
        await tester.pump(const Duration(seconds: 4));
      },
          () => MockClient((request) async {
                final path = request.url.path;
                if (request.method == 'POST' && path.startsWith('/api/pump/')) {
                  final action = path.split('/').last;
                  commands.add(action);
                  if (action == 'hold') return hold.future;
                  return http.Response('{"ok":true,"active":${action == 'heartbeat'}}', 200);
                }
                return http.Response('{}', 200);
              }));
    });
  }
}
