import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../data/app_state.dart';
import '../services/api_service.dart';
import '../widgets/lettuce_card.dart';
import '../widgets/mjpeg_viewer.dart';
import '../widgets/scan_status_card.dart';
import '../widgets/cooldown_chip.dart';
import 'about_screen.dart';
import 'lettuce_detail_screen.dart';
import 'logs_screen.dart';

// ─── Design tokens ───────────────────────────────────────────────
const _bg      = Color(0xFFFAFAF9);
const _card    = Color(0xFFFFFFFF);
const _border  = Color(0xFFE4E4E7);
const _fg      = Color(0xFF18181B);
const _muted   = Color(0xFF71717A);
const _green   = Color(0xFF16A34A);
const _amber   = Color(0xFFD97706);
const _amberBg = Color(0xFFFEF3C7);
const _red     = Color(0xFFDC2626);
const _redBg   = Color(0xFFFEE2E2);
const _zinc100 = Color(0xFFF4F4F5);
const _blue    = Color(0xFF2563EB);
const _blueBg  = Color(0xFFEFF6FF);
// ─────────────────────────────────────────────────────────────────

class DashboardScreen extends StatefulWidget {
  final String rpiIp;
  final int rpiPort;
  final bool demoMode;

  const DashboardScreen({
    super.key,
    this.rpiIp = '',
    this.rpiPort = 8000,
    this.demoMode = false,
  });

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> with WidgetsBindingObserver {
  late final AppState _appState;
  late final ApiService _api;

  bool _showStream  = true;
  bool _pumpHeld    = false;
  bool _pumpStarting = false;
  bool _pumpStopping = false;
  bool _pumpConfirmed = false;
  int _pumpSession = 0;
  Timer? _heartbeat;

  // Cooldown / buzzer state
  double _pumpCooldownRemaining = 0;
  double _pumpCooldownTotal     = 300;
  double _smsCooldownRemaining  = 0;
  double _smsCooldownTotal      = 300;
  bool   _buzzerMuted           = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _appState = AppState();
    _api = ApiService(_appState);
    if (widget.demoMode) {
      _appState.enableDemoMode();
    } else {
      _appState.setConnection(widget.rpiIp, widget.rpiPort);
      _appState.setConnected(true);
      _startPolling();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _heartbeat?.cancel();
    _pumpHeld = false;
    if (_pumpConfirmed && !_pumpStarting && !_pumpStopping) {
      _api.pumpRelease();
    }
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) _onPumpPressEnd();
  }

  void _startPolling() {
    Future.doWhile(() async {
      if (!mounted) return false;
      try {
        final stations = await _api.fetchStations();
        final sysInfo  = await _api.fetchSystemInfo();
        final pump     = await _api.fetchPumpStatus();
        // One batched update = one notifyListeners() = one rebuild of the
        // dashboard, instead of the 3-4 rebuilds this used to trigger per
        // poll tick (see AppState.applyPollResult for why).
        _appState.applyPollResult(newStations: stations, info: sysInfo, pumpData: pump);
      } catch (_) {}
      // Poll cooldown status every 3 s (lighter weight)
      try {
        final cd = await _api.getCooldownStatus();
        if (cd != null && mounted) {
          setState(() {
            final pump = cd['pump'] as Map? ?? {};
            final sms  = cd['sms']  as Map? ?? {};
            _pumpCooldownRemaining = (pump['remaining_s'] as num? ?? 0).toDouble();
            _pumpCooldownTotal     = (pump['total_s']     as num? ?? 300).toDouble();
            _smsCooldownRemaining  = (sms['remaining_s']  as num? ?? 0).toDouble();
            _smsCooldownTotal      = (sms['total_s']      as num? ?? 300).toDouble();
          });
        }
      } catch (_) {}
      await Future.delayed(const Duration(seconds: 2));
      return mounted;
    });
  }

  // ─── Pump hold-to-spray ────────────────────────────────────────

