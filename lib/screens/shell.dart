import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../widgets/activity_overlay.dart';
import '../widgets/ui.dart';
import 'controls_page.dart';
import 'history_page.dart';
import 'home_page.dart';
import 'monitor_page.dart';

/// Main app frame: Home · Monitor · History · Controls. (Taking training
/// photos is in Settings > Developer.)
/// Pages stay alive in an IndexedStack, so switching keeps scroll position
/// and nothing reloads.
class Shell extends StatefulWidget {
  const Shell({super.key, this.initialTab = 0});
  final int initialTab;
  static ShellState? of(BuildContext context) => context.findAncestorStateOfType<ShellState>();
  @override
  State<Shell> createState() => ShellState();
}

class ShellState extends State<Shell> {
  late int _tab = widget.initialTab;

  void open(int tab) {
    if (tab == _tab) return;
    HapticFeedback.selectionClick();
    setState(() => _tab = tab);
  }

  static const destinations = [
    NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home_rounded), label: 'Home'),
    NavigationDestination(icon: Icon(Icons.radar_outlined), selectedIcon: Icon(Icons.radar_rounded), label: 'Monitor'),
    NavigationDestination(icon: Icon(Icons.insights_outlined), selectedIcon: Icon(Icons.insights_rounded), label: 'History'),
    NavigationDestination(icon: Icon(Icons.tune_outlined), selectedIcon: Icon(Icons.tune_rounded), label: 'Controls'),
  ];

  @override
  Widget build(BuildContext context) {
    const pages = [HomePage(), MonitorPage(), HistoryPage(), ControlsPage()];
    return PopScope(
      canPop: _tab == 0,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) open(0);
      },
      // The spraying / moving pop-up covers everything, nav bar included.
      child: Stack(children: [
        Scaffold(
        body: SafeArea(
          bottom: false,
          child: Column(children: [
            const ConnectionBanner(),
            Expanded(
              // Comfortable reading width on tablets; phones use the full width.
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 860),
                  child: IndexedStack(index: _tab, children: [
                    for (var i = 0; i < pages.length; i++)
                      TickerMode(enabled: i == _tab, child: pages[i]),
                  ]),
                ),
              ),
            ),
          ]),
        ),
        // Labels stay on one line even with very large phone text sizes.
        bottomNavigationBar: MediaQuery.withClampedTextScaling(
          maxScaleFactor: 1.15,
          child: NavigationBar(
            selectedIndex: _tab,
            onDestinationSelected: open,
            destinations: destinations,
          ),
        ),
        ),
        const Positioned.fill(child: ActivityOverlay()),
      ]),
    );
  }
}
