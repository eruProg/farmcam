import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A realistic /api/app/state body; [rev] controls whether the app redraws.
/// [photos] adds snapshot paths: leave it off in widget tests, where
/// Image.network always fails (HTTP 400) and is reported as an exception.
Map<String, dynamic> fakeAppState({String rev = 'r1', bool running = true, int station = 2,
    String position = 'upper', bool esp32 = true, bool photos = false}) => {
      'rev': rev,
      'build': 'test-build',
      'model_colors': 'split',
      'scan': {
        'running': running, 'run_id': 'run-1', 'phase': running ? 'DETECT_UPPER' : 'IDLE',
        'phase_label': running ? 'Detecting (Upper)' : 'Idle', 'station': station, 'position': position,
        'is_detecting': running, 'server_time': 110.0, 'phase_started_at': 100.0, 'phase_duration_s': 60.0,
      },
      'stations': [
        for (var i = 1; i <= 4; i++)
          {
            'id': i, 'name': 'Lettuce $i', 'active': i == station,
            'pest_count': i == 2 ? 2 : 0, 'worm_count': i == 2 ? 2 : 0,
            'pest_severity_level': i == 2 ? 'medium' : 'none',
            'fungus_severity_level': i == 3 ? 'medium' : 'none',
            'fungus_coverage_pct': i == 3 ? 6.5 : 0,
            'threats': [
              if (i == 2)
                {'class': 'worm', 'count': 2, 'confidence': .87, if (photos) 'thumb_path': 'abc/latest_thumb.jpg'},
              if (i == 3) {'class': 'fungus', 'count': 1, 'confidence': .61, 'coverage_pct': 6.5},
            ],
            'last_scanned': 1792600000.0,
          }
      ],
      'cooldowns': {
        'pump': {'active': false, 'remaining_s': 0, 'total_s': 300},
        'sms': {'active': true, 'remaining_s': 120, 'total_s': 300},
        'buzzer_muted': false,
      },
      'pump': {'active': false},
      'esp32': {'ip': '10.229.135.161', 'reachable': esp32, 'scanning': false},
      'health': {'camera_ok': true, 'detection_ok': true, 'ready': true, 'models': ['DET', 'SEG']},
      'capture': {'running': false, 'done': 0, 'total': 0},
      'recent': [
        {'id': 'abc', 'station': 2, 'position': 'upper', 'class': 'worm', 'confidence': .87,
         'first_seen': 1792600000.0, 'last_seen': 1792600500.0, 'scans_seen': 3,
         if (photos) 'thumb_path': 'abc/latest_thumb.jpg', if (photos) 'snapshot_path': 'abc/latest_full.jpg'},
      ],
    };

/// Mock Pi server. [paths] records every request path.
MockClient fakePi(List<String> paths, {Map<String, dynamic> Function()? state, bool Function()? online}) =>
    MockClient((request) async {
      paths.add(request.url.path);
      if (online != null && !online()) return http.Response('{"detail":"offline"}', 503);
      switch (request.url.path) {
        case '/api/hello':
          return http.Response(jsonEncode({'app': 'farmcam', 'build': 'test-build', 'hostname': 'aipest'}), 200);
        case '/api/app/state':
          return http.Response(jsonEncode((state ?? fakeAppState)()), 200);
        case '/api/scan/settings':
          return http.Response(jsonEncode({'settings': {}, 'state': fakeAppState()['scan']}), 200);
        case '/api/logs':
          return http.Response(jsonEncode({'items': fakeAppState()['recent'], 'total': 1, 'page': 1, 'pages': 1}), 200);
        case '/api/system/power':
          return http.Response(jsonEncode({'ok': true, 'action': jsonDecode(request.body)['action'], 'in_s': 3}), 200);
        case '/api/captures':
          return http.Response('{"items":[],"total":0,"page":1,"pages":1}', 200);
        case '/api/reports/summary':
          return http.Response(
              jsonEncode({
                'total': 3, 'false_positives': 1, 'completed_scans': 8, 'totals': {'worm': 3},
                'days': [for (var i = 0; i < 14; i++) {'date': '2026-10-${(9 + i).toString().padLeft(2, '0')}', 'classes': {if (i.isEven) 'worm': 1}}],
                'stations': [for (var s = 1; s <= 4; s++) {'station': s, 'classes': {if (s == 2) 'worm': 3}, 'active': s == 2 ? 1 : 0}],
                'precision': {},
              }),
              200);
        default:
          return http.Response('{}', 200);
      }
    });
