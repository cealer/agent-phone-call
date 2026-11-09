// Look and feel: a sober dark theme, contact cards and the animated call pieces.
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'texto_llamada.dart';

class Palette {
  static const background = Color(0xFF0D0E12);
  static const surface = Color(0xFF17191F);
  static const surfaceHigh = Color(0xFF22252D);
  static const outline = Color(0xFF2C3039);
  static const text = Color(0xFFF2F2F4);
  static const textDim = Color(0xFF9097A3);
  static const accent = Color(0xFFD97757);
  static const green = Color(0xFF3DD68C);
  static const amber = Color(0xFFF5B841);
  static const orange = Color(0xFFFF7A59);
  static const grey = Color(0xFF5E6470);
  static const red = Color(0xFFE5484D);
}

ThemeData llamadaTheme() {
  final scheme =
      ColorScheme.fromSeed(
        seedColor: Palette.accent,
        brightness: Brightness.dark,
      ).copyWith(
        primary: Palette.accent,
        surface: Palette.background,
        surfaceContainerHighest: Palette.surfaceHigh,
        onSurface: Palette.text,
        onSurfaceVariant: Palette.textDim,
        error: Palette.red,
      );
  final base = ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    brightness: Brightness.dark,
  );
  return base.copyWith(
    scaffoldBackgroundColor: Palette.background,
    appBarTheme: const AppBarTheme(
      systemOverlayStyle: SystemUiOverlayStyle.light,
    ),
    textTheme: base.textTheme.apply(
      bodyColor: Palette.text,
      displayColor: Palette.text,
    ),
    snackBarTheme: const SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: Palette.surfaceHigh,
      contentTextStyle: TextStyle(color: Palette.text),
    ),
    pageTransitionsTheme: const PageTransitionsTheme(
      builders: {TargetPlatform.android: FadeForwardsPageTransitionsBuilder()},
    ),
  );
}

/// Color and label for a Herdr agent state.
Color statusColor(String status) => switch (status) {
  'offline' => Palette.grey,
  'working' => Palette.amber,
  'blocked' => Palette.orange,
  _ => Palette.green,
};

