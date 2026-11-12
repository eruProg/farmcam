import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../core/farmcam_controller.dart';
import '../core/models.dart';
import '../core/theme.dart';
import '../widgets/daily_chart.dart';
import '../widgets/ui.dart';
import 'monitor_page.dart';

/// Reports (totals, 7/14/30-day chart, per lettuce) and every detection of
/// that period, grouped by day, each with its photo. Reloads by itself when a
/// new detection arrives.
class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key});
  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  FarmCamController? _controller;
  Map<String, dynamic>? _summary;
  List<Track> _tracks = [];
  int _days = 14, _page = 1, _pages = 1, _total = 0, _request = 0;
  int? _station;
  String? _class;
  bool _hideFalse = true, _loading = true;
  String? _error, _newest;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_controller == null) {
      _controller = FarmCamScope.of(context)
        ..link.addListener(_onLink)
        ..snapshot.addListener(_onSnapshot);
      _newest = _controller!.snapshot.value?.recent.firstOrNull?.id;
      _onLink();
    }
  }

  void _onLink() {
    if (_controller!.link.value.isOnline && (_summary == null || _error != null)) _load();
  }

  /// A new detection arrived: show it without waiting for a pull-to-refresh.
  void _onSnapshot() {
    final newest = _controller!.snapshot.value?.recent.firstOrNull?.id;
    if (newest == null || newest == _newest) return;
    _newest = newest;
    if (!_loading && !_controller!.isDemo) _load();
  }

  @override
  void dispose() {
    _controller?.link.removeListener(_onLink);
    _controller?.snapshot.removeListener(_onSnapshot);
    super.dispose();
  }

  Future<void> _load({bool resetPage = false}) async {
    final c = _controller!;
    if (c.isDemo) {
      setState(() {
        _loading = false;
        _tracks = c.snapshot.value?.recent ?? [];
        _total = _tracks.length;
        _summary = {
          'total': _total, 'false_positives': 0, 'completed_scans': 12,
          'totals': {'worm': 3, 'fungus': 1},
          'days': [
            for (var i = _days - 1; i >= 0; i--)
              {
                'date': DateTime.now().subtract(Duration(days: i)).toIso8601String().substring(0, 10),
                'classes': {if (i % 3 == 0) 'worm': 1 + i % 2, if (i % 5 == 0) 'fungus': 1}
              }
          ],
          'stations': [for (var s = 1; s <= 4; s++) {'station': s, 'classes': {if (s == 2) 'worm': 3, if (s == 3) 'fungus': 1}, 'active': s == 2 ? 2 : 0}],
          'precision': {},
        };
      });
      return;
    }
    final request = ++_request;
    if (resetPage) _page = 1;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        c.api.reportSummary(days: _days),
        c.api.getTracksPage(
            station: _station,
            cls: _class,
            isFalsePositive: _hideFalse ? false : null,
            dateFrom: DateTime.now().subtract(Duration(days: _days)),
            page: _page),
      ]);
      if (!mounted || request != _request) return;
      setState(() {
        _summary = results[0];
        final page = results[1];
        _tracks = [for (final t in (page['items'] as List)) Track.fromJson(Map<String, dynamic>.from(t))];
        _pages = page['pages'] as int? ?? 1;
        _total = page['total'] as int? ?? _tracks.length;
        _loading = false;
      });
    } catch (_) {
      if (mounted && request == _request) {
        setState(() {
          _loading = false;
          _error = 'Could not load history. Check the connection to FarmCam.';
        });
      }
    }
  }

  Future<void> _exportCsv() async {
    final url = Uri.parse(_controller!.api.reportCsvUrl);
    final ok = await launchUrl(url, mode: LaunchMode.externalApplication);
    if (!ok && mounted) showMessage(context, 'Open ${url.toString()} in a browser to download the CSV.');
  }

  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final text = Theme.of(context).textTheme;
    final s = _summary;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.all(Space.lg), children: [
        Row(children: [
          Expanded(child: Text('History', style: text.headlineSmall)),
          IconButton(tooltip: 'Refresh', onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh_rounded)),
          IconButton(tooltip: 'Download as spreadsheet (CSV)', onPressed: _controller?.isDemo == true ? null : _exportCsv,
              icon: const Icon(Icons.download_rounded)),
        ]),
        const SizedBox(height: Space.md),
        if (_error != null)
          SectionCard(child: EmptyState(icon: Icons.wifi_off_rounded, title: _error!, actionLabel: 'Retry', onAction: _load))
        else if (s == null)
          const SectionCard(child: SkeletonBox(height: 200))
        else ...[
          Row(children: [
            _Tile('Detections', '${s['total'] ?? 0}', farm.pest),
            const SizedBox(width: Space.sm),
            _Tile('Scans done', '${s['completed_scans'] ?? 0}', farm.healthy),
            const SizedBox(width: Space.sm),
            _Tile('False alarms', '${s['false_positives'] ?? 0}', farm.muted),
          ]),
          const SizedBox(height: Space.md),
          SectionCard(
            title: 'Last $_days days',
            icon: Icons.bar_chart_rounded,
            trailing: SegmentedButton<int>(
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
              showSelectedIcon: false,
              segments: const [ButtonSegment(value: 7, label: Text('7d')), ButtonSegment(value: 14, label: Text('14d')), ButtonSegment(value: 30, label: Text('30d'))],
              selected: {_days},
              onSelectionChanged: (v) {
                setState(() => _days = v.first);
                _load(resetPage: true);
              },
            ),
            child: DailyChart(days: [for (final d in (s['days'] as List? ?? [])) Map<String, dynamic>.from(d)]),
          ),
          const SizedBox(height: Space.md),
          SectionCard(
            title: 'By lettuce',
            icon: Icons.grid_view_rounded,
            child: Column(children: [
              for (final st in (s['stations'] as List? ?? []))
                _StationRow(Map<String, dynamic>.from(st)),
            ]),
          ),
        ],
        const SizedBox(height: Space.md),
        SectionCard(
          title: 'Detections',
          icon: Icons.list_alt_rounded,
          trailing: Text('$_total in $_days days', style: TextStyle(color: farm.muted)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Wrap(spacing: Space.sm, runSpacing: Space.sm, children: [
              for (final st in [null, 1, 2, 3, 4])
                ChoiceChip(
                  label: Text(st == null ? 'All lettuces' : 'Lettuce $st'),
                  selected: _station == st,
                  onSelected: (_) {
                    setState(() => _station = st);
                    _load(resetPage: true);
                  },
                ),
            ]),
            const SizedBox(height: Space.sm),
            Wrap(spacing: Space.sm, runSpacing: Space.sm, children: [
              for (final c in [null, 'worm', 'whiteflies', 'fungus'])
                ChoiceChip(
                  label: Text(c == null ? 'All types' : classLabel(c)),
                  selected: _class == c,
                  onSelected: (_) {
                    setState(() => _class = c);
                    _load(resetPage: true);
                  },
                ),
              FilterChip(
                label: const Text('Hide false alarms'),
                selected: _hideFalse,
                onSelected: (v) {
                  setState(() => _hideFalse = v);
                  _load(resetPage: true);
                },
              ),
            ]),
            const SizedBox(height: Space.sm),
            if (_loading)
              const Column(children: [SkeletonBox(height: 56), SizedBox(height: 8), SkeletonBox(height: 56)])
            else if (_tracks.isEmpty)
              EmptyState(
                  icon: Icons.eco_outlined,
                  title: 'No detections in this period',
                  message: _station == null && _class == null
                      ? 'Your lettuces look healthy.'
                      : 'Nothing found with these filters.')
            else
              for (final (i, t) in _tracks.indexed) ...[
                if (i == 0 || !_sameDay(_tracks[i - 1].firstSeen, t.firstSeen)) _DayHeader(t.firstSeen),
                TrackTile(track: t, onChanged: _load),
              ],
            if (_pages > 1)
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                IconButton(
                    onPressed: _page > 1 ? () {
                      _page--;
                      _load();
                    } : null,
                    icon: const Icon(Icons.chevron_left_rounded)),
                Text('$_page / $_pages'),
                IconButton(
                    onPressed: _page < _pages ? () {
                      _page++;
                      _load();
                    } : null,
                    icon: const Icon(Icons.chevron_right_rounded)),
              ]),
          ]),
        ),
      ]),
    );
  }
}

