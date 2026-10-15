/// logs_screen.dart — Detection track log with filtering and false-positive marking
///
/// Features:
///   - Paginated list of detection tracks from /api/logs
///   - Filter by: station, class, status (active/resolved/all)
///   - Pull-to-refresh
///   - Swipe-to-mark-false-positive with undo Snackbar
///   - Tap track row to see snapshot thumbnail
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';

// ── Palette ─────────────────────────────────────────────────────
const _bg     = Color(0xFF09090B);
const _card   = Color(0xFF18181B);
const _border = Color(0xFF27272A);
const _fg     = Color(0xFFFAFAFA);
const _muted  = Color(0xFF71717A);
const _green  = Color(0xFF22C55E);
const _amber  = Color(0xFFF59E0B);
const _red    = Color(0xFFEF4444);
const _blue   = Color(0xFF3B82F6);

class LogsScreen extends StatefulWidget {
  final ApiService api;

  const LogsScreen({super.key, required this.api});

  @override
  State<LogsScreen> createState() => _LogsScreenState();
}

class _LogsScreenState extends State<LogsScreen> {
  // Filters
  int?    _filterStation;  // null = all
  String? _filterClass;    // null = all
  String  _filterStatus = 'active';

  // Pagination
  int _page     = 1;
  int _total    = 0;
  int _pages    = 1;

  // Data
  List<Map<String, dynamic>> _tracks = [];
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool reset = false}) async {
    if (_loading) return;
    if (reset) {
      _page   = 1;
      _tracks = [];
    }
    setState(() { _loading = true; _error = null; });
    try {
      final res = await widget.api.getTracksPage(
        station:  _filterStation,
        cls:      _filterClass,
        status:   _filterStatus == 'all' ? null : _filterStatus,
        page:     _page,
      );
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (reset) _tracks = [];
        _tracks.addAll((res['items'] as List? ?? []).cast<Map<String, dynamic>>());
        _total  = res['total'] as int? ?? 0;
        _pages  = res['pages'] as int? ?? 1;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() { _loading = false; _error = e.toString(); });
    }
  }

  Future<void> _markFalsePositive(String trackId, int index) async {
    final track = _tracks[index];
    setState(() => _tracks.removeAt(index));
    final ok = await widget.api.markFalsePositive(trackId);
    if (!mounted) return;
    if (!ok) {
      setState(() => _tracks.insert(index, track));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not mark as false positive.')),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: _card,
          content: const Text('Marked as false positive', style: TextStyle(color: _fg)),
          action: SnackBarAction(
            label: 'Undo',
            textColor: _green,
            onPressed: () {
              // Best-effort: reload to restore
              _load(reset: true);
            },
          ),
        ),
      );
    }
  }

  void _changeFilter({int? station, String? cls, String? status}) {
    setState(() {
      if (station != null || station == null && _filterStation != null)
        _filterStation = station;
      if (cls != null) _filterClass = cls == 'all' ? null : cls;
      if (status != null) _filterStatus = status;
    });
    _load(reset: true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        surfaceTintColor: Colors.transparent,
        foregroundColor: _fg,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Detection Logs',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: _fg)),
            if (_total > 0)
              Text('$_total track${_total == 1 ? '' : 's'}',
                  style: const TextStyle(fontSize: 11, color: _muted)),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded, size: 20, color: _muted),
            onPressed: () => _load(reset: true),
            tooltip: 'Refresh',
          ),
        ],
      ),
      body: Column(
        children: [
          // ── Filter row
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: Row(
              children: [
                _FilterChip(
                  label: 'Status',
                  value: _filterStatus,
                  options: const {'active': 'Active', 'resolved': 'Resolved', 'all': 'All'},
                  onChanged: (v) => _changeFilter(status: v),
                ),
                const SizedBox(width: 8),
                _FilterChip(
                  label: 'Station',
                  value: _filterStation?.toString() ?? 'all',
                  options: const {'all': 'All stations', '1': 'S1', '2': 'S2', '3': 'S3', '4': 'S4'},
                  onChanged: (v) => _changeFilter(station: v == 'all' ? null : int.tryParse(v)),
                ),
                const SizedBox(width: 8),
                _FilterChip(
                  label: 'Class',
                  value: _filterClass ?? 'all',
                  options: const {
                    'all': 'All classes',
                    'whitefly': 'Whitefly',
                    'whiteflies': 'Whiteflies',
                    'worm': 'Worm',
                    'fungus': 'Fungus',
                  },
                  onChanged: (v) => _changeFilter(cls: v),
                ),
              ],
            ),
          ),

          // ── Track list
          Expanded(
            child: _error != null
                ? _ErrorState(error: _error!, onRetry: () => _load(reset: true))
                : _tracks.isEmpty && !_loading
                    ? const _EmptyState()
                    : RefreshIndicator(
                        color: _green,
                        backgroundColor: _card,
                        onRefresh: () => _load(reset: true),
                        child: ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
                          itemCount: _tracks.length + (_page < _pages ? 1 : 0),
                          itemBuilder: (context, i) {
                            if (i == _tracks.length) {
                              // Load-more button
                              return Padding(
                                padding: const EdgeInsets.symmetric(vertical: 12),
                                child: TextButton(
                                  onPressed: () {
                                    _page++;
                                    _load();
                                  },
                                  child: const Text('Load more',
                                      style: TextStyle(color: _blue, fontSize: 13)),
                                ),
                              );
                            }
                            final track = _tracks[i];
                            return _TrackRow(
                              key: ValueKey(track['id']),
                              track: track,
                              onMarkFP: () => _markFalsePositive(
                                  track['id'] as String, i),
                              onTap: () => _showTrackDetail(track),
                            );
                          },
                        ),
                      ),
          ),

          if (_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: SizedBox(
                width: 22, height: 22,
                child: CircularProgressIndicator(strokeWidth: 2, color: _green),
              ),
            ),
        ],
      ),
    );
  }

  void _showTrackDetail(Map<String, dynamic> track) {
    showModalBottomSheet(
      context: context,
      backgroundColor: _card,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (_) => _TrackDetailSheet(track: track, api: widget.api),
    );
  }
}

