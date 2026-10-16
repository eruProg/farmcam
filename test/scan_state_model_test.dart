import 'package:flutter_test/flutter_test.dart';
import 'package:farmcam/data/scan_state_model.dart';

void main() {
  test('countdown advances between polls and resets on reboot', () {
    double now = 10;
    final model = ScanStateModel(now: () => now);
    Map<String, dynamic> sample(double server, double start) => {
          'server_time': server,
          'phase_started_at': start,
          'phase_duration_s': 30,
        };
    model.update(sample(100, 95), 10, 10);
    expect(model.remaining, 25);
    now = 15;
    expect(model.remaining, 20);
    model.update(sample(2, 0), 15, 15);
    expect(model.remaining, 28);
    expect(model.update(sample(1000, 0), 14, 14), isFalse);
    model.disconnect();
    expect(model.connected, isFalse);
    expect(model.progress, 0);
    model.update(sample(4, 0), 15, 15);
    expect(model.remaining, 26);
    now = 100;
    expect(model.remaining, 0);
  });
}
