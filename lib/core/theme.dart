import 'package:flutter/material.dart';

/// FarmCam colours that Material's ColorScheme has no slot for.
/// Read with `FarmColors.of(context)`; both themes define every value.
@immutable
class FarmColors extends ThemeExtension<FarmColors> {
  const FarmColors({
    required this.healthy,
    required this.healthyBg,
    required this.pest,
    required this.pestBg,
    required this.fungus,
    required this.fungusBg,
    required this.info,
    required this.infoBg,
    required this.warning,
    required this.muted,
    required this.border,
    required this.card,
    required this.subtle,
  });

  final Color healthy, healthyBg, pest, pestBg, fungus, fungusBg;
  final Color info, infoBg, warning, muted, border, card, subtle;

  static FarmColors of(BuildContext context) =>
      Theme.of(context).extension<FarmColors>()!;

  static const light = FarmColors(
    healthy: Color(0xFF237A3B),
    healthyBg: Color(0xFFE3F2E6),
    pest: Color(0xFFC7362F),
    pestBg: Color(0xFFFBE7E5),
    fungus: Color(0xFFB4690E),
    fungusBg: Color(0xFFFCEFD9),
    info: Color(0xFF2563A8),
    infoBg: Color(0xFFE5EEF8),
    warning: Color(0xFFB4690E),
    muted: Color(0xFF66716A),
    border: Color(0xFFDDE3DE),
    card: Color(0xFFFFFFFF),
    subtle: Color(0xFFF1F4F1),
  );

  static const dark = FarmColors(
    healthy: Color(0xFF6FCB86),
    healthyBg: Color(0xFF1C3324),
    pest: Color(0xFFF08A82),
    pestBg: Color(0xFF3D2120),
    fungus: Color(0xFFF0B45E),
    fungusBg: Color(0xFF3B2C14),
    info: Color(0xFF8DB8EB),
    infoBg: Color(0xFF1B2A3B),
    warning: Color(0xFFF0B45E),
    muted: Color(0xFF9AA59D),
    border: Color(0xFF2C3630),
    card: Color(0xFF1A211C),
    subtle: Color(0xFF222A25),
  );

  /// Colour for a detection class (whitefly, worm, fungus).
  Color forClass(String cls) => cls == 'fungus' ? fungus : pest;

  /// Colour for a severity word (none/low/medium/high/clear).
  Color forSeverity(String? severity) => switch (severity) {
        'high' => pest,
        'medium' || 'low' => fungus,
        _ => healthy,
      };

  @override
  FarmColors copyWith() => this;

  @override
  FarmColors lerp(ThemeExtension<FarmColors>? other, double t) {
    if (other is! FarmColors) return this;
    Color l(Color a, Color b) => Color.lerp(a, b, t)!;
    return FarmColors(
      healthy: l(healthy, other.healthy),
      healthyBg: l(healthyBg, other.healthyBg),
      pest: l(pest, other.pest),
      pestBg: l(pestBg, other.pestBg),
      fungus: l(fungus, other.fungus),
      fungusBg: l(fungusBg, other.fungusBg),
      info: l(info, other.info),
      infoBg: l(infoBg, other.infoBg),
      warning: l(warning, other.warning),
      muted: l(muted, other.muted),
      border: l(border, other.border),
      card: l(card, other.card),
      subtle: l(subtle, other.subtle),
    );
  }
}

/// Spacing scale used across the app.
abstract final class Space {
  static const xs = 4.0, sm = 8.0, md = 12.0, lg = 16.0, xl = 24.0, xxl = 32.0;
}

abstract final class AppTheme {
  static const _seed = Color(0xFF2F7D43);

  static ThemeData light() => _build(Brightness.light, FarmColors.light,
      background: const Color(0xFFF7F9F7), onBackground: const Color(0xFF17201A));

  static ThemeData dark() => _build(Brightness.dark, FarmColors.dark,
      background: const Color(0xFF111613), onBackground: const Color(0xFFE6ECE7));

  static ThemeData _build(Brightness brightness, FarmColors farm,
      {required Color background, required Color onBackground}) {
    final scheme = ColorScheme.fromSeed(
      seedColor: _seed,
      brightness: brightness,
      surface: farm.card,
      onSurface: onBackground,
      error: farm.pest,
    ).copyWith(outline: farm.border, outlineVariant: farm.border);
    final text = Typography.material2021(platform: TargetPlatform.android)
        .black
        .apply(fontFamily: 'Inter');
    final textTheme = (brightness == Brightness.dark
            ? Typography.material2021().white.apply(fontFamily: 'Inter')
            : text)
        .apply(bodyColor: onBackground, displayColor: onBackground);
    final shape = RoundedRectangleBorder(borderRadius: BorderRadius.circular(14));
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      fontFamily: 'Inter',
      textTheme: textTheme.copyWith(
        headlineSmall: textTheme.headlineSmall
            ?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.4),
        titleLarge: textTheme.titleLarge
            ?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.3),
        titleMedium:
            textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
        labelSmall: textTheme.labelSmall
            ?.copyWith(letterSpacing: 0.6, fontWeight: FontWeight.w600),
      ),
      scaffoldBackgroundColor: background,
      extensions: [farm],
      appBarTheme: AppBarTheme(
        backgroundColor: background,
        foregroundColor: onBackground,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: textTheme.titleLarge
            ?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.3),
      ),
      cardTheme: CardTheme(
        color: farm.card,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: farm.border)),
      ),
      dividerTheme: DividerThemeData(color: farm.border, space: 1),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: farm.card,
        indicatorColor: scheme.primaryContainer,
        elevation: 0,
        height: 68,
        labelTextStyle: WidgetStatePropertyAll(textTheme.labelMedium
            ?.copyWith(fontWeight: FontWeight.w600)),
      ),
      filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
              minimumSize: const Size(64, 48), shape: shape)),
      outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
              minimumSize: const Size(64, 48),
              shape: shape,
              side: BorderSide(color: farm.border))),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: farm.subtle,
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: farm.border)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: farm.border)),
      ),
      snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
      chipTheme: ChipThemeData(
          side: BorderSide(color: farm.border),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10))),
      pageTransitionsTheme: const PageTransitionsTheme(builders: {
        TargetPlatform.android: FadeForwardsPageTransitionsBuilderCompat(),
        TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        TargetPlatform.windows: FadeForwardsPageTransitionsBuilderCompat(),
      }),
    );
  }
}

/// Short fade + slide page transition (Flutter 3.24 has no FadeForwards builder).
class FadeForwardsPageTransitionsBuilderCompat extends PageTransitionsBuilder {
  const FadeForwardsPageTransitionsBuilderCompat();
  @override
  Widget buildTransitions<T>(PageRoute<T> route, BuildContext context,
      Animation<double> animation, Animation<double> secondaryAnimation, Widget child) {
    final curved = CurvedAnimation(parent: animation, curve: Curves.easeOutCubic);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
          position: Tween(begin: const Offset(0.04, 0), end: Offset.zero).animate(curved),
          child: child),
    );
  }
}