// ── Track Row ─────────────────────────────────────────────────────

class _TrackRow extends StatelessWidget {
  final Map<String, dynamic> track;
  final VoidCallback onMarkFP;
  final VoidCallback onTap;

  const _TrackRow({
    super.key,
    required this.track,
    required this.onMarkFP,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cls       = track['class'] as String? ?? 'unknown';
    final conf      = (track['confidence'] as num?)?.toDouble() ?? 0.0;
    final station   = track['station'] as int? ?? 0;
    final position  = track['position'] as String? ?? '';
    final status    = track['status'] as String? ?? '';
    final scans     = track['scans_seen'] as int? ?? 1;
    final firstSeen = _parseTs(track['first_seen']);

    final clsColor = _classColor(cls);

    return Dismissible(
      key: ValueKey('dismissible_${track['id']}'),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(
          color: _amber.withOpacity(0.15),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: _amber.withOpacity(0.3)),
        ),
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.thumb_down_outlined, size: 16, color: _amber),
            SizedBox(width: 6),
            Text('False Positive', style: TextStyle(color: _amber, fontSize: 12, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
      confirmDismiss: (_) async {
        onMarkFP();
        return false;   // we handle removal ourselves
      },
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: _card,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: _border),
          ),
          child: Row(
            children: [
              // Class icon
              Container(
                width: 36, height: 36,
                decoration: BoxDecoration(
                  color: clsColor.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Icon(_classIcon(cls), size: 18, color: clsColor),
              ),
              const SizedBox(width: 12),

              // Details
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(_classLabel(cls),
                            style: TextStyle(color: clsColor, fontSize: 13,
                                fontWeight: FontWeight.w600)),
                        const SizedBox(width: 6),
                        Text('${(conf * 100).toStringAsFixed(0)}%',
                            style: const TextStyle(color: _muted, fontSize: 11)),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'S$station · ${_capitalise(position)} · $scans scan${scans == 1 ? '' : 's'}',
                      style: const TextStyle(color: _muted, fontSize: 11),
                    ),
                  ],
                ),
              ),

