import 'dart:ui' show Color;
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// One event from the Pi's `/api/events` ("Whitefly detected", "Error: …").
class FarmEvent {
  const FarmEvent({required this.id, required this.kind, required this.title, this.body = '',
      this.station, this.severity, this.time});
  final int id;
  final String kind; // whiteflies | worm | fungus | spray | error | ok | test
  final String title, body;
  final int? station;
  final String? severity;
  final double? time;

  bool get isError => kind == 'error' || kind == 'ok';

  factory FarmEvent.fromJson(Map<String, dynamic> j) => FarmEvent(
      id: (j['id'] as num?)?.toInt() ?? 0,
      kind: '${j['kind'] ?? ''}',
      title: '${j['title'] ?? ''}',
      body: '${j['body'] ?? ''}',
      station: (j['station'] as num?)?.toInt(),
      severity: j['severity'] as String?,
      time: (j['time'] as num?)?.toDouble());
}

/// Which alerts the farmer wants on this phone (Settings → Phone notifications).
class NotifyPrefs {
  const NotifyPrefs({this.whitefly = true, this.worm = true, this.fungus = true, this.sprays = true, this.errors = true});
  final bool whitefly, worm, fungus, sprays, errors;

  static const keys = {
    'whitefly': 'notify_whiteflies', 'worm': 'notify_worm', 'fungus': 'notify_fungus',
    'sprays': 'notify_spray', 'errors': 'notify_errors',
  };

  bool allows(FarmEvent e) => switch (e.kind) {
        'whiteflies' || 'whitefly' => whitefly,
        'worm' => worm,
        'fungus' => fungus,
        'spray' => sprays,
        'error' || 'ok' => errors,
        _ => true, // test and anything new
      };

  static NotifyPrefs read(SharedPreferences p) => NotifyPrefs(
      whitefly: p.getBool(keys['whitefly']!) ?? true,
      worm: p.getBool(keys['worm']!) ?? true,
      fungus: p.getBool(keys['fungus']!) ?? true,
      sprays: p.getBool(keys['sprays']!) ?? true,
      errors: p.getBool(keys['errors']!) ?? true);
}

/// What to put on the phone for a batch of new events: each one on its own,
/// or (more than [maxSingles]) one summary so the shade is not flooded.
class NoticePlan {
  const NoticePlan(this.singles, this.summary);
  final List<FarmEvent> singles;
  final List<FarmEvent>? summary;
}

NoticePlan planNotices(List<FarmEvent> events, NotifyPrefs prefs, {int maxSingles = 3}) {
  final wanted = [for (final e in events) if (prefs.allows(e)) e];
  if (wanted.length <= maxSingles) return NoticePlan(wanted, null);
  return NoticePlan(const [], wanted);
}

String summaryTitle(List<FarmEvent> events) {
  final errors = events.where((e) => e.kind == 'error').length;
  return errors > 0 ? '${events.length} new FarmCam alerts ($errors errors)' : '${events.length} new FarmCam alerts';
}

/// Android side: channels and showing the alerts (FarmCam logo + name).
class FarmNotifications {
  FarmNotifications([FlutterLocalNotificationsPlugin? plugin]) : _plugin = plugin ?? FlutterLocalNotificationsPlugin();
  final FlutterLocalNotificationsPlugin _plugin;
  bool _ready = false;

  static const alerts = AndroidNotificationChannel('farmcam_alerts', 'Pest & fungus alerts',
      description: 'Whitefly, worm and fungus detected; pesticide sprayed', importance: Importance.high);
  static const errors = AndroidNotificationChannel('farmcam_errors', 'Errors',
      description: 'Pump/SMS unit not responding, SMS not sent, scan stopped', importance: Importance.high);
  static const smallIcon = 'ic_stat_farmcam';
  static const _green = Color(0xFF2F7D43);

  Future<void> init() async {
    if (_ready) return;
    await _plugin.initialize(const InitializationSettings(android: AndroidInitializationSettings(smallIcon)));
    final android = _plugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();
    await android?.createNotificationChannel(alerts);
    await android?.createNotificationChannel(errors);
    _ready = true;
  }

  NotificationDetails _details(AndroidNotificationChannel channel, String body, {StyleInformation? style}) =>
      NotificationDetails(
        android: AndroidNotificationDetails(channel.id, channel.name,
            channelDescription: channel.description,
            importance: Importance.high,
            priority: Priority.high,
            icon: smallIcon,
            largeIcon: const DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
            color: _green,
            visibility: NotificationVisibility.public,
            styleInformation: style ?? BigTextStyleInformation(body),
            category: AndroidNotificationCategory.alarm),
      );

  Future<void> showPlan(NoticePlan plan) async {
    await init();
    for (final e in plan.singles) {
      await _plugin.show(e.id & 0x3fffffff, e.title, e.body,
          _details(e.isError ? errors : alerts, e.body));
    }
    final batch = plan.summary;
    if (batch != null && batch.isNotEmpty) {
      final lines = [for (final e in batch.take(7)) e.title];
      await _plugin.show(0x3fffffff, summaryTitle(batch), lines.join(' · '),
          _details(batch.any((e) => e.isError) ? errors : alerts, lines.join('\n'),
              style: InboxStyleInformation(lines,
                  contentTitle: summaryTitle(batch),
                  summaryText: batch.length > lines.length ? '+${batch.length - lines.length} more' : null)));
    }
  }
}
