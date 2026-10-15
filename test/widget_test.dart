import 'package:flutter_test/flutter_test.dart';
import 'package:farmcam/main.dart';

void main() {
  testWidgets('FarmCam app loads', (WidgetTester tester) async {
    await tester.pumpWidget(const FarmCamApp());
    expect(find.text('FarmCam'), findsOneWidget);
  });
}
