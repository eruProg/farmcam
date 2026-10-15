import 'package:flutter/material.dart';
import '../data/app_state.dart';

// ─── Design Tokens ──────────────────────────────────────────────
const _card    = Color(0xFFFFFFFF);
const _border  = Color(0xFFE4E4E7);
const _fg      = Color(0xFF18181B);
const _muted   = Color(0xFF71717A);
const _green   = Color(0xFF16A34A);
const _greenBg = Color(0xFFDCFCE7);
const _amber   = Color(0xFFD97706);
const _amberBg = Color(0xFFFEF3C7);
const _red     = Color(0xFFDC2626);
const _redBg   = Color(0xFFFEE2E2);
const _blue    = Color(0xFF2563EB);
const _blueBg  = Color(0xFFEFF6FF);
// ────────────────────────────────────────────────────────────────

class LettuceCard extends StatelessWidget {
  final LettuceStation station;
  final bool isActive;
  final VoidCallback? onTap;

  const LettuceCard({
    super.key,
    required this.station,
    this.isActive = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final s = station;

    // Color scheme by status
    Color accent, accentBg;
    IconData icon;
    String statusText;

    switch (s.status) {
      case LettuceStatus.pest:
        accent = _red;
        accentBg = _redBg;
        icon = Icons.bug_report_outlined;
        statusText = 'Pest Detected';
      case LettuceStatus.fungus:
        accent = _amber;
        accentBg = _amberBg;
        icon = Icons.coronavirus_outlined;
        statusText = 'Fungus Detected';
      case LettuceStatus.both:
        accent = _red;
        accentBg = _redBg;
        icon = Icons.report_problem_outlined;
        statusText = 'Pest & Fungus Detected';
      case LettuceStatus.unknown:
        accent = _blue;
        accentBg = _blueBg;
        icon = Icons.search_rounded;
        statusText = 'Scanning…';
      default:
        accent = _green;
        accentBg = _greenBg;
        icon = Icons.check_circle_outline_rounded;
        statusText = 'Healthy';
    }

    // Confidence display
    final confPct = (s.confidence * 100).toStringAsFixed(0);
    final hasConf = s.confidence > 0 && s.status != LettuceStatus.clear && s.status != LettuceStatus.unknown;

    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _card,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: isActive ? accent.withOpacity(0.5) : _border,
            width: isActive ? 1.5 : 1,
          ),
          boxShadow: isActive
              ? [BoxShadow(color: accent.withOpacity(0.08), blurRadius: 12, spreadRadius: 1)]
              : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Header: icon + name + active indicator
            Row(
              children: [
                Container(
                  width: 32, height: 32,
                  decoration: BoxDecoration(
                    color: accentBg,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(icon, size: 16, color: accent),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(s.name,
                          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: _fg),
                          overflow: TextOverflow.ellipsis),
                      Text(statusText,
                          style: TextStyle(fontSize: 11, color: accent, fontWeight: FontWeight.w500)),
                    ],
                  ),
                ),
                if (isActive)
                  Container(
                    width: 8, height: 8,
                    decoration: BoxDecoration(
                      color: _green,
                      shape: BoxShape.circle,
                      boxShadow: [BoxShadow(color: _green.withOpacity(0.4), blurRadius: 4)],
                    ),
                  ),
              ],
            ),

            const SizedBox(height: 10),
            const Divider(color: _border, height: 1),
            const SizedBox(height: 10),

            // ── Metrics
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  // Pest / Fungus counts
                  if (s.status == LettuceStatus.pest || s.pestCount > 0)
                    _MiniStat(
                      icon: Icons.bug_report_outlined,
                      label: 'Pests',
                      value: '${s.pestCount}',
                      color: _red,
                    ),

                  if (s.status == LettuceStatus.fungus || s.fungusCoveragePct > 0) ...[
                    _MiniStat(
                      icon: Icons.coronavirus_outlined,
                      label: 'Coverage',
                      value: '${s.fungusCoveragePct.toStringAsFixed(1)}%',
                      color: _amber,
                    ),
                  ],

                  if (hasConf)
                    _MiniStat(
                      icon: Icons.analytics_outlined,
                      label: 'Confidence',
                      value: '$confPct%',
                      color: _blue,
                    ),

                  // Severity badges — PEST and FUNGUS shown independently.
                  // BUG FIX: this used to be one shared badge driven by
                  // severityLevel (worst-of-both), so e.g. a MEDIUM pest
                  // reading and a separate HIGH fungus reading collapsed
                  // into a single "HIGH" badge with no way to tell which
                  // problem was actually the bad one. Each now gets its
                  // own badge, in its own color, from its own severity.
                  if (s.pestCount > 0 && s.pestSeverityLevel != 'none')
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: _SeverityBadge(label: 'PEST', level: s.pestSeverityLevel),
                    ),
                  if (s.fungusCoveragePct > 0 && s.fungusSeverityLevel != 'none')
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: _SeverityBadge(label: 'FUNGUS', level: s.fungusSeverityLevel),
                    ),

                  // Healthy placeholder
                  if (s.status == LettuceStatus.clear && s.pestCount == 0 && s.fungusCoveragePct == 0)
                    const Text('No threats detected',
                        style: TextStyle(fontSize: 11, color: _muted)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SeverityBadge extends StatelessWidget {
  final String label;  // 'PEST' or 'FUNGUS'
  final String level;  // 'low' | 'medium' | 'high'
  const _SeverityBadge({required this.label, required this.level});

  @override
  Widget build(BuildContext context) {
    final color = level == 'high' ? _red : level == 'medium' ? _amber : _green;
    final bg = level == 'high' ? _redBg : level == 'medium' ? _amberBg : _greenBg;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(4)),
      child: Text(
        '$label: ${level.toUpperCase()}',
        style: TextStyle(fontSize: 9, fontWeight: FontWeight.w700, letterSpacing: 0.5, color: color),
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color color;
  const _MiniStat({required this.icon, required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(
        children: [
          Icon(icon, size: 12, color: color),
          const SizedBox(width: 4),
          Text(label, style: const TextStyle(fontSize: 10, color: _muted)),
          const Spacer(),
          Text(value, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: color)),
        ],
      ),
    );
  }
}
