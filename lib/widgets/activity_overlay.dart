import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';

/// Full-screen pop-up while FarmCam sprays pesticide or moves to a lettuce
/// (never for detections): white text in the centre over a dark, lightly
/// blurred layer, so the app stays visible behind it. The same pop-up shows on
/// the machine's HDMI monitor. A tap hides it until the next spray or move.
class ActivityOverlay extends StatefulWidget {
  const ActivityOverlay({super.key});
  @override
  State<ActivityOverlay> createState() => _ActivityOverlayState();
}

class _ActivityOverlayState extends State<ActivityOverlay> {
  FarmCamController? _controller;
  Activity? _dismissed;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _controller ??= FarmCamScope.of(context)..snapshot.addListener(_onSnapshot);
  }

  /// Once the activity is over, the next one shows again even if it reads the same.
  void _onSnapshot() {
    if (_controller!.snapshot.value?.activity == null) _dismissed = null;
  }

  @override
  void dispose() {
    _controller?.snapshot.removeListener(_onSnapshot);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<AppSnapshot?>(
      valueListenable: _controller!.snapshot,
      builder: (context, snap, _) {
        final activity = snap?.activity;
        final visible = activity != null && activity != _dismissed;
        return AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: visible
              ? _Layer(key: ValueKey(activity), activity: activity,
                  onTap: () => setState(() => _dismissed = activity))
              : const SizedBox.shrink(),
        );
      },
    );
  }
}

class _Layer extends StatelessWidget {
  const _Layer({super.key, required this.activity, required this.onTap});
  final Activity activity;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final shadow = [Shadow(color: Colors.black.withOpacity(0.5), blurRadius: 14)];
    return Material(
      type: MaterialType.transparency,
      child: GestureDetector(
        onTap: onTap,
        behavior: HitTestBehavior.opaque,
        child: ClipRect(
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 6, sigmaY: 6),
            child: Container(
              color: Colors.black.withOpacity(0.45),
              alignment: Alignment.center,
              padding: const EdgeInsets.all(32),
              child: Semantics(
                liveRegion: true,
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Icon(activity.kind == 'spray' ? Icons.water_drop_rounded : Icons.arrow_forward_rounded,
                      color: Colors.white, size: 72, shadows: shadow),
                  const SizedBox(height: 18),
                  Text(activity.title,
                      textAlign: TextAlign.center,
                      style: text.headlineMedium?.copyWith(
                          color: Colors.white, fontWeight: FontWeight.w800, shadows: shadow)),
                  if (activity.body.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    Text(activity.body,
                        textAlign: TextAlign.center,
                        style: text.titleMedium?.copyWith(color: Colors.white.withOpacity(0.92), shadows: shadow)),
                  ],
                  const SizedBox(height: 28),
                  Text('Tap to hide',
                      style: text.labelMedium?.copyWith(color: Colors.white.withOpacity(0.65))),
                ]),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
