import 'package:flutter/material.dart';
import '../services/api_service.dart';

class LogsScreen extends StatefulWidget {
  const LogsScreen({super.key, required this.api});
  final ApiService api;
  @override
  State<LogsScreen> createState() => _LogsScreenState();
}

class _LogsScreenState extends State<LogsScreen> {
  int? _station;
  String? _class;
  String? _status = 'active';
  bool? _fp;
  DateTimeRange? _dates;
  int _page = 1, _pages = 1, _total = 0, _request = 0;
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tracks = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool reset = false}) async {
    final request = ++_request;
    if (reset) _page = 1;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.api.getTracksPage(
          station: _station,
          cls: _class,
          status: _status,
          isFalsePositive: _fp,
          page: _page,
          dateFrom: _dates?.start,
          dateTo: _dates?.end
              .add(const Duration(days: 1))
              .subtract(const Duration(milliseconds: 1)));
      if (!mounted || request != _request) return;
      setState(() {
        _tracks = (result['items'] as List).cast<Map<String, dynamic>>();
        _pages = result['pages'] as int;
        _total = result['total'] as int;
        _loading = false;
      });
    } catch (_) {
      if (mounted && request == _request) {
        setState(() {
          _loading = false;
          _error = 'Detection history unavailable';
        });
      }
    }
  }

  Widget _filter<T>(
      String label, T? value, Map<T, String> options, ValueChanged<T?> change) {
    return SizedBox(
        width: 152,
        child: DropdownButtonFormField<T>(
          initialValue: value,
          isExpanded: true,
          decoration: InputDecoration(
              labelText: label,
              border: const OutlineInputBorder(),
              isDense: true),
          items: [
            DropdownMenuItem<T>(value: null, child: const Text('All')),
            ...options.entries.map(
                (e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
          ],
          onChanged: (v) {
            change(v);
            _load(reset: true);
          },
        ));
  }

  Future<void> _detail(Map<String, dynamic> track) async {
    final result = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => _TrackDetail(track: track, api: widget.api));
    if (result == true && mounted) _load();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Detection history'), actions: [
          IconButton(
              tooltip: 'Refresh',
              onPressed: () => _load(),
              icon: const Icon(Icons.refresh))
        ]),
        body: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 960),
                child: Column(children: [
                  Padding(
                      padding: const EdgeInsets.all(20),
                      child: Wrap(spacing: 12, runSpacing: 16, children: [
                        _filter<int>(
                            'Station',
                            _station,
                            {
                              1: 'Station 1',
                              2: 'Station 2',
                              3: 'Station 3',
                              4: 'Station 4'
                            },
                            (v) => _station = v),
                        _filter<String>(
                            'Detection',
                            _class,
                            {
                              'whiteflies': 'Whiteflies',
                              'worm': 'Worm',
                              'fungus': 'Fungus'
                            },
                            (v) => _class = v),
                        _filter<String>(
                            'Status',
                            _status,
                            {'active': 'Active', 'resolved': 'Resolved'},
                            (v) => _status = v),
                        _filter<bool>(
                            'Review',
                            _fp,
                            {true: 'False positive', false: 'Not marked false'},
                            (v) => _fp = v),
                        OutlinedButton.icon(
                            icon: const Icon(Icons.date_range),
                            label: Text(_dates == null
                                ? 'Date range'
                                : 'Date range selected'),
                            onPressed: () async {
                              final dates = await showDateRangePicker(
                                  context: context,
                                  firstDate: DateTime(2020),
                                  lastDate: DateTime.now(),
                                  initialDateRange: _dates);
                              if (dates != null && mounted) {
                                _dates = dates;
                                _load(reset: true);
                              }
                            }),
                        if (_dates != null)
                          IconButton(
                              tooltip: 'Clear dates',
                              onPressed: () {
                                _dates = null;
                                _load(reset: true);
                              },
                              icon: const Icon(Icons.close)),
                      ])),
                  const Divider(height: 1),
                  Expanded(
                      child: _loading
                          ? const Center(child: CircularProgressIndicator())
                          : _error != null
                              ? Center(
                                  child: Column(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                      Text(_error!),
                                      const SizedBox(height: 12),
                                      TextButton(
                                          onPressed: _load,
                                          child: const Text('Retry'))
                                    ]))
                              : _tracks.isEmpty
                                  ? const Center(child: Text('No detections'))
                                  : RefreshIndicator(
                                      onRefresh: () => _load(),
                                      child: ListView.separated(
                                          itemCount: _tracks.length,
                                          separatorBuilder: (_, __) =>
                                              const Divider(height: 1),
                                          itemBuilder: (context, i) {
                                            final track = _tracks[i];
                                            final thumb =
                                                track['thumb_path'] as String?;
                                            return ListTile(
                                                contentPadding:
                                                    const EdgeInsets.symmetric(
                                                        horizontal: 20,
                                                        vertical: 12),
                                                leading: SizedBox(
                                                    width: 56,
                                                    height: 56,
                                                    child: thumb == null
                                                        ? const Icon(
                                                            Icons.eco_outlined)
                                                        : Image.network(
                                                            widget.api
                                                                .snapshotUrl(
                                                                    thumb),
                                                            fit: BoxFit.cover,
                                                            errorBuilder: (_,
                                                                    __, ___) =>
                                                                const Icon(Icons
                                                                    .image_not_supported_outlined))),
                                                title: Text(
                                                    _label(track['class'].toString())),
                                                subtitle: Text('Station ${track['station']} · ${track['position']} · ${((track['confidence'] as num) * 100).round()}%'),
                                                trailing: Icon(track['is_fp'] == 1 ? Icons.flag_outlined : Icons.chevron_right),
                                                onTap: () => _detail(track));
                                          }))),
                  Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 12),
                      child: Row(children: [
                        Expanded(
                            child:
                                Text('$_total detections · $_page / $_pages')),
                        IconButton(
                            tooltip: 'Previous page',
                            onPressed: !_loading && _page > 1
                                ? () {
                                    _page--;
                                    _load();
                                  }
                                : null,
                            icon: const Icon(Icons.chevron_left)),
                        IconButton(
                            tooltip: 'Next page',
                            onPressed: !_loading && _page < _pages
                                ? () {
                                    _page++;
                                    _load();
                                  }
                                : null,
                            icon: const Icon(Icons.chevron_right)),
                      ])),
                ]))),
      );
}