  void _showPumpError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _onPumpPressStart() async {
    if (_pumpHeld || _pumpStarting || _pumpStopping || widget.demoMode) return;
    final session = ++_pumpSession;
    setState(() { _pumpHeld = true; _pumpStarting = true; });
    final result = await _api.pumpHold();
    _pumpStarting = false;
    // Release may happen while the start request is still in flight.
    // Send stop AFTER that request completes, including timeout/failure.
    if (!mounted || !_pumpHeld || !result.ok) {
      if (mounted) setState(() => _pumpHeld = false);
      if (!result.ok) _showPumpError(result.error!);
      await _stopPump();
      return;
    }
    setState(() => _pumpConfirmed = true);
    _heartbeat?.cancel();
    var heartbeatInFlight = false;
    _heartbeat = Timer.periodic(const Duration(milliseconds: 500), (_) async {
      if (!_pumpHeld || heartbeatInFlight) return;
      heartbeatInFlight = true;
      final result = await _api.pumpHeartbeat();
      heartbeatInFlight = false;
      if (!result.ok && _pumpHeld && mounted && session == _pumpSession) {
        _showPumpError(result.error!);
        _onPumpPressEnd();
      }
    });
  }

  void _onPumpPressEnd() {
    if (!_pumpHeld) return;
    _heartbeat?.cancel();
    setState(() => _pumpHeld = false);
    if (!_pumpStarting) _stopPump();
  }

  Future<void> _stopPump() async {
    if (_pumpStopping) return;
    _pumpStopping = true;
    _heartbeat?.cancel();
    if (mounted) {
      setState(() {
        _pumpConfirmed = false;
        _appState.pumpActive = false;
      });
    }
    final result = await _api.pumpRelease();
    _pumpStopping = false;
    if (mounted) setState(() {});
    if (!result.ok) _showPumpError(result.error!);
  }

  // ─── Image picker detection ────────────────────────────────────

