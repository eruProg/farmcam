import 'dart:async';
import 'dart:convert';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'discovery.dart';
import 'notifications.dart';

/// Phone notifications while the app is closed: an Android foreground service
/// asks the Pi every 30 s for new events (`/api/events?after=`) over the
/// hotspot and shows them as FarmCam alerts. No internet needed.

const _enabledKey = 'notify_enabled';
const _lastIdKey = 'notify_last_event_id';
const _lastCheckKey = 'notify_last_check_ms';

/// Where the poller keeps its state; SharedPreferences on the phone,
/// a map in tests.
abstract class PollStore {
  Future<String?> baseUrl();
  Future<int?> lastId();
  Future<void> setLastId(int id);
  Future<NotifyPrefs> prefs();
  Future<void> markChecked(DateTime when);
}

class PrefsPollStore implements PollStore {
  Future<SharedPreferences> _fresh() async {
    final p = await SharedPreferences.getInstance();
    await p.reload(); // the app (another isolate) may have changed the Pi address or choices
    return p;
  }

  @override
  Future<String?> baseUrl() async {
    final p = await _fresh();
    final ip = p.getString('last_rpi_ip');
    return ip == null || ip.isEmpty ? null : 'http://$ip:${p.getString('last_rpi_port') ?? '8000'}';
  }

  @override
  Future<int?> lastId() async => (await _fresh()).getInt(_lastIdKey);
  @override
  Future<void> setLastId(int id) async => (await SharedPreferences.getInstance()).setInt(_lastIdKey, id);
  @override
  Future<NotifyPrefs> prefs() async => NotifyPrefs.read(await _fresh());
  @override
  Future<void> markChecked(DateTime when) async =>
      (await SharedPreferences.getInstance()).setInt(_lastCheckKey, when.millisecondsSinceEpoch);
}

typedef FetchJson = Future<Map<String, dynamic>> Function(Uri uri);

/// One check: new events since the last seen id -> notifications.
class EventPoller {
  EventPoller({required this.fetch, required this.store, required this.show});
  final FetchJson fetch;
  final PollStore store;
  final Future<void> Function(NoticePlan plan) show;

  /// Returns the events that were new (before the user's filters).
  Future<List<FarmEvent>> poll() async {
    final base = await store.baseUrl();
    if (base == null) return const [];
    final last = await store.lastId();
    if (last == null) {
      // First check on this phone: start from now, don't replay old alerts.
      final body = await fetch(Uri.parse('$base/api/events?after=-1'));
      await store.setLastId((body['last_id'] as num?)?.toInt() ?? 0);
      await store.markChecked(DateTime.now());
      return const [];
    }
    final body = await fetch(Uri.parse('$base/api/events?after=$last&limit=50'));
    final events = [for (final e in (body['events'] as List? ?? const [])) if (e is Map) FarmEvent.fromJson(Map<String, dynamic>.from(e))];
    final newest = (body['last_id'] as num?)?.toInt() ?? last;
    await store.markChecked(DateTime.now());
    if (events.isEmpty) {
      // The Pi's database was reset (its newest id is below ours): follow it.
      if (newest < last) await store.setLastId(newest);
      return const [];
    }
    await store.setLastId(events.map((e) => e.id).reduce((a, b) => a > b ? a : b));
    await show(planNotices(events, await store.prefs()));
    return events;
  }
}

Future<Map<String, dynamic>> _httpJson(Uri uri) async {
  final r = await http.get(uri).timeout(const Duration(seconds: 6));
  if (r.statusCode != 200) throw http.ClientException('HTTP ${r.statusCode}', uri);
  return jsonDecode(r.body) as Map<String, dynamic>;
}

@pragma('vm:entry-point')
void farmWatchCallback() {
  FlutterForegroundTask.setTaskHandler(FarmWatchHandler());
}