String _label(String cls) =>
    {
      'whiteflies': 'Whiteflies',
      'whitefly': 'Whitefly',
      'worm': 'Worm',
      'fungus': 'Fungus'
    }[cls] ??
    cls;

class _TrackDetail extends StatefulWidget {
  const _TrackDetail({required this.track, required this.api});
  final Map<String, dynamic> track;
  final ApiService api;
  @override
  State<_TrackDetail> createState() => _TrackDetailState();
}

class _TrackDetailState extends State<_TrackDetail> {
  bool _busy = false;
  String? _error;
  Future<void> _review(bool falsePositive) async {
    setState(() => _busy = true);
    final id = widget.track['id'] as String;
    final ok = falsePositive
        ? await widget.api.markFalsePositive(id)
        : await widget.api.markReviewed(id);
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context, true);
      return;
    }
    setState(() {
      _busy = false;
      _error = 'Review could not be saved';
    });
  }

  @override
  Widget build(BuildContext context) {
    final track = widget.track;
    final image = track['snapshot_path'] as String?;
    return SafeArea(
        child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(_label(track['class'].toString()),
                      style: Theme.of(context).textTheme.headlineSmall),
                  const SizedBox(height: 8),
                  Text(
                      'Station ${track['station']} · ${track['position']} · ${track['scans_seen']} scans'),
                  const SizedBox(height: 20),
                  if (image != null)
                    ConstrainedBox(
                        constraints: BoxConstraints(
                            maxHeight: MediaQuery.sizeOf(context).height * .45),
                        child: Image.network(widget.api.snapshotUrl(image),
                            fit: BoxFit.contain,
                            errorBuilder: (_, __, ___) => const Padding(
                                padding: EdgeInsets.all(24),
                                child: Text('Snapshot unavailable'))))
                  else
                    const Padding(
                        padding: EdgeInsets.all(24),
                        child: Text('No snapshot')),
                  const SizedBox(height: 20),
                  if (_error != null)
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  if (track['is_fp'] != 1)
                    Wrap(spacing: 12, runSpacing: 12, children: [
                      FilledButton(
                          onPressed: _busy ? null : () => _review(false),
                          child: const Text('Confirm detection')),
                      OutlinedButton(
                          onPressed: _busy ? null : () => _review(true),
                          child: const Text('False positive')),
                    ])
                  else
                    const Text('Marked as false positive'),
                ])));
  }
}
