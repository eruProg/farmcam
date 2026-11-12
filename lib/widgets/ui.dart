import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/farmcam_controller.dart';
import '../core/theme.dart';

/// Card with an optional header row. The one container style used app-wide.
class SectionCard extends StatelessWidget {
  const SectionCard({super.key, this.title, this.icon, this.trailing, required this.child,
      this.padding = const EdgeInsets.all(Space.lg), this.onTap, this.color});
  final String? title;
  final IconData? icon;
  final Widget? trailing, child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final text = Theme.of(context).textTheme;
    return Card(
      color: color,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: padding,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
            if (title != null) ...[
              Row(children: [
                if (icon != null) ...[Icon(icon, size: 18, color: farm.muted), const SizedBox(width: Space.sm)],
                Expanded(child: Text(title!, style: text.titleMedium)),
                if (trailing != null) trailing!,
              ]),
              const SizedBox(height: Space.md),
            ],
            if (child != null) child!,
          ]),
        ),
      ),
    );
  }
}

/// Small rounded label carrying a state colour (Ready, Cooldown, High…).
class StatusPill extends StatelessWidget {
  const StatusPill(this.label, {super.key, required this.color, this.background, this.icon});
  final String label;
  final Color color;
  final Color? background;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
            color: background ?? color.withOpacity(0.12), borderRadius: BorderRadius.circular(999)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 14, color: color), const SizedBox(width: 5)],
          Flexible(
            child: Text(label,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
          ),
        ]),
      );
}

/// Shown at the top of every page while the Pi is unreachable.
class ConnectionBanner extends StatelessWidget {
  const ConnectionBanner({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = FarmCamScope.of(context);
    final farm = FarmColors.of(context);
    return ValueListenableBuilder<LinkInfo>(
      valueListenable: controller.link,
      builder: (context, link, _) {
        final show = link.state == LinkState.reconnecting || link.state == LinkState.notFound ||
            link.state == LinkState.searching;
        return AnimatedSize(
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
          child: !show
              ? const SizedBox(width: double.infinity)
              : Material(
                  color: farm.fungusBg,
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(Space.lg, Space.sm, Space.sm, Space.sm),
                    child: Row(children: [
                      SizedBox(
                          width: 16,
                          height: 16,
                          child: link.state == LinkState.notFound
                              ? Icon(Icons.wifi_off_rounded, size: 16, color: farm.fungus)
                              : CircularProgressIndicator(strokeWidth: 2, color: farm.fungus)),
                      const SizedBox(width: Space.md),
                      Expanded(
                          child: Text(link.message ?? link.label,
                              style: TextStyle(color: farm.fungus, fontWeight: FontWeight.w600))),
                      TextButton(
                          onPressed: () => controller.findAndConnect(preferred: link.ip),
                          child: const Text('Retry')),
                    ]),
                  ),
                ),
        );
      },
    );
  }
}

/// Friendly empty or error state with one clear action.
class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.message,
      this.actionLabel, this.onAction});
  final IconData icon;
  final String title;
  final String? message, actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: Space.xl, horizontal: Space.lg),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: farm.subtle, shape: BoxShape.circle),
          child: Icon(icon, size: 28, color: farm.muted),
        ),
        const SizedBox(height: Space.md),
        Text(title, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
        if (message != null) ...[
          const SizedBox(height: Space.xs),
          Text(message!, textAlign: TextAlign.center, style: TextStyle(color: farm.muted)),
        ],
        if (actionLabel != null && onAction != null) ...[
          const SizedBox(height: Space.md),
          OutlinedButton(onPressed: onAction, child: Text(actionLabel!)),
        ],
      ]),
    );
  }
}