  Future<void> _pickAndDetect() async {
    final picker = ImagePicker();
    final file   = await picker.pickImage(source: ImageSource.gallery);
    if (file == null) return;

    if (mounted) {
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (_) => const AlertDialog(
          backgroundColor: _card,
          content: Row(mainAxisSize: MainAxisSize.min, children: [
            SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: _green)),
            SizedBox(width: 14),
            Text('Analyzing image…', style: TextStyle(color: _fg, fontSize: 14)),
          ]),
        ),
      );
    }

    final result = await _api.detectImage(File(file.path));
    if (mounted) Navigator.of(context).pop();
    if (result != null && mounted) _showDetectionResult(result);
  }

  void _showDetectionResult(Map<String, dynamic> result) {
    final b64    = result['annotated_image_base64'] as String? ?? '';
    final count  = result['detection_count'] as int? ?? 0;
    final status = result['status'] as String? ?? 'clear';

    Color statusColor = _green;
    if (status == 'pest')   statusColor = _red;
    if (status == 'fungus') statusColor = _amber;

    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: _card,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: _border)),
        title: Row(children: [
          Icon(status == 'clear' ? Icons.check_circle_outline_rounded : Icons.warning_amber_rounded, color: statusColor, size: 20),
          const SizedBox(width: 8),
          Text(status == 'clear' ? 'All Clear' : '$count Detection(s)', style: TextStyle(color: statusColor, fontSize: 17, fontWeight: FontWeight.w700)),
        ]),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (b64.isNotEmpty)
            ClipRRect(borderRadius: BorderRadius.circular(10), child: Image.memory(base64Decode(b64), fit: BoxFit.contain)),
          if (count == 0)
            const Padding(padding: EdgeInsets.only(top: 14), child: Text('No pests or fungus detected.', style: TextStyle(color: _muted, fontSize: 13))),
        ]),
        actions: [TextButton(onPressed: () => Navigator.pop(context), style: TextButton.styleFrom(foregroundColor: _muted), child: const Text('Close'))],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _appState,
      builder: (context, child) {
        final stations    = _appState.stations;
        final alertCount  = _appState.alertCount;
        final streamUrl   = 'http://${widget.rpiIp}:${widget.rpiPort}/api/stream';

        // Summary stats
        int totalPests = 0;
        double maxFungusCov = 0;
        for (final s in stations) {
          totalPests += s.pestCount;
          if (s.fungusCoveragePct > maxFungusCov) maxFungusCov = s.fungusCoveragePct;
        }

        return Scaffold(
          backgroundColor: _bg,
          body: SafeArea(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // ── Top bar
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 16, 20, 0),
                  child: Row(
                    children: [
                      const Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('FarmCam', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: _fg, letterSpacing: -0.5)),
                          Text('Lettuce Monitoring', style: TextStyle(fontSize: 12, color: _muted)),
                        ],
                      ),
                      const Spacer(),
                      // Demo badge
                      if (widget.demoMode)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(color: _amberBg, borderRadius: BorderRadius.circular(6), border: Border.all(color: _amber.withOpacity(0.4))),
                          child: const Text('DEMO', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: _amber, letterSpacing: 0.8)),
                        ),
                      const SizedBox(width: 8),
                      // Buzzer mute toggle
                      if (!widget.demoMode)
                        GestureDetector(
                          onTap: () async {
                            final muted = !_buzzerMuted;
                            final ok = await _api.setBuzzerMute(muted);
                            if (ok && mounted) setState(() => _buzzerMuted = muted);
                          },
                          child: Container(
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(
                              color: _buzzerMuted ? _amberBg : _card,
                              borderRadius: BorderRadius.circular(9),
                              border: Border.all(color: _buzzerMuted ? _amber.withOpacity(0.5) : _border),
                            ),
                            child: Icon(
                              _buzzerMuted ? Icons.volume_off_rounded : Icons.volume_up_rounded,
                              size: 18,
                              color: _buzzerMuted ? _amber : _muted,
                            ),
                          ),
                        ),
                      const SizedBox(width: 6),
                      // Detect photo button
                      if (!widget.demoMode)
                        GestureDetector(
                          onTap: _pickAndDetect,
                          child: Container(
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(9), border: Border.all(color: _border)),
                            child: const Icon(Icons.add_photo_alternate_outlined, size: 18, color: _fg),
                          ),
                        ),
                      const SizedBox(width: 6),
                      // Logs button
                      GestureDetector(
                        onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => LogsScreen(api: _api),
                        )),
                        child: Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(9), border: Border.all(color: _border)),
                          child: const Icon(Icons.list_alt_rounded, size: 18, color: _fg),
                        ),
                      ),
                      const SizedBox(width: 6),
                      // About button
                      GestureDetector(
                        onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => AboutScreen(api: _api),
                        )),
                        child: Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(9), border: Border.all(color: _border)),
                          child: const Icon(Icons.info_outline_rounded, size: 18, color: _muted),
                        ),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: 14),

                // ── Alert banner
                if (alertCount > 0)
                  Container(
                    key: const ValueKey('alert_banner'),
                    margin: const EdgeInsets.fromLTRB(20, 0, 20, 10),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    decoration: BoxDecoration(color: _redBg, borderRadius: BorderRadius.circular(10), border: Border.all(color: _red.withOpacity(0.3))),
                    child: Row(children: [
                      const Icon(Icons.warning_rounded, size: 14, color: _red),
                      const SizedBox(width: 8),
                      Expanded(child: Text('$alertCount station${alertCount > 1 ? "s" : ""} need attention', style: const TextStyle(color: _red, fontSize: 13, fontWeight: FontWeight.w600))),
                    ]),
                  ),

                // ── Pump banner (active spray indicator)
                if (!widget.demoMode && (_appState.pumpActive || _pumpConfirmed))
                  Container(
                    key: const ValueKey('pump_banner'),
                    margin: const EdgeInsets.fromLTRB(20, 0, 20, 10),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    decoration: BoxDecoration(color: _blueBg, borderRadius: BorderRadius.circular(10), border: Border.all(color: _blue.withOpacity(0.3))),
                    child: Row(children: [
                      const Icon(Icons.water_drop, size: 14, color: _blue),
                      const SizedBox(width: 8),
                      Flexible(child: Text(_pumpConfirmed ? 'Manual Spray — Hold to continue' : 'Pesticide Pump SPRAYING',
                          style: const TextStyle(color: _blue, fontSize: 13, fontWeight: FontWeight.w600))),
                    ]),
                  ),

                // ── Live camera stream
                Container(
                  key: const ValueKey('live_stream_container'),
                  margin: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                  clipBehavior: Clip.antiAlias,
                  decoration: BoxDecoration(
                    color: _card,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: _border),
                  ),
                  child: Column(
                    children: [
                      GestureDetector(
                        onTap: () => setState(() => _showStream = !_showStream),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          color: _card,
                          child: Row(children: [
                            Container(width: 8, height: 8, decoration: const BoxDecoration(color: _green, shape: BoxShape.circle)),
                            const SizedBox(width: 8),
                            const Text('Live Camera Feed', style: TextStyle(color: _fg, fontSize: 13, fontWeight: FontWeight.w600)),
                            const Spacer(),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                              decoration: BoxDecoration(color: _zinc100, borderRadius: BorderRadius.circular(6)),
                              child: Text(widget.demoMode ? 'DEMO' : 'LIVE', style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: _muted)),
                            ),
                            const SizedBox(width: 6),
                            Icon(_showStream ? Icons.expand_less_rounded : Icons.expand_more_rounded, size: 18, color: _muted),
                          ]),
                        ),
                      ),
                      if (_showStream)
                        AspectRatio(
                          aspectRatio: 4 / 3,
                          child: widget.demoMode
                              ? Container(
                                  color: const Color(0xFF18181B),
                                  child: const Center(
                                    child: Column(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Icon(Icons.videocam_outlined, size: 40, color: Color(0xFF71717A)),
                                        SizedBox(height: 8),
                                        Text('Camera Feed (Demo Mode)', style: TextStyle(color: Color(0xFFA1A1AA), fontSize: 13, fontWeight: FontWeight.w500)),
                                      ],
                                    ),
                                  ),
                                )
                              : MjpegViewer(key: const ValueKey('mjpeg_viewer'), streamUrl: streamUrl),
                        ),
                    ],
                  ),
                ),

                // ── Manual Pump Button (hold-to-spray)
                if (!widget.demoMode)
                  Padding(
                    key: const ValueKey('pump_button'),
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                    child: GestureDetector(
                      onLongPressStart: (_) => _onPumpPressStart(),
                      onLongPressEnd: (_) => _onPumpPressEnd(),
                      onLongPressCancel: _onPumpPressEnd,
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 150),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        decoration: BoxDecoration(
                          color: _pumpHeld ? _blue : _card,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: _pumpHeld ? _blue : _border,
                            width: _pumpHeld ? 2 : 1,
                          ),
                          boxShadow: _pumpHeld
                              ? [BoxShadow(color: _blue.withOpacity(0.25), blurRadius: 12, spreadRadius: 1)]
                              : null,
                        ),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(
                              _pumpHeld ? Icons.water_drop : Icons.water_drop_outlined,
                              size: 18,
                              color: _pumpHeld ? Colors.white : _blue,
                            ),
                            const SizedBox(width: 8),
                            Flexible(child: Text(
                              _pumpStarting ? 'Connecting to pump…' :
                              _pumpStopping ? 'Stopping pump…' :
                              _pumpConfirmed ? 'SPRAYING — Release to Stop' : 'Hold to Spray Pesticide',
                              style: TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w700,
                                color: _pumpHeld ? Colors.white : _blue,
                              ),
                            )),
                          ],
                        ),
                      ),
                    ),
                  ),

                // ── Scan status card
                ScanStatusCard(
                  key: const ValueKey('scan_status_card'),
                  api: _api,
                  demoMode: widget.demoMode,
                ),

                // ── Cooldown chips row
                if (!widget.demoMode)
                  Padding(
                    key: const ValueKey('cooldown_row'),
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
                    child: Row(
                      children: [
                        CooldownChip(
                          label: 'Pump',
                          remainingS: _pumpCooldownRemaining,
                          totalS: _pumpCooldownTotal,
                        ),
                        const SizedBox(width: 8),
                        CooldownChip(
                          label: 'SMS',
                          remainingS: _smsCooldownRemaining,
                          totalS: _smsCooldownTotal,
                        ),
                      ],
                    ),
                  ),

                // ── Section label
                const Padding(
                  padding: EdgeInsets.fromLTRB(20, 0, 20, 10),
                  child: Text('LETTUCE STATIONS', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: _muted, letterSpacing: 0.8)),
                ),

                // ── Station grid
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: GridView.builder(
                      physics: const BouncingScrollPhysics(),
                      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 2,
                        mainAxisSpacing: 10,
                        crossAxisSpacing: 10,
                        childAspectRatio: 0.88,
                      ),
                      itemCount: stations.length,
                      itemBuilder: (_, i) {
                        final s        = stations[i];
                        final isActive = _appState.systemInfo.activeStationId == s.id;
                        return LettuceCard(
                          key: ValueKey('lettuce_card_${s.id}'),
                          station: s,
                          isActive: isActive,
                          onTap: () => Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => LettuceDetailScreen(station: s, demoMode: widget.demoMode, api: widget.demoMode ? null : _api),
                          )),
                        );
                      },
                    ),
                  ),
                ),
                const SizedBox(height: 12),
              ],
            ),
          ),
        );
      },
    );
  }
}