              // Right side
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  _StatusPill(status: status),
                  const SizedBox(height: 4),
                  Text(firstSeen, style: const TextStyle(color: _muted, fontSize: 10)),
                ],
              ),
              const SizedBox(width: 4),
              const Icon(Icons.chevron_right_rounded, size: 16, color: _muted),
            ],
          ),
        ),
      ),
    );
  }

  static String _classLabel(String cls) {
    const m = {
      'whitefly': 'Whitefly', 'whiteflies': 'Whiteflies',
      'worm': 'Worm', 'fungus': 'Fungus',
    };
    return m[cls] ?? cls;
  }

  static IconData _classIcon(String cls) {
    if (cls == 'fungus')     return Icons.coronavirus_outlined;
    if (cls.contains('worm')) return Icons.pest_control_rodent_outlined;
    return Icons.bug_report_outlined;
  }

  static Color _classColor(String cls) {
    if (cls == 'fungus')     return _amber;
    if (cls.contains('worm')) return _red;
    return _blue;
  }

  static String _capitalise(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

  static String _parseTs(dynamic ts) {
    if (ts == null) return '';
    try {
      final dt = DateTime.fromMillisecondsSinceEpoch((ts as num).toInt() * 1000);
      final now = DateTime.now();
      final diff = now.difference(dt);
      if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
      if (diff.inHours < 24)   return '${diff.inHours}h ago';
      return '${diff.inDays}d ago';
    } catch (_) { return ''; }
  }
}

// ── Status Pill ──────────────────────────────────────────────────

class _StatusPill extends StatelessWidget {
  final String status;
  const _StatusPill({required this.status});

  @override
  Widget build(BuildContext context) {
    final (color, label) = switch(status) {
      'active'   => (_green, 'Active'),
      'resolved' => (_muted, 'Resolved'),
      _          => (_muted, status),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(0.12),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(label,
          style: TextStyle(fontSize: 10, color: color, fontWeight: FontWeight.w600)),
    );
  }
}

// ── Track detail bottom sheet ────────────────────────────────────

class _TrackDetailSheet extends StatelessWidget {
  final Map<String, dynamic> track;
  final ApiService api;

  const _TrackDetailSheet({required this.track, required this.api});

  @override
  Widget build(BuildContext context) {
    final cls      = track['class'] as String? ?? 'unknown';
    final conf     = (track['confidence'] as num?)?.toDouble() ?? 0.0;
    final station  = track['station'] as int? ?? 0;
    final position = track['position'] as String? ?? '';
    final scans    = track['scans_seen'] as int? ?? 1;
    final thumb    = track['thumb_path'] as String?;
    final status   = track['status'] as String? ?? '';
    final bboxStr  = track['bbox'] as String? ?? '';

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Handle
          Center(child: Container(
            width: 36, height: 4,
            decoration: BoxDecoration(color: const Color(0xFF3F3F46), borderRadius: BorderRadius.circular(2)),
          )),
          const SizedBox(height: 16),

          // Header
          Row(children: [
            Text(_rowLabel(cls), style: const TextStyle(color: _fg, fontSize: 17, fontWeight: FontWeight.w700)),
            const Spacer(),
            _StatusPill(status: status),
          ]),
          const SizedBox(height: 4),
          Text('Station $station · ${_cap(position)} · ${(conf*100).toStringAsFixed(0)}% conf · $scans scan${scans==1?'':'s'}',
              style: const TextStyle(color: _muted, fontSize: 12)),

          const SizedBox(height: 14),

          // Thumbnail placeholder
          Container(
            height: 120,
            decoration: BoxDecoration(
              color: const Color(0xFF27272A),
              borderRadius: BorderRadius.circular(10),
            ),
            child: thumb != null && thumb.isNotEmpty
                ? ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Center(
                      child: Icon(Icons.image_outlined, color: _muted, size: 32),
                    ),
                  )
                : const Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.image_not_supported_outlined, color: _muted, size: 28),
                        SizedBox(height: 6),
                        Text('No snapshot', style: TextStyle(color: _muted, fontSize: 12)),
                      ],
                    ),
                  ),
          ),

          if (bboxStr.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('BBox: $bboxStr', style: const TextStyle(color: _muted, fontSize: 11)),
          ],

          const SizedBox(height: 16),
          Row(
            children: [
              Text('Track ID: ${(track['id'] as String? ?? '').substring(0, 8)}…',
                  style: const TextStyle(color: _muted, fontSize: 11)),
            ],
          ),
        ],
      ),
    );
  }

  static String _rowLabel(String cls) {
    const m = {'whitefly': 'Whitefly', 'whiteflies': 'Whiteflies',
                'worm': 'Worm', 'fungus': 'Fungus'};
    return m[cls] ?? cls;
  }

  static String _cap(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

// ── Filter chip ──────────────────────────────────────────────────

class _FilterChip extends StatelessWidget {
  final String label;
  final String value;
  final Map<String, String> options;
  final ValueChanged<String> onChanged;

  const _FilterChip({
    required this.label,
    required this.value,
    required this.options,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final isDefault = value == 'all' || value == 'active';
    return GestureDetector(
      onTap: () async {
        final result = await showModalBottomSheet<String>(
          context: context,
          backgroundColor: const Color(0xFF18181B),
          shape: const RoundedRectangleBorder(
              borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
          builder: (_) => _FilterSheet(
            label: label, value: value, options: options,
          ),
        );
        if (result != null) onChanged(result);
      },
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          color: isDefault ? const Color(0xFF18181B) : _green.withOpacity(0.12),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: isDefault ? const Color(0xFF27272A) : _green.withOpacity(0.4),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              options[value] ?? value,
              style: TextStyle(
                fontSize: 12,
                color: isDefault ? _muted : _green,
                fontWeight: isDefault ? FontWeight.w500 : FontWeight.w600,
              ),
            ),
            const SizedBox(width: 4),
            Icon(Icons.expand_more_rounded, size: 14,
                color: isDefault ? _muted : _green),
          ],
        ),
      ),
    );
  }
}