/// Grey placeholder that gently pulses while data loads.
class SkeletonBox extends StatefulWidget {
  const SkeletonBox({super.key, this.height = 16, this.width = double.infinity, this.radius = 8});
  final double height, width, radius;
  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox> with SingleTickerProviderStateMixin {
  late final _anim = AnimationController(vsync: this, duration: const Duration(milliseconds: 900))
    ..repeat(reverse: true);
  @override
  void dispose() {
    _anim.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FadeTransition(
        opacity: Tween(begin: 0.45, end: 1.0).animate(_anim),
        child: Container(
          height: widget.height,
          width: widget.width,
          decoration: BoxDecoration(
              color: FarmColors.of(context).subtle, borderRadius: BorderRadius.circular(widget.radius)),
        ),
      );
}

/// Network photo from the Pi: fades in, shows a placeholder while loading
/// or when missing, and opens full screen with pinch-zoom when tapped.
class SnapshotImage extends StatelessWidget {
  const SnapshotImage(this.url, {super.key, this.fullUrl, this.height, this.fit = BoxFit.cover,
      this.radius = 12, this.zoomable = true});
  final String? url, fullUrl;
  final double? height;
  final BoxFit fit;
  final double radius;
  final bool zoomable;

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final placeholder = Container(
      height: height,
      color: farm.subtle,
      alignment: Alignment.center,
      child: Icon(Icons.image_outlined, color: farm.muted),
    );
    final image = url == null
        ? placeholder
        : Image.network(
            url!,
            height: height,
            width: double.infinity,
            fit: fit,
            gaplessPlayback: true,
            frameBuilder: (context, child, frame, sync) => sync
                ? child
                : AnimatedOpacity(
                    opacity: frame == null ? 0 : 1,
                    duration: const Duration(milliseconds: 250),
                    child: frame == null ? placeholder : child),
            errorBuilder: (_, __, ___) => placeholder,
          );
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: !zoomable || url == null
          ? image
          : GestureDetector(
              onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  fullscreenDialog: true, builder: (_) => _ZoomView(fullUrl ?? url!))),
              child: image),
    );
  }
}

class _ZoomView extends StatelessWidget {
  const _ZoomView(this.url);
  final String url;
  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(backgroundColor: Colors.black, foregroundColor: Colors.white),
        body: Center(
          child: InteractiveViewer(
            maxScale: 6,
            child: Image.network(url,
                errorBuilder: (_, __, ___) =>
                    const Text('Photo unavailable', style: TextStyle(color: Colors.white))),
          ),
        ),
      );
}

/// Bottom-sheet confirmation for actions that move hardware or delete data.
Future<bool> confirmAction(BuildContext context,
    {required String title, required String message, required String confirmLabel,
    bool destructive = false}) async {
  HapticFeedback.selectionClick();
  final farm = FarmColors.of(context);
  final result = await showModalBottomSheet<bool>(
    context: context,
    showDragHandle: true,
    builder: (context) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Space.xl, 0, Space.xl, Space.xl),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: Space.sm),
          Text(message, style: TextStyle(color: farm.muted, height: 1.4)),
          const SizedBox(height: Space.xl),
          FilledButton(
            style: destructive ? FilledButton.styleFrom(backgroundColor: farm.pest) : null,
            onPressed: () => Navigator.pop(context, true),
            child: Text(confirmLabel),
          ),
          const SizedBox(height: Space.sm),
          OutlinedButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        ]),
      ),
    ),
  );
  return result == true;
}

void showMessage(BuildContext context, String message, {bool error = false}) {
  final farm = FarmColors.of(context);
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(message),
      backgroundColor: error ? farm.pest : null,
      duration: const Duration(seconds: 3),
    ));
}

String timeAgo(DateTime? t) {
  if (t == null) return 'never';
  final d = DateTime.now().difference(t);
  if (d.inSeconds < 45) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} min ago';
  if (d.inHours < 24) return '${d.inHours} h ago';
  return '${d.inDays} d ago';
}

String mmss(double seconds) {
  final s = seconds.ceil().clamp(0, 359999);
  return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
}

/// Cooldowns from minutes to a week: "4 days 3 h", "5 h 12 min", "4:59".
String durationText(double seconds) {
  final s = seconds.ceil().clamp(0, 1 << 31);
  final days = s ~/ 86400, hours = (s % 86400) ~/ 3600, minutes = (s % 3600) ~/ 60;
  if (days > 0) return '$days day${days == 1 ? '' : 's'} $hours h';
  if (hours > 0) return '$hours h $minutes min';
  return mmss(seconds);
}

/// "Oct 25, 14:02" in the phone's local time.
String shortDateTime(DateTime t) {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${months[t.month - 1]} ${t.day}, ${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}