bool _sameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

/// "Today", "Yesterday" or "Mon, Oct 27" above each day's detections.
class _DayHeader extends StatelessWidget {
  const _DayHeader(this.day);
  final DateTime day;
  @override
  Widget build(BuildContext context) {
    const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    final now = DateTime.now();
    final label = _sameDay(day, now)
        ? 'Today'
        : _sameDay(day, now.subtract(const Duration(days: 1)))
            ? 'Yesterday'
            : '${weekdays[day.weekday - 1]}, ${months[day.month - 1]} ${day.day}';
    return Padding(
      padding: const EdgeInsets.only(top: Space.md, bottom: 2),
      child: Text(label.toUpperCase(),
          style: Theme.of(context).textTheme.labelSmall?.copyWith(color: FarmColors.of(context).muted, letterSpacing: 0.6)),
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile(this.label, this.value, this.color);
  final String label, value;
  final Color color;
  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(Space.md),
        decoration: BoxDecoration(
            color: farm.card, borderRadius: BorderRadius.circular(14), border: Border.all(color: farm.border)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(value, style: Theme.of(context).textTheme.headlineSmall?.copyWith(color: color)),
          Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: farm.muted, fontSize: 12)),
        ]),
      ),
    );
  }
}

class _StationRow extends StatelessWidget {
  const _StationRow(this.data);
  final Map<String, dynamic> data;
  @override
  Widget build(BuildContext context) {
    final farm = FarmColors.of(context);
    final classes = Map<String, dynamic>.from(data['classes'] as Map? ?? {});
    final total = classes.values.fold<int>(0, (a, b) => a + (b as num).toInt());
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [
        ConstrainedBox(
          constraints: const BoxConstraints(minWidth: 84),
          child: Text('Lettuce ${data['station']}', style: const TextStyle(fontWeight: FontWeight.w700)),
        ),
        const SizedBox(width: Space.sm),
        Expanded(
          child: total == 0
              ? Text('No detections', style: TextStyle(color: farm.muted))
              : Wrap(spacing: 6, runSpacing: 4, children: [
                  for (final e in classes.entries)
                    StatusPill('${e.value} ${classLabel(e.key).toLowerCase()}', color: DailyChart.colorFor(farm, e.key)),
                ]),
        ),
        if ((data['active'] as num? ?? 0) > 0)
          StatusPill('${data['active']} active', color: farm.pest, icon: Icons.circle, background: farm.pestBg),
      ]),
    );
  }
}
