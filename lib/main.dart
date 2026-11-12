import 'dart:async';
import 'dart:io' show Platform;
import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'core/background_watch.dart';
import 'core/farmcam_controller.dart';
import 'core/models.dart';
import 'core/theme.dart';
import 'screens/connect_screen.dart';
import 'screens/shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  if (Platform.isAndroid) {
    // Phone notifications run in a background service; keep them running
    // after app updates if the farmer turned them on.
    FlutterForegroundTask.initCommunicationPort();
    BackgroundWatch.init();
    unawaited(BackgroundWatch.resume().catchError((_) {}));
  }
  runApp(FarmCamApp());
}

/// Root: one controller for the whole app, light/dark theme, and a gate
/// that shows the Connect screen until a Pi (or demo mode) is connected.
class FarmCamApp extends StatefulWidget {
  FarmCamApp({super.key, FarmCamController? controller, this.autoConnect = true})
      : controller = controller ?? FarmCamController();
  final FarmCamController controller;
  final bool autoConnect;
  @override
  State<FarmCamApp> createState() => _FarmCamAppState();
}

class _FarmCamAppState extends State<FarmCamApp> {
  @override
  void initState() {
    super.initState();
    widget.controller.start(autoConnect: widget.autoConnect);
  }

  @override
  void dispose() {
    widget.controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return FarmCamScope(
      controller: controller,
      child: ValueListenableBuilder<ThemeMode>(
        valueListenable: controller.themeMode,
        builder: (context, mode, _) => MaterialApp(
          title: 'FarmCam',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          themeMode: mode,
          home: const _Gate(),
        ),
      ),
    );
  }
}

/// Connect screen until the first successful connection; after that the
/// app stays in the Shell (a banner handles reconnecting).
class _Gate extends StatelessWidget {
  const _Gate();
  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    return ValueListenableBuilder<LinkInfo>(
      valueListenable: controller.link,
      builder: (context, link, _) => ValueListenableBuilder<AppSnapshot?>(
        valueListenable: controller.snapshot,
        builder: (context, snap, _) {
          final inApp = link.isOnline || (snap != null && link.state != LinkState.idle);
          return AnimatedSwitcher(
            duration: const Duration(milliseconds: 300),
            child: inApp ? const Shell(key: ValueKey('shell')) : const ConnectScreen(key: ValueKey('connect')),
          );
        },
      ),
    );
  }
}
