// The call in text: what the agent says and what the user said, line by line, to reread while talking.
// Lives only in memory while the call screen is open; nothing is stored.
import 'package:flutter/material.dart';

import 'ui.dart';

@immutable
class CallLine {
  const CallLine({required this.fromAgent, required this.text});
  final bool fromAgent;
  final String text;

  @override
  bool operator ==(Object other) =>
      other is CallLine && other.fromAgent == fromAgent && other.text == text;

  @override
  int get hashCode => Object.hash(fromAgent, text);

  @override
  String toString() => '${fromAgent ? 'agent' : 'user'}: $text';
}

/// A `{type: line, who: agent|user, text}` message from the server, or null for anything else.
CallLine? lineFromMessage(Map<String, dynamic> data) {
  if (data['type'] != 'line') return null;
  final text = (data['text'] as String? ?? '').trim();
  final who = data['who'];
  if (text.isEmpty || (who != 'agent' && who != 'user')) return null;
  return CallLine(fromAgent: who == 'agent', text: text);
}

/// The conversation as a scrollable chat that follows the last line, unless the user scrolled up to reread:
/// then it stays where they are and a «New» pill takes them back to the end.
class CallTranscript extends StatefulWidget {
  const CallTranscript({super.key, required this.lines, required this.name});
  final List<CallLine> lines;
  final String name;

  @override
  State<CallTranscript> createState() => _CallTranscriptState();
}

class _CallTranscriptState extends State<CallTranscript> {
  static const _slack = 40.0; // this close to the end still counts as "at the end"
  final _scroll = ScrollController();
  bool _following = true;
  bool _hasNew = false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _toEndSoon();
  }

  @override
  void didUpdateWidget(CallTranscript old) {
    super.didUpdateWidget(old);
    if (widget.lines.length == old.lines.length) return;
    if (_following) {
      _toEndSoon();
    } else {
      setState(() => _hasNew = true);
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    final p = _scroll.position;
    final atEnd = p.maxScrollExtent - p.pixels <= _slack;
    if (atEnd != _following || (atEnd && _hasNew)) {
      setState(() {
        _following = atEnd;
        if (atEnd) _hasNew = false;
      });
    }
  }

  void _toEndSoon([int tries = 4]) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final p = _scroll.position;
      if (p.pixels >= p.maxScrollExtent) return;
      _scroll.jumpTo(p.maxScrollExtent);
      // Lines laid out for the first time can make the list longer than estimated: look again next frame.
      if (tries > 1) _toEndSoon(tries - 1);
    });
  }

  void _goToEnd() {
    setState(() {
      _following = true;
      _hasNew = false;
    });
    _scroll.jumpTo(_scroll.position.maxScrollExtent);
    _toEndSoon(); // the last lines may still be laying out
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        ShaderMask(
          shaderCallback: (rect) => const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Colors.transparent, Colors.white, Colors.white],
            stops: [0, 0.05, 1],
          ).createShader(rect),
          blendMode: BlendMode.dstIn,
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.fromLTRB(0, 14, 0, 8),
            itemCount: widget.lines.length,
            itemBuilder: (context, i) {
              final line = widget.lines[i];
              final sameAsBefore = i > 0 && widget.lines[i - 1].fromAgent == line.fromAgent;
              return _Bubble(line: line, name: widget.name, withName: !sameAsBefore);
            },
          ),
        ),
        if (_hasNew)
          Positioned(
            bottom: 8,
            left: 0,
            right: 0,
            child: Center(
              child: Material(
                color: Palette.surfaceHigh,
                shape: const StadiumBorder(side: BorderSide(color: Palette.outline)),
                child: InkWell(
                  customBorder: const StadiumBorder(),
                  onTap: _goToEnd,
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.arrow_downward_rounded, size: 16, color: Palette.text),
                        SizedBox(width: 6),
                        Text(
                          'New',
                          style: TextStyle(color: Palette.text, fontWeight: FontWeight.w600),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.line, required this.name, required this.withName});
  final CallLine line;
  final String name;
  final bool withName;

  @override
  Widget build(BuildContext context) {
    final agent = line.fromAgent;
    return Padding(
      padding: EdgeInsets.only(top: withName ? 12 : 4),
      child: Column(
        crossAxisAlignment: agent ? CrossAxisAlignment.start : CrossAxisAlignment.end,
        children: [
          if (withName)
            Padding(
              padding: const EdgeInsets.only(bottom: 4, left: 4, right: 4),
              child: Text(
                agent ? name : 'You',
                style: TextStyle(
                  color: agent ? Palette.accent : Palette.green,
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.2,
                ),
              ),
            ),
          ConstrainedBox(
            constraints: BoxConstraints(
              maxWidth: MediaQuery.sizeOf(context).width * (agent ? 0.86 : 0.78),
            ),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: agent
                    ? Palette.surfaceHigh.withValues(alpha: 0.92)
                    : Palette.green.withValues(alpha: 0.16),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(18),
                  topRight: const Radius.circular(18),
                  bottomLeft: Radius.circular(agent ? 6 : 18),
                  bottomRight: Radius.circular(agent ? 18 : 6),
                ),
              ),
              child: SelectableText(
                line.text,
                style: const TextStyle(color: Palette.text, fontSize: 17, height: 1.38),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
