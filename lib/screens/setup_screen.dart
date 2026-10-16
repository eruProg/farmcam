import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../data/app_state.dart';
import '../services/api_service.dart';
import 'dashboard_screen.dart';

// ─── Design Tokens ──────────────────────────────────────────────
const _bg = Color(0xFFFAFAF9);
const _card = Color(0xFFFFFFFF);
const _border = Color(0xFFE4E4E7);
const _fg = Color(0xFF18181B);
const _muted = Color(0xFF71717A);
const _green = Color(0xFF16A34A);
const _greenBg = Color(0xFFDCFCE7);
const _red = Color(0xFFDC2626);
const _redBg = Color(0xFFFEE2E2);
const _blue = Color(0xFF2563EB);
// ────────────────────────────────────────────────────────────────

class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key});
  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  final _ipCtrl = TextEditingController(text: '192.168.');
  final _portCtrl = TextEditingController(text: '8000');
  bool _connecting = false;
  String _status = '';
  String _error = '';
  bool _showForm = false;

  @override
  void initState() {
    super.initState();
    _loadLastIp();
  }

  Future<void> _loadLastIp() async {
    final prefs = await SharedPreferences.getInstance();
    final lastIp = prefs.getString('last_rpi_ip');
    final lastPort = prefs.getString('last_rpi_port');
    if (lastIp != null && lastIp.isNotEmpty) {
      setState(() {
        _ipCtrl.text = lastIp;
        if (lastPort != null) _portCtrl.text = lastPort;
        _showForm = true;
      });
    }
  }

  Future<void> _saveLastIp(String ip, String port) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('last_rpi_ip', ip);
    await prefs.setString('last_rpi_port', port);
  }

  @override
  void dispose() {
    _ipCtrl.dispose();
    _portCtrl.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final ip = _ipCtrl.text.trim();
    final port = int.tryParse(_portCtrl.text.trim()) ?? 8000;
    if (ip.isEmpty || ip == '192.168.') {
      setState(() => _error = 'Enter the RPi5 IP address');
      return;
    }
    setState(() {
      _connecting = true;
      _status = 'Connecting to $ip:$port…';
      _error = '';
    });
    final appState = AppState();
    appState.setConnection(ip, port);
    final api = ApiService(appState);
    final reachable = await api.ping();
    if (!reachable) {
      setState(() {
        _connecting = false;
        _error =
            'Cannot reach RPi5 at $ip:$port.\n\n• Hotspot ON (FarmCam / farmcam1)\n• RPi5 powered on and connected\n• Correct IP address';
        _status = '';
      });
      return;
    }
    setState(() => _status = 'Connected! Loading…');
    await _saveLastIp(ip, _portCtrl.text.trim());
    await Future.delayed(const Duration(milliseconds: 400));
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
          builder: (_) => DashboardScreen(rpiIp: ip, rpiPort: port)),
    );
  }

  void _demoMode() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => const DashboardScreen(demoMode: true)),
    );
  }

  void _showSetupGuide() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: _card,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: _border)),
        title: const Row(children: [
          Icon(Icons.wifi_tethering_rounded, color: _green, size: 20),
          SizedBox(width: 10),
          Text('Hotspot Setup',
              style: TextStyle(
                  color: _fg, fontSize: 17, fontWeight: FontWeight.w700)),
        ]),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Configure your phone hotspot so the RPi5 can auto-connect.',
                style: TextStyle(color: _muted, fontSize: 13, height: 1.5)),
            SizedBox(height: 16),
            _Step(n: '1', text: 'Open Phone Settings → Mobile Hotspot'),
            _Step(n: '2', text: 'Network Name:  FarmCam'),
            _Step(n: '3', text: 'Password:  farmcam1'),
            _Step(n: '4', text: 'Turn Hotspot ON'),
            _Step(n: '5', text: 'Power on RPi5 — it auto-connects!'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.pop(ctx);
              setState(() => _showForm = true);
            },
            style: TextButton.styleFrom(foregroundColor: _green),
            child: const Text('Done, connect now',
                style: TextStyle(fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 36),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // App mark
              Center(
                child: Column(
                  children: [
                    Container(
                      width: 56,
                      height: 56,
                      decoration: BoxDecoration(
                        color: _greenBg,
                        borderRadius: BorderRadius.circular(16),
                        border:
                            Border.all(color: _green.withValues(alpha: 0.3)),
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(15),
                        child: Image.asset(
                          'assets/logo_180.png',
                          fit: BoxFit.cover,
                          errorBuilder: (context, error, stackTrace) =>
                              const Icon(Icons.grass_rounded,
                                  color: _green, size: 28),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Text('FarmCam',
                        style: TextStyle(
                            fontSize: 26,
                            fontWeight: FontWeight.w800,
                            color: _fg,
                            letterSpacing: -0.8)),
                    const SizedBox(height: 4),
                    const Text('Lettuce pest & fungus monitoring',
                        style: TextStyle(fontSize: 13, color: _muted)),
                  ],
                ),
              ),

              const SizedBox(height: 44),

              if (!_showForm) ...[
                _ActionCard(
                    icon: Icons.bolt_rounded,
                    title: 'Quick Connect',
                    subtitle: 'Connect to your RPi5 by IP address.',
                    accent: _blue,
                    onTap: () => setState(() => _showForm = true)),
                const SizedBox(height: 10),
                _ActionCard(
                    icon: Icons.wifi_tethering_rounded,
                    title: 'First Time Setup',
                    subtitle: 'Set up your phone hotspot for the RPi5.',
                    accent: _green,
                    onTap: _showSetupGuide),
                const SizedBox(height: 32),
                Center(
                    child: TextButton(
                        onPressed: _demoMode,
                        style: TextButton.styleFrom(foregroundColor: _muted),
                        child: const Text('Try demo mode',
                            style: TextStyle(fontSize: 13)))),
              ] else ...[
                GestureDetector(
                  onTap: () => setState(() {
                    _showForm = false;
                    _error = '';
                    _status = '';
                  }),
                  child: const Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.arrow_back_ios_rounded, size: 13, color: _muted),
                    SizedBox(width: 4),
                    Text('Back', style: TextStyle(color: _muted, fontSize: 13)),
                  ]),
                ),
                const SizedBox(height: 28),
                const Text('Connect to RPi5',
                    style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                        color: _fg,
                        letterSpacing: -0.5)),
                const SizedBox(height: 6),
                const Text(
                    'Make sure your phone hotspot (FarmCam / farmcam1) is on and the RPi5 is powered.',
                    style: TextStyle(color: _muted, fontSize: 13, height: 1.5)),
                const SizedBox(height: 24),
                _Field(
                    controller: _ipCtrl,
                    label: 'IP Address',
                    hint: '192.168.x.x',
                    keyboardType: TextInputType.number,
                    inputFormatters: [
                      FilteringTextInputFormatter.allow(RegExp(r'[0-9.]'))
                    ]),
                const SizedBox(height: 12),
                _Field(
                    controller: _portCtrl,
                    label: 'Port',
                    hint: '8000',
                    keyboardType: TextInputType.number,
                    inputFormatters: [FilteringTextInputFormatter.digitsOnly]),
                const SizedBox(height: 20),
                if (_status.isNotEmpty) ...[
                  Row(children: [
                    const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: _green)),
                    const SizedBox(width: 10),
                    Text(_status,
                        style: const TextStyle(color: _green, fontSize: 13)),
                  ]),
                  const SizedBox(height: 12),
                ],
                if (_error.isNotEmpty) ...[
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                        color: _redBg,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: _red.withValues(alpha: 0.3))),
                    child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(Icons.info_outline_rounded,
                              size: 15, color: _red),
                          const SizedBox(width: 8),
                          Expanded(
                              child: Text(_error,
                                  style: const TextStyle(
                                      color: _red, fontSize: 12, height: 1.5))),
                        ]),
                  ),
                  const SizedBox(height: 12),
                ],
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    onPressed: _connecting ? null : _connect,
                    style: FilledButton.styleFrom(
                      backgroundColor: _fg,
                      foregroundColor: Colors.white,
                      disabledBackgroundColor: const Color(0xFFD4D4D8),
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                    child: _connecting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white))
                        : const Text('Connect',
                            style: TextStyle(
                                fontWeight: FontWeight.w600, fontSize: 15)),
                  ),
                ),
                const SizedBox(height: 16),
                Center(
                    child: TextButton(
                        onPressed: _demoMode,
                        style: TextButton.styleFrom(foregroundColor: _muted),
                        child: const Text('Try demo mode instead',
                            style: TextStyle(fontSize: 13)))),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

