import 'package:farmcam/core/background_watch.dart';
import 'package:farmcam/core/farmcam_controller.dart';
import 'package:farmcam/core/notifications.dart';
import 'package:farmcam/core/theme.dart';
import 'package:farmcam/screens/settings_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

FarmEvent ev(int id, String kind, [String? title]) =>
    FarmEvent(id: id, kind: kind, title: title ?? '$kind $id', body: 'Lettuce 2, upper part');

class MemoryStore implements PollStore {
  MemoryStore({this.base = 'http://10.0.0.2:8000', this.last, this.notify = const NotifyPrefs()});
  String? base;
  int? last;
  NotifyPrefs notify;
  DateTime? checked;
  @override
  Future<String?> baseUrl() async => base;
  @override
  Future<int?> lastId() async => last;
  @override
  Future<void> setLastId(int id) async => last = id;
  @override
  Future<NotifyPrefs> prefs() async => notify;
  @override
  Future<void> markChecked(DateTime when) async => checked = when;
}

void main() {
  test('events from the Pi keep their alert titles', () {
    final e = FarmEvent.fromJson({'id': 7, 'kind': 'whiteflies', 'title': 'Whitefly detected',
        'body': 'Lettuce 2, upper part: 3 whiteflies (high).', 'station': 2, 'severity': 'high', 'time': 1.5});
    expect(e.title, 'Whitefly detected');
    expect(e.station, 2);
    expect(e.isError, isFalse);
    expect(FarmEvent.fromJson({'id': 8, 'kind': 'error', 'title': 'Error: SMS not sent'}).isError, isTrue);
  });

  test('the farmer\'s choices filter alerts; tests always show', () {
    const prefs = NotifyPrefs(worm: false, errors: false);
    final plan = planNotices([ev(1, 'worm'), ev(2, 'whiteflies'), ev(3, 'error'), ev(4, 'test')], prefs);
    expect(plan.singles.map((e) => e.id), [2, 4]);
    expect(plan.summary, isNull);
  });

  test('more than three alerts become one summary', () {
    final plan = planNotices([for (var i = 1; i <= 5; i++) ev(i, i == 5 ? 'error' : 'fungus')], const NotifyPrefs());
    expect(plan.singles, isEmpty);
    expect(plan.summary, hasLength(5));
    expect(summaryTitle(plan.summary!), '5 new FarmCam alerts (1 errors)');
  });

  test('first check starts from now instead of replaying old alerts', () async {
    final store = MemoryStore();
    final shown = <NoticePlan>[];
    final urls = <Uri>[];
    final poller = EventPoller(
        fetch: (uri) async {
          urls.add(uri);
          return {'events': [], 'last_id': 41};
        },
        store: store,
        show: (p) async => shown.add(p));
    expect(await poller.poll(), isEmpty);
    expect(urls.single.toString(), 'http://10.0.0.2:8000/api/events?after=-1');
    expect(store.last, 41);
    expect(shown, isEmpty);
  });

  test('new events are shown once and the cursor moves on', () async {
    final store = MemoryStore(last: 41);
    final shown = <NoticePlan>[];
    final poller = EventPoller(
        fetch: (uri) async => {
              'events': [
                {'id': 42, 'kind': 'worm', 'title': 'Worm detected'},
                {'id': 43, 'kind': 'spray', 'title': 'Pesticide sprayed'},
              ],
              'last_id': 43,
            },
        store: store,
        show: (p) async => shown.add(p));
    final events = await poller.poll();
    expect(events.map((e) => e.title), ['Worm detected', 'Pesticide sprayed']);
    expect(store.last, 43);
    expect(shown.single.singles.map((e) => e.id), [42, 43]);
    expect(store.checked, isNotNull);
  });

  test('a reset Pi database does not silence the phone forever', () async {
    final store = MemoryStore(last: 500);
    final poller = EventPoller(fetch: (uri) async => {'events': [], 'last_id': 3}, store: store, show: (_) async {});
    await poller.poll();
    expect(store.last, 3);
  });

  Future<void> pumpCard(WidgetTester tester, {required bool supported, Map<String, Object> prefs = const {}}) async {
    SharedPreferences.setMockInitialValues(prefs);
    await tester.pumpWidget(FarmCamScope(
      controller: FarmCamController(),
      child: MaterialApp(theme: AppTheme.light(), home: Scaffold(body: SingleChildScrollView(
          child: PhoneAlertsCard(supported: supported)))),
    ));
    await tester.pump();
  }

  testWidgets('settings card: off on non-Android phones', (tester) async {
    await pumpCard(tester, supported: false);
    expect(find.text('Android phones only'), findsOneWidget);
    expect(tester.widget<SwitchListTile>(find.byKey(const ValueKey('alerts_switch'))).onChanged, isNull);
  });

  testWidgets('settings card: when on, lists every alert type and the test button', (tester) async {
    await pumpCard(tester, supported: true, prefs: {'notify_enabled': true, 'notify_worm': false});
    for (final label in ['Whitefly detected', 'Worm detected', 'Fungus detected', 'Pesticide sprayed']) {
      expect(find.text(label), findsOneWidget);
    }
    expect(tester.widget<CheckboxListTile>(find.widgetWithText(CheckboxListTile, 'Worm detected')).value, isFalse);
    expect(find.text('Send test notification'), findsOneWidget);
    await tester.pumpWidget(const SizedBox()); // stop the status timer
  });

  test('no Pi address yet: nothing to do', () async {
    final store = MemoryStore(base: null);
    var called = false;
    final poller = EventPoller(fetch: (uri) async {
      called = true;
      return {};
    }, store: store, show: (_) async {});
    expect(await poller.poll(), isEmpty);
    expect(called, isFalse);
  });
}
