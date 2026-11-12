import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;

/// A FarmCam Pi answering on the network.
class FoundPi {
  const FoundPi(this.ip, this.port, {this.hostname = '', this.build = ''});
  final String ip, hostname, build;
  final int port;
  @override
  bool operator ==(Object other) => other is FoundPi && other.ip == ip && other.port == port;
  @override
  int get hashCode => Object.hash(ip, port);
}

/// Finds the FarmCam Pi on the hotspot without typing its IP.
///
/// The phone usually IS the hotspot, so it is not a Wi-Fi client and
/// Wi-Fi info APIs return nothing; `NetworkInterface.list()` still shows the
/// hotspot's own address. Each private /24 the phone is on is probed on port
/// 8000 with `GET /api/hello`, which only a FarmCam Pi answers with
/// `{"app":"farmcam"}`.
class PiDiscovery {
  PiDiscovery({this.port = 8000, this.parallel = 40,
      this.timeout = const Duration(milliseconds: 700),
      Future<List<String>> Function()? localAddresses})
      : _localAddresses = localAddresses ?? _interfaceAddresses;

  final int port, parallel;
  final Duration timeout;
  final Future<List<String>> Function() _localAddresses;

  /// Common Android/iOS hotspot ranges, tried when no interface is found.
  static const fallbackPrefixes = ['192.168.43', '172.20.10', '192.168.137', '10.0.0'];

  static Future<List<String>> _interfaceAddresses() async {
    try {
      final ifaces = await NetworkInterface.list(
          type: InternetAddressType.IPv4, includeLoopback: false);
      return [for (final i in ifaces) for (final a in i.addresses) a.address];
    } catch (_) {
      return const [];
    }
  }

  static bool isPrivate(String ip) {
    final p = ip.split('.').map(int.tryParse).toList();
    if (p.length != 4 || p.contains(null)) return false;
    return p[0] == 10 ||
        (p[0] == 192 && p[1] == 168) ||
        (p[0] == 172 && p[1]! >= 16 && p[1]! <= 31);
  }

  /// One quick check of a single address.
  Future<FoundPi?> probe(String ip, {int? port}) async {
    final p = port ?? this.port;
    try {
      final resp = await http.get(Uri.parse('http://$ip:$p/api/hello')).timeout(timeout);
      if (resp.statusCode != 200) return null;
      final data = jsonDecode(resp.body);
      if (data is Map && data['app'] == 'farmcam') {
        return FoundPi(ip, p, hostname: '${data['hostname'] ?? ''}', build: '${data['build'] ?? ''}');
      }
    } catch (_) {}
    return null;
  }

  /// The /24 prefixes to scan, own networks first.
  Future<List<String>> prefixes() async {
    final own = [
      for (final ip in await _localAddresses())
        if (isPrivate(ip)) ip.substring(0, ip.lastIndexOf('.'))
    ];
    return {...own, if (own.isEmpty) ...fallbackPrefixes}.toList();
  }

  /// Scans for Pis. [preferred] (e.g. the last good IP) is tried first and
  /// returned immediately when it answers. [onProgress] gets 0..1.
  Future<List<FoundPi>> scan({String? preferred, void Function(double)? onProgress,
      bool stopAtFirst = false}) async {
    if (preferred != null && preferred.isNotEmpty) {
      final hit = await probe(preferred);
      if (hit != null) return [hit];
    }
    final hosts = <String>[
      for (final prefix in await prefixes())
        for (var n = 1; n <= 254; n++) '$prefix.$n'
    ]..remove(preferred);
    final found = <FoundPi>[];
    var done = 0;
    var next = 0;
    Future<void> worker() async {
      while (next < hosts.length && !(stopAtFirst && found.isNotEmpty)) {
        final ip = hosts[next++];
        final hit = await probe(ip);
        if (hit != null) found.add(hit);
        done++;
        onProgress?.call(done / hosts.length);
      }
    }
    await Future.wait([for (var i = 0; i < parallel; i++) worker()]);
    onProgress?.call(1);
    return found;
  }
}