// ─── Widgets ─────────────────────────────────────────────────────

class _ActionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final Color accent;
  final VoidCallback onTap;
  const _ActionCard(
      {required this.icon,
      required this.title,
      required this.subtitle,
      required this.accent,
      required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
            color: _card,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: _border)),
        child: Row(children: [
          Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(10)),
              child: Icon(icon, size: 20, color: accent)),
          const SizedBox(width: 14),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(title,
                    style: const TextStyle(
                        color: _fg, fontSize: 14, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(subtitle,
                    style: const TextStyle(color: _muted, fontSize: 12)),
              ])),
          const Icon(Icons.chevron_right_rounded, color: _muted, size: 18),
        ]),
      ),
    );
  }
}

class _Field extends StatelessWidget {
  final TextEditingController controller;
  final String label;
  final String hint;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? inputFormatters;
  const _Field(
      {required this.controller,
      required this.label,
      required this.hint,
      this.keyboardType,
      this.inputFormatters});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(
                color: _fg, fontSize: 13, fontWeight: FontWeight.w500)),
        const SizedBox(height: 6),
        TextField(
          controller: controller,
          keyboardType: keyboardType,
          inputFormatters: inputFormatters,
          style: const TextStyle(color: _fg, fontSize: 14),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: const TextStyle(color: _muted),
            filled: true,
            fillColor: _card,
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
            border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: _border)),
            enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: _border)),
            focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: _fg, width: 1.5)),
          ),
        ),
      ],
    );
  }
}

class _Step extends StatelessWidget {
  final String n;
  final String text;
  const _Step({required this.n, required this.text});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: 20,
          height: 20,
          decoration: BoxDecoration(
              color: _greenBg, borderRadius: BorderRadius.circular(6)),
          alignment: Alignment.center,
          child: Text(n,
              style: const TextStyle(
                  color: _green, fontSize: 11, fontWeight: FontWeight.w700)),
        ),
        const SizedBox(width: 10),
        Expanded(
            child: Text(text,
                style: const TextStyle(color: _fg, fontSize: 13, height: 1.4))),
      ]),
    );
  }
}
