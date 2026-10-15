import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'screens/setup_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
    systemNavigationBarColor: Color(0xFFFAFAF9),
    systemNavigationBarIconBrightness: Brightness.dark,
  ));
  runApp(const FarmCamApp());
}

// ─── shadcn/ui-inspired Design Tokens ────────────────────────────
// Background:  #FAFAF9  (warm stone white)
// Card:        #FFFFFF
// Border:      #E4E4E7  (zinc-200)
// Muted text:  #71717A  (zinc-500)
// Foreground:  #18181B  (zinc-900)
// Green:       #16A34A  (natural forest green — not neon)
// Amber:       #D97706
// Red:         #DC2626
// ─────────────────────────────────────────────────────────────────

class FarmCamApp extends StatelessWidget {
  const FarmCamApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FarmCam',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.light,
        scaffoldBackgroundColor: const Color(0xFFFAFAF9),
        fontFamily: 'Inter',
        colorScheme: const ColorScheme.light(
          primary: Color(0xFF18181B),
          secondary: Color(0xFF71717A),
          surface: Color(0xFFFFFFFF),
          error: Color(0xFFDC2626),
          outline: Color(0xFFE4E4E7),
          surfaceContainerHighest: Color(0xFFF4F4F5),
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFFFAFAF9),
          elevation: 0,
          scrolledUnderElevation: 0,
          foregroundColor: Color(0xFF18181B),
          iconTheme: IconThemeData(color: Color(0xFF18181B)),
        ),
        dividerTheme: const DividerThemeData(
          color: Color(0xFFE4E4E7),
          thickness: 1,
        ),
      ),
      home: const SetupScreen(),
    );
  }
}