class _FilterSheet extends StatelessWidget {
  final String label;
  final String value;
  final Map<String, String> options;

  const _FilterSheet({required this.label, required this.value, required this.options});

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(20, 16, 20, 40),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Center(child: Container(
          width: 36, height: 4,
          decoration: BoxDecoration(color: const Color(0xFF3F3F46), borderRadius: BorderRadius.circular(2)),
        )),
        const SizedBox(height: 14),
        Text(label, style: const TextStyle(color: _fg, fontSize: 15, fontWeight: FontWeight.w700)),
        const SizedBox(height: 12),
        ...options.entries.map((e) => _OptionRow(
          label: e.value, selected: e.key == value,
          onTap: () => Navigator.pop(context, e.key),
        )),
      ],
    ),
  );
}

class _OptionRow extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _OptionRow({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) => GestureDetector(
    onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 4),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: _border.withOpacity(0.5))),
      ),
      child: Row(children: [
        Expanded(child: Text(label,
            style: TextStyle(color: selected ? _green : _fg, fontSize: 14,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w400))),
        if (selected) const Icon(Icons.check_rounded, size: 16, color: _green),
      ]),
    ),
  );
}

// ── Empty / Error states ─────────────────────────────────────────

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) => const Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.search_off_rounded, size: 48, color: _muted),
        SizedBox(height: 12),
        Text('No tracks found', style: TextStyle(color: _fg, fontSize: 15, fontWeight: FontWeight.w600)),
        SizedBox(height: 4),
        Text('Try changing the filters above', style: TextStyle(color: _muted, fontSize: 13)),
      ],
    ),
  );
}

class _ErrorState extends StatelessWidget {
  final String error;
  final VoidCallback onRetry;
  const _ErrorState({required this.error, required this.onRetry});

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.wifi_off_rounded, size: 40, color: _muted),
          const SizedBox(height: 12),
          const Text('Could not load logs', style: TextStyle(color: _fg, fontSize: 15, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(error, style: const TextStyle(color: _muted, fontSize: 12), textAlign: TextAlign.center),
          const SizedBox(height: 16),
          TextButton(onPressed: onRetry,
              child: const Text('Retry', style: TextStyle(color: _green))),
        ],
      ),
    ),
  );
}
