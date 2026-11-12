import 'package:farmcam/core/models.dart';
import 'package:farmcam/core/theme.dart';
import 'package:farmcam/data/app_state.dart';
import 'package:farmcam/services/api_service.dart';
import 'package:farmcam/widgets/live_card.dart';
import 'package:farmcam/widgets/pump_button.dart';
import 'package:farmcam/widgets/ui.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Manual spraying during the 7-day automatic-spray cooldown, and the
/// "Detecting now" card.
void main() {
  Cooldown week({double left = 4 * 86400 + 3 * 3600}) =>
      Cooldown(active: true, remaining: left, total: 7 * 86400, lastAt: DateTime(2026, 10, 25, 14, 2));

  Future<List<Uri>> tapPump(WidgetTester tester, {Cooldown? cooldown, String answer = 'Spray anyway',
      List<int> statuses = const [200]}) async {
    final calls = <Uri>[];
    final replies = [...statuses];
    final api = ApiService(AppState()..setConnection('192.0.2.1', 8000));
    await http.runWithClient(() async {
      await tester.pumpWidget(MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(body: Center(child: PumpButton(api: api, cooldown: cooldown))),
      ));
      await tester.tap(find.text('Manual Pump'));
      await tester.pumpAndSettle();
      if (find.text('Spray during the cooldown?').evaluate().isNotEmpty) {
        expect(find.textContaining('paused for another 4 days 3 h'), findsOneWidget);
        if (cooldown != null) {
          expect(find.textContaining('(last spray Oct 25, 14:02)'), findsOneWidget);
          expect(find.textContaining('restarts the 7-day cooldown.'), findsOneWidget);
        } else {
          expect(find.textContaining('restarts the cooldown.'), findsOneWidget);   // no 0:00 text
        }
        await tester.tap(find.text(answer));
        await tester.pumpAndSettle();
      }
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpWidget(const SizedBox());
    }, () => MockClient((request) async {
          calls.add(request.url);
          final status = replies.isEmpty ? 200 : replies.removeAt(0);
          return status == 409
              ? http.Response('{"ok":false,"error":"cooldown","remaining_s":${4 * 86400 + 3 * 3600},'
                  '"message":"Automatic spraying is paused for another 4 days 3 h."}', 409)
              : http.Response('{"ok":true,"active":false}', 200);
        }));
    return calls;
  }

  testWidgets('no cooldown: a tap sprays straight away', (tester) async {
    final calls = await tapPump(tester);
    expect(calls.map((u) => u.toString()), ['http://192.0.2.1:8000/api/pump/trigger']);
  });

  testWidgets('during the cooldown: "Spray anyway" sprays with override', (tester) async {
    final calls = await tapPump(tester, cooldown: week());
    expect(calls.single.queryParameters['override'], '1');
  });

  testWidgets('during the cooldown: Cancel sprays nothing', (tester) async {
    final calls = await tapPump(tester, cooldown: week(), answer: 'Cancel');
    expect(calls, isEmpty);
  });

  testWidgets('the Pi still in cooldown (app info out of date): ask, then retry with override', (tester) async {
    final calls = await tapPump(tester, statuses: [409, 200]);
    expect(calls.map((u) => u.query), ['', 'override=1']);
  });

  test('cooldown durations read well', () {
    expect(durationText(7 * 86400), '7 days 0 h');
    expect(durationText(86400 + 3600), '1 day 1 h');
    expect(durationText(5 * 3600 + 12 * 60), '5 h 12 min');
    expect(durationText(299), '4:59');
  });

  test('live info from the Pi', () {
    final live = LiveInfo.fromJson({'station': 2, 'position': 'upper', 'counts': {'whiteflies': 2, 'worm': 1},
        'fungus_pct': 0, 'spray_reason': null});
    expect(live.summary, '2 whiteflies, 1 worm');
    expect(LiveInfo.fromJson({'station': 1, 'position': 'lower', 'counts': {}}).isEmpty, isTrue);
    final snap = AppSnapshot.fromJson({'live': {'station': 3, 'position': 'lower', 'counts': {'worm': 3},
        'spray_reason': '3 worms on Lettuce 3'}});
    expect(snap.live!.sprayReason, '3 worms on Lettuce 3');
    expect(AppSnapshot.fromJson({}).live, isNull);
  });

  testWidgets('Detecting now card', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      home: const Scaffold(body: LiveCard(live: LiveInfo(station: 2, position: 'upper',
          counts: {'whiteflies': 3}, sprayReason: '3 whiteflies on Lettuce 2'))),
    ));
    expect(find.text('Detecting now'), findsOneWidget);
    expect(find.text('Lettuce 2, upper part'), findsOneWidget);
    expect(find.text('3 whiteflies'), findsOneWidget);
    expect(find.text('Spray rule met: 3 whiteflies on Lettuce 2'), findsOneWidget);
  });
}