/// A stable two-color gradient per name, so each contact keeps its own avatar.
LinearGradient avatarGradient(String name) {
  const pairs = [
    [Color(0xFFD97757), Color(0xFF9B4A8C)],
    [Color(0xFF4F8CFF), Color(0xFF6A4CFF)],
    [Color(0xFF2BB3A3), Color(0xFF2D6CDF)],
    [Color(0xFFF5B841), Color(0xFFE5484D)],
    [Color(0xFF8E7CFF), Color(0xFFD45BB5)],
    [Color(0xFF3DD68C), Color(0xFF1E8A8A)],
  ];
  final pair =
      pairs[name.codeUnits.fold<int>(26, (a, b) => (a * 37 + b) % 1000003) %
          pairs.length];
  return LinearGradient(
    colors: pair,
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}

String initials(String name) {
  final words = name
      .trim()
      .split(RegExp(r'\s+'))
      .where((w) => w.isNotEmpty)
      .toList();
  if (words.isEmpty) return '?';
  if (words.length == 1) return words.first.characters.first.toUpperCase();
  return (words[0].characters.first + words[1].characters.first).toUpperCase();
}

class Avatar extends StatelessWidget {
  const Avatar({
    super.key,
    required this.name,
    this.size = 52,
    this.status,
    this.dimmed = false,
  });
  final String name;
  final double size;
  final String? status;
  final bool dimmed;

  @override
  Widget build(BuildContext context) {
    final dot = size * 0.28;
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Opacity(
            opacity: dimmed ? 0.4 : 1,
            child: Container(
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: avatarGradient(name),
              ),
              alignment: Alignment.center,
              child: Text(
                initials(name),
                style: TextStyle(
                  color: Colors.white,
                  fontSize: size * 0.36,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.5,
                ),
              ),
            ),
          ),
          if (status != null)
            Positioned(
              right: -1,
              bottom: -1,
              child: Container(
                width: dot,
                height: dot,
                decoration: BoxDecoration(
                  color: statusColor(status!),
                  shape: BoxShape.circle,
                  border: Border.all(color: Palette.surface, width: dot * 0.22),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Header pill: whether the phone can receive calls right now.
class ReadinessBanner extends StatelessWidget {
  const ReadinessBanner({
    super.key,
    required this.ready,
    required this.checking,
  });
  final bool ready;
  final bool checking;

  @override
  Widget build(BuildContext context) {
    final color = checking
        ? Palette.textDim
        : (ready ? Palette.green : Palette.orange);
    final text = checking
        ? 'Comprobando…'
        : (ready
              ? 'Listo: Claude puede llamarte'
              : 'Falta algo para recibir llamadas');
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          PulsingDot(color: color, pulsing: ready && !checking),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              text,
              style: TextStyle(
                color: color,
                fontWeight: FontWeight.w600,
                fontSize: 13.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class PulsingDot extends StatefulWidget {
  const PulsingDot({
    super.key,
    required this.color,
    this.pulsing = true,
    this.size = 9,
  });
  final Color color;
  final bool pulsing;
  final double size;

  @override
  State<PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<PulsingDot>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.size;
    return SizedBox(
      width: s * 2,
      height: s * 2,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (_, _) {
          final t = _controller.value;
          return Stack(
            alignment: Alignment.center,
            children: [
              if (widget.pulsing)
                Container(
                  width: s + s * t,
                  height: s + s * t,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: widget.color.withValues(alpha: 0.35 * (1 - t)),
                  ),
                ),
              Container(
                width: s,
                height: s,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: widget.color,
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(4, 28, 4, 12),
    child: Text(
      text.toUpperCase(),
      style: const TextStyle(
        color: Palette.textDim,
        fontSize: 12,
        fontWeight: FontWeight.w600,
        letterSpacing: 1.4,
      ),
    ),
  );
}

class ContactCard extends StatelessWidget {
  const ContactCard({
    super.key,
    required this.name,
    required this.subtitle,
    required this.status,
    required this.statusLabel,
    required this.onCall,
  });
  final String name;
  final String subtitle;
  final String status;
  final String statusLabel;
  final VoidCallback? onCall;

  @override
  Widget build(BuildContext context) {
    final online = onCall != null;
    final color = statusColor(status);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Palette.surface,
        borderRadius: BorderRadius.circular(20),
        child: InkWell(
          borderRadius: BorderRadius.circular(20),
          onTap: onCall,
          child: Container(
            padding: const EdgeInsets.fromLTRB(14, 14, 12, 14),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
            ),
            child: Row(
              children: [
                Avatar(name: name, status: status, dimmed: !online),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        name,
                        style: TextStyle(
                          fontSize: 16.5,
                          fontWeight: FontWeight.w600,
                          color: online ? Palette.text : Palette.textDim,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        statusLabel,
                        style: TextStyle(
                          color: color,
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      if (subtitle.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(
                          subtitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Palette.textDim,
                            fontSize: 12.5,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                CallCircleButton(onPressed: onCall),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class CallCircleButton extends StatelessWidget {
  const CallCircleButton({super.key, required this.onPressed});
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null;
    return SizedBox(
      width: 46,
      height: 46,
      child: IconButton(
        onPressed: onPressed,
        style: IconButton.styleFrom(
          backgroundColor: enabled ? Palette.green : Palette.surfaceHigh,
          disabledBackgroundColor: Palette.surfaceHigh,
          foregroundColor: const Color(0xFF06140D),
          disabledForegroundColor: Palette.grey,
        ),
        icon: const Icon(Icons.call_rounded, size: 22),
      ),
    );
  }
}

class EmptyContacts extends StatelessWidget {
  const EmptyContacts({super.key});

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 20),
    decoration: BoxDecoration(
      color: Palette.surface,
      borderRadius: BorderRadius.circular(20),
      border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
    ),
    child: const Column(
      children: [
        Icon(Icons.people_outline_rounded, color: Palette.textDim, size: 32),
        SizedBox(height: 10),
        Text('Sin contactos todavía', style: TextStyle(color: Palette.textDim)),
      ],
    ),
  );
}

/// One line of the phone checklist inside the "Estado" card.
class CheckRow extends StatelessWidget {
  const CheckRow({
    super.key,
    required this.ok,
    required this.title,
    this.detail,
    this.onFix,
    this.optional = false,
    this.last = false,
  });
  final bool? ok;
  final String title;
  final String? detail;
  final VoidCallback? onFix;
  final bool optional;
  final bool last;

  @override
  Widget build(BuildContext context) {
    final Widget icon = switch (ok) {
      null => const SizedBox(
        width: 20,
        height: 20,
        child: CircularProgressIndicator(
          strokeWidth: 2,
          color: Palette.textDim,
        ),
      ),
      true => const Icon(
        Icons.check_circle_rounded,
        color: Palette.green,
        size: 22,
      ),
      false => Icon(
        optional ? Icons.info_outline_rounded : Icons.error_outline_rounded,
        color: optional ? Palette.amber : Palette.red,
        size: 22,
      ),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
      decoration: BoxDecoration(
        border: last
            ? null
            : Border(
                bottom: BorderSide(
                  color: Palette.outline.withValues(alpha: 0.6),
                ),
              ),
      ),
      child: Row(
        children: [
          SizedBox(width: 24, child: Center(child: icon)),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                if (detail != null) ...[
                  const SizedBox(height: 2),
                  Text(
                    detail!,
                    style: const TextStyle(
                      color: Palette.textDim,
                      fontSize: 12.5,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (ok == false && onFix != null)
            TextButton(
              onPressed: onFix,
              style: TextButton.styleFrom(foregroundColor: Palette.accent),
              child: const Text('Activar'),
            ),
        ],
      ),
    );
  }
}

class GroupCard extends StatelessWidget {
  const GroupCard({super.key, required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
    decoration: BoxDecoration(
      color: Palette.surface,
      borderRadius: BorderRadius.circular(20),
      border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
    ),
    clipBehavior: Clip.antiAlias,
    child: Column(children: children),
  );
}

/// Background of the call screen: dark with a soft glow behind the orb.
class CallBackground extends StatelessWidget {
  const CallBackground({super.key, required this.child, required this.glow});
  final Widget child;
  final Color glow;

  @override
  Widget build(BuildContext context) => AnimatedContainer(
    duration: const Duration(milliseconds: 600),
    decoration: BoxDecoration(
      gradient: RadialGradient(
        center: const Alignment(0, -0.1),
        radius: 1.0,
        colors: [glow.withValues(alpha: 0.22), Palette.background],
      ),
    ),
    child: child,
  );
}

/// Who is talking: rings around the avatar while the agent speaks, live bars while
/// it listens to the user, a slow arc while it thinks.
class VoiceOrb extends StatefulWidget {
  const VoiceOrb({
    super.key,
    required this.name,
    required this.state,
    this.size = 150,
  });
  final String name;
  final String state; // connecting | speaking | listening | thinking | error
  final double size;

  @override
  State<VoiceOrb> createState() => _VoiceOrbState();
}

class _VoiceOrbState extends State<VoiceOrb>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2400),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final size = widget.size;
    final box = size * 1.9;
    return SizedBox(
      width: box,
      height: box,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (_, _) => CustomPaint(
          painter: _OrbPainter(
            state: widget.state,
            t: _controller.value,
            radius: size / 2,
          ),
          child: Center(
            child: Avatar(name: widget.name, size: size),
          ),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter({required this.state, required this.t, required this.radius});
  final String state;
  final double t;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    switch (state) {
      case 'speaking':
        for (var i = 0; i < 3; i++) {
          final p = (t * 1.5 + i / 3) % 1.0;
          canvas.drawCircle(
            c,
            radius + 8 + p * radius * 0.75,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 2.5
              ..color = Palette.accent.withValues(alpha: 0.55 * (1 - p)),
          );
        }
      case 'listening':
        canvas.drawCircle(
          c,
          radius + 6,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 3
            ..color = Palette.green.withValues(alpha: 0.8),
        );
      case 'thinking':
        final rect = Rect.fromCircle(center: c, radius: radius + 10);
        canvas.drawArc(
          rect,
          t * 2 * math.pi,
          math.pi * 0.6,
          false,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 3
            ..strokeCap = StrokeCap.round
            ..color = Palette.amber.withValues(alpha: 0.9),
        );
      case 'connecting':
        final p = (math.sin(t * 2 * math.pi) + 1) / 2;
        canvas.drawCircle(
          c,
          radius + 8,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2
            ..color = Palette.textDim.withValues(alpha: 0.2 + 0.4 * p),
        );
    }
  }

  @override
  bool shouldRepaint(_OrbPainter old) => old.t != t || old.state != state;
}

/// Animated bars: the line is open and it is the user's turn to talk.
class WaveBars extends StatefulWidget {
  const WaveBars({super.key, required this.active, required this.color});
  final bool active;
  final Color color;

  @override
  State<WaveBars> createState() => _WaveBarsState();
}

class _WaveBarsState extends State<WaveBars>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 28,
    child: AnimatedBuilder(
      animation: _controller,
      builder: (_, _) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < 7; i++)
            Container(
              margin: const EdgeInsets.symmetric(horizontal: 2.5),
              width: 4,
              height: widget.active
                  ? 6 +
                        22 *
                            ((math.sin(
                                      (_controller.value * 2 * math.pi) +
                                          i * 0.9,
                                    ) +
                                    1) /
                                2)
                  : 4,
              decoration: BoxDecoration(
                color: widget.color.withValues(
                  alpha: widget.active ? 0.9 : 0.3,
                ),
                borderRadius: BorderRadius.circular(2),
              ),
            ),
        ],
      ),
    ),
  );
}

class CallActionButton extends StatelessWidget {
  const CallActionButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onPressed,
    this.color,
    this.selected = false,
    this.size = 68,
  });
  final IconData icon;
  final String label;
  final VoidCallback onPressed;
  final Color? color;
  final bool selected;
  final double size;

  @override
  Widget build(BuildContext context) {
    final background =
        color ??
        (selected ? Palette.text : Colors.white.withValues(alpha: 0.10));
    final foreground = color != null
        ? Colors.white
        : (selected ? Palette.background : Palette.text);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          width: size,
          height: size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: background,
            boxShadow: color != null
                ? [
                    BoxShadow(
                      color: color!.withValues(alpha: 0.45),
                      blurRadius: 24,
                      offset: const Offset(0, 8),
                    ),
                  ]
                : null,
          ),
          child: Material(
            type: MaterialType.transparency,
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onPressed,
              child: Icon(icon, color: foreground, size: size * 0.42),
            ),
          ),
        ),
        const SizedBox(height: 10),
        Text(
          label,
          style: const TextStyle(
            color: Palette.textDim,
            fontSize: 12.5,
            fontWeight: FontWeight.w500,
          ),
        ),
      ],
    );
  }
}

/// The whole call screen, fed by the call's state (kept apart from WebRTC so it can be previewed).
class CallLayout extends StatelessWidget {
  const CallLayout({
    super.key,
    required this.name,
    required this.elapsed,
    required this.state,
    required this.label,
    required this.hint,
    required this.muted,
    required this.speaker,
    required this.onMute,
    required this.onHangUp,
    required this.onSpeaker,
    this.lines = const [],
  });
  final String name;
  final String elapsed;
  final String state;
  final String label;
  final String hint;
  final bool muted;
  final bool speaker;
  final VoidCallback onMute;
  final VoidCallback onHangUp;
  final VoidCallback onSpeaker;
  final List<LineaLlamada> lines; // the call in text, once something has been said

  Color get _glow => switch (state) {
    'speaking' => Palette.accent,
    'listening' => Palette.green,
    'thinking' => Palette.amber,
    'error' => Palette.red,
    _ => Palette.grey,
  };

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: CallBackground(
        glow: _glow,
        child: SafeArea(
          child: Padding(
            padding: EdgeInsets.fromLTRB(24, lines.isEmpty ? 36 : 16, 24, lines.isEmpty ? 36 : 24),
            child: Column(
              children: [
                Text(
                  name,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontSize: lines.isEmpty ? 34 : 24,
                    fontWeight: FontWeight.w600,
                    letterSpacing: -0.5,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  elapsed,
                  style: const TextStyle(
                    color: Palette.textDim,
                    fontSize: 16,
                    fontFeatures: [FontFeature.tabularFigures()],
                    letterSpacing: 1,
                  ),
                ),
                if (lines.isEmpty) ...[
                  const Spacer(),
                  VoiceOrb(name: name, state: state),
                  const SizedBox(height: 12),
                  AnimatedSwitcher(
                    duration: const Duration(milliseconds: 250),
                    child: Text(
                      label,
                      key: ValueKey(label),
                      style: TextStyle(
                        color: _glow == Palette.grey ? Palette.text : _glow,
                        fontSize: 19,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  WaveBars(
                    active: state == 'listening' && !muted,
                    color: Palette.green,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    hint,
                    style: const TextStyle(
                      color: Palette.textDim,
                      fontSize: 13.5,
                    ),
                  ),
                  const Spacer(),
                ] else ...[
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      VoiceOrb(name: name, state: state, size: 52),
                      const SizedBox(width: 12),
                      AnimatedSwitcher(
                        duration: const Duration(milliseconds: 250),
                        child: Text(
                          label,
                          key: ValueKey(label),
                          style: TextStyle(
                            color: _glow == Palette.grey ? Palette.text : _glow,
                            fontSize: 17,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  Expanded(child: TextoLlamada(lineas: lines, nombre: name)),
                  const SizedBox(height: 16),
                ],
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    CallActionButton(
                      icon: muted ? Icons.mic_off_rounded : Icons.mic_rounded,
                      label: muted ? 'Activar' : 'Silenciar',
                      selected: muted,
                      onPressed: onMute,
                    ),
                    CallActionButton(
                      icon: Icons.call_end_rounded,
                      label: 'Colgar',
                      color: Palette.red,
                      size: 80,
                      onPressed: onHangUp,
                    ),
                    CallActionButton(
                      icon: speaker
                          ? Icons.volume_up_rounded
                          : Icons.phone_in_talk_rounded,
                      label: speaker ? 'Altavoz' : 'Auricular',
                      selected: speaker,
                      onPressed: onSpeaker,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Home screen body: readiness, contacts and the phone checklist.
class HomeLayout extends StatelessWidget {
  const HomeLayout({
    super.key,
    required this.ready,
    required this.checking,
    required this.contacts,
    required this.checks,
    required this.onRefresh,
    required this.avisos,
    required this.decisiones,
  });
  final Widget avisos;
  final Widget decisiones;
  final bool ready;
  final bool checking;
  final List<Widget> contacts;
  final List<Widget> checks;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
        body: SafeArea(
          child: RefreshIndicator(
            color: Palette.accent,
            backgroundColor: Palette.surfaceHigh,
            onRefresh: onRefresh,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(18, 20, 18, 32),
              children: [
                const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 4),
                  child: Text(
                    'Llamada',
                    style: TextStyle(
                      fontSize: 32,
                      fontWeight: FontWeight.w700,
                      letterSpacing: -0.6,
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                Align(
                  alignment: Alignment.centerLeft,
                  child: ReadinessBanner(ready: ready, checking: checking),
                ),
                const SectionTitle('Contactos'),
                if (contacts.isEmpty) const EmptyContacts() else ...contacts,
                const SectionTitle('Avisos'),
                avisos,
                const SectionTitle('Decisiones'),
                decisiones,
                const SectionTitle('Estado del teléfono'),
                GroupCard(children: checks),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
