import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:farmcam/main.dart';

void main() {
  testWidgets('app opens on the Connect screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(FarmCamApp(autoConnect: false));
    await tester.pump();
    expect(find.text('FarmCam'), findsOneWidget);
    expect(find.text('Search for FarmCam'), findsOneWidget);
    await tester.scrollUntilVisible(find.textContaining('demo mode'), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('demo mode'), findsOneWidget);
  });
}
