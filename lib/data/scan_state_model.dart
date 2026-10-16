/// Maps server monotonic timestamps onto a local monotonic clock.
class ScanStateModel {
  ScanStateModel({double Function()? now}) : _now = now ?? _defaultNow;
  static final Stopwatch _clock = Stopwatch()..start();
  static double _defaultNow() => _clock.elapsedMicroseconds / 1000000;
  final double Function() _now;
  double? _offset;
  double _lastServer = -1;
  double _lastResponse = -1;
  double _duration = 0;
  double _started = 0;
  Map<String, dynamic> state = {};
  bool connected = false;
  double get now => _now();
  void disconnect() {
    connected = false;
    _offset = null;
  }

  bool update(Map<String, dynamic> data, double sent, double received) {
    if (received < _lastResponse) return false;
    final server = (data['server_time'] as num).toDouble();
    final sample = server - (sent + received) / 2;
    final restarted = server < _lastServer;
    _offset =
        _offset == null || restarted ? sample : 0.2 * sample + 0.8 * _offset!;
    _lastServer = server;
    _lastResponse = received;
    _started = (data['phase_started_at'] as num).toDouble();
    _duration = (data['phase_duration_s'] as num).toDouble();
    state = data;
    connected = true;
    return true;
  }

  double get remaining => !connected || _offset == null
      ? 0
      : (_duration - (_now() + _offset! - _started)).clamp(0, _duration);
  double get progress => _duration <= 0 || !connected
      ? 0
      : (1 - remaining / _duration).clamp(0, 1);
}