/// Runs in the background service's own isolate.
class FarmWatchHandler extends TaskHandler {
  final _notes = FarmNotifications();
  late final EventPoller _poller = EventPoller(fetch: _httpJson, store: PrefsPollStore(), show: _notes.showPlan);
  bool _busy = false;
  int _misses = 0;
  DateTime _lastSearch = DateTime.fromMillisecondsSinceEpoch(0);

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    await _notes.init();
    await _check();
  }

  @override
  void onRepeatEvent(DateTime timestamp) => unawaited(_check());

  @override
  Future<void> onDestroy(DateTime timestamp) async {}

  @override
  void onNotificationPressed() => FlutterForegroundTask.launchApp();

  Future<void> _check() async {
    if (_busy) return;
    _busy = true;
    try {
      await _poller.poll();
      _misses = 0;
    } catch (_) {
      // Pi off or phone away from the hotspot: stay quiet, try again later.
      _misses++;
      if (_misses >= 4) await _searchAgain();
    } finally {
      _busy = false;
    }
  }

  /// The hotspot may have given the Pi a new address (at most every 5 min).
  Future<void> _searchAgain() async {
    if (DateTime.now().difference(_lastSearch).inMinutes < 5) return;
    _lastSearch = DateTime.now();
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final found = await PiDiscovery().scan(preferred: prefs.getString('last_rpi_ip'), stopAtFirst: true);
    if (found.isNotEmpty) {
      await prefs.setString('last_rpi_ip', found.first.ip);
      await prefs.setString('last_rpi_port', '${found.first.port}');
    }
  }
}

/// Turning phone notifications on and off (Settings page) and keeping the
/// service running after app updates.
class BackgroundWatch {
  static void init() {
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'farmcam_background',
        channelName: 'Background',
        channelDescription: 'Required by Android while FarmCam alerts are on. Can be hidden.',
        channelImportance: NotificationChannelImportance.MIN,
        priority: NotificationPriority.MIN,
        onlyAlertOnce: true,
      ),
      iosNotificationOptions: const IOSNotificationOptions(showNotification: false),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(30000),
        autoRunOnBoot: true,
        autoRunOnMyPackageReplaced: true,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  static Future<bool> isEnabled() async => (await SharedPreferences.getInstance()).getBool(_enabledKey) ?? false;
  static Future<bool> get isRunning => FlutterForegroundTask.isRunningService;

  static Future<DateTime?> lastCheck() async {
    final p = await SharedPreferences.getInstance();
    await p.reload();
    final ms = p.getInt(_lastCheckKey);
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  }

  /// Asks for the notification permission and battery exemption, then starts.
  /// Returns an error message, or null when it is running.
  static Future<String?> enable() async {
    if (await FlutterForegroundTask.checkNotificationPermission() != NotificationPermission.granted &&
        await FlutterForegroundTask.requestNotificationPermission() != NotificationPermission.granted) {
      return 'Notifications are blocked. Allow them for FarmCam in the phone settings.';
    }
    if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.requestIgnoreBatteryOptimization(); // optional; alerts may be late without it
    }
    await FarmNotifications().init();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_enabledKey, true);
    final result = await _start();
    return result is ServiceRequestFailure ? 'Could not start: ${result.error}' : null;
  }

  static Future<void> disable() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_enabledKey, false);
    await FlutterForegroundTask.stopService();
  }

  /// On app start: if the farmer turned alerts on, make sure they are running.
  static Future<void> resume() async {
    if (await isEnabled() && !await FlutterForegroundTask.isRunningService) await _start();
  }

  static Future<ServiceRequestResult> _start() async {
    if (await FlutterForegroundTask.isRunningService) return FlutterForegroundTask.restartService();
    return FlutterForegroundTask.startService(
      serviceId: 7331,
      notificationTitle: 'FarmCam alerts are on',
      notificationText: 'You will be alerted about pests, fungus and errors.',
      notificationIcon: const NotificationIcon(metaDataName: 'com.farmcam.farmcam.service_icon'),
      callback: farmWatchCallback,
    );
  }
}
