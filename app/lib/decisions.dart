// «Decisions» section: what waits for the user's answer, from the main agent's latest `tasks` snapshot.
// Sending and the state (answered, error, «Call me») live in the native code shared with the Decisions widget.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:home_widget/home_widget.dart';

import 'tasks.dart';
import 'ui.dart';

enum DecisionPhase { pending, answered, error, callMe }

/// Where a decision stands on this phone, and the line shown under its title.
class DecisionState {
  const DecisionState(this.phase, [this.text]);
  final DecisionPhase phase;
  final String? text;
}

/// What was answered, failed or asked with «Call me», as `DecisionStore` (Kotlin) keeps it.
class DecisionStates {
  const DecisionStates({
    this.answered = const {},
    this.errors = const {},
    this.callMes = const {},
  });

  final Map<String, String> answered;
  final Map<String, String> errors;
  final Set<String> callMes;

  static DecisionStates fromJson(Object? json) {
    if (json is! Map) return const DecisionStates();
    Map<String, String> map(Object? m) => {
      if (m is Map)
        for (final e in m.entries) '${e.key}': '${e.value}',
    };
    final calls = json['callMes'];
    return DecisionStates(
      answered: map(json['answered']),
      errors: map(json['errors']),
      callMes: {if (calls is List) for (final e in calls) '$e'},
    );
  }

  /// Same precedence as the widget: answered, then error, then «Call me».
  DecisionState of(String id) {
    final a = answered[id];
    if (a != null) {
      return DecisionState(DecisionPhase.answered, 'Answered: $a');
    }
    final e = errors[id];
    if (e != null) {
      return DecisionState(
        DecisionPhase.error,
        'Not sent: $e Tap to retry.',
      );
    }
    if (callMes.contains(id)) {
      return const DecisionState(
        DecisionPhase.callMe,
        'Requested: you will get a call',
      );
    }
    return const DecisionState(DecisionPhase.pending);
  }
}

/// «11:30», or «11:30 · 3 h ago» / «2 d ago» once the snapshot is no longer current (same rule as the widget).
String snapshotLabel(DateTime generated, DateTime now) {
  String two(int n) => n.toString().padLeft(2, '0');
  final local = generated.toLocal();
  final time = '${two(local.hour)}:${two(local.minute)}';
  final age = now.difference(generated);
  if (age <= tasksExpireAfter) return time;
  return age.inHours >= 48
      ? '$time · ${age.inDays} d ago'
      : '$time · ${age.inHours} h ago';
}

/// Sending and state of the decisions, shared with the widget (`DecisionSender` and `DecisionStore` in Kotlin).
/// `answer` and `callMe` return null when the server accepted the request, or else the reason it failed.
abstract class DecisionsNative {
  Future<DecisionStates> states();
  Future<String?> answer(String id, String option, String title);
  Future<String?> callMe(String id, String title);
}

class DecisionsChannel implements DecisionsNative {
  const DecisionsChannel();
  static const _channel = MethodChannel('phonecall/decisions');

  @override
  Future<DecisionStates> states() async {
    final text = await _channel.invokeMethod<String>('states');
    try {
      return DecisionStates.fromJson(text == null ? null : jsonDecode(text));
    } catch (_) {
      return const DecisionStates();
    }
  }

  @override
  Future<String?> answer(String id, String option, String title) =>
      _channel.invokeMethod<String>('answer', {
        'id': id,
        'option': option,
        'title': title,
      });

  @override
  Future<String?> callMe(String id, String title) => _channel.invokeMethod<String>(
    'callMe',
    {'id': id, 'title': title},
  );
}

/// The latest snapshot the FCM message left (the same one the widgets read).
Future<Tasks?> readStoredTasks() async =>
    Tasks.fromStored(await HomeWidget.getWidgetData<String>('tasks'));

/// Home screen row that leads to the Decisions section.
class DecisionsEntry extends StatelessWidget {
  const DecisionsEntry({super.key, required this.pending, required this.onTap});
  final int pending;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GroupCard(
    children: [
      InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              const Icon(Icons.help_outline_rounded, color: Palette.amber, size: 22),
              const SizedBox(width: 14),
              const Expanded(
                child: Text(
                  'Decisions',
                  style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w500),
                ),
              ),
              if (pending > 0)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 2),
                  decoration: BoxDecoration(
                    color: Palette.amber,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '$pending',
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
                      color: Colors.black,
                    ),
                  ),
                ),
              const SizedBox(width: 6),
              const Icon(Icons.chevron_right_rounded, color: Palette.textDim),
            ],
          ),
        ),
      ),
    ],
  );
}

class DecisionsScreen extends StatefulWidget {
  const DecisionsScreen({
    super.key,
    this.native = const DecisionsChannel(),
    this.load = readStoredTasks,
    this.now = DateTime.now,
    this.openDecision,
  });

  final DecisionsNative native;
  final Future<Tasks?> Function() load;
  final DateTime Function() now;

  /// The decision to open in detail once loaded (the one tapped in the widget): its id, or its text without an id.
  final String? openDecision;

  @override
  State<DecisionsScreen> createState() => _DecisionsScreenState();
}

class _DecisionsScreenState extends State<DecisionsScreen>
    with WidgetsBindingObserver, _DecisionActions {
  Tasks? _tasks;
  bool _loaded = false;
  bool _openedFromWidget = false;

  @override
  DecisionsNative get native => widget.native;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _reload();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _reload();
  }

  Future<void> _reload() async {
    Tasks? tasks;
    var fresh = const DecisionStates();
    try {
      tasks = await widget.load();
      fresh = await native.states();
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _tasks = tasks;
      states = fresh;
      _loaded = true;
    });
    final key = widget.openDecision;
    if (key == null || _openedFromWidget || tasks == null) return;
    _openedFromWidget = true;
    final d = tasks.waiting
        .where((d) => d.id == key || (d.id == null && d.text == key))
        .firstOrNull;
    if (d != null) await _openDetail(d);
  }

  Future<void> _openDetail(Decision d) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => DecisionDetailScreen(decision: d, native: native),
      ),
    );
    // What was answered or asked in the detail shows on the way back.
    final s = await native.states().catchError((_) => states);
    if (mounted) setState(() => states = s);
  }

  @override
  Widget build(BuildContext context) {
    final tasks = _tasks;
    final now = widget.now();
    final expired = tasks?.expired(now) ?? false;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Decisions'),
        backgroundColor: Palette.background,
      ),
      body: !_loaded
          ? const SizedBox.shrink()
          : tasks == null
          ? const _Message('No snapshot from the main agent has arrived yet.')
          : RefreshIndicator(
              color: Palette.accent,
              backgroundColor: Palette.surfaceHigh,
              onRefresh: _reload,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
                    child: Text(
                      'Main · ${snapshotLabel(tasks.generated, now)}'
                      '${expired ? ' · may be out of date' : ''}',
                      style: TextStyle(
                        color: expired ? Palette.amber : Palette.textDim,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                  if (tasks.waiting.isEmpty)
                    const _Message('Nothing is waiting for your decision.')
                  else
                    for (final d in tasks.waiting) ...[
                      _DecisionRow(
                        decision: d,
                        state: stateOf(d),
                        sending: sending.contains(d.id),
                        onOption: (o) => answer(d, o),
                        onCallMe: () => callMe(d),
                        onTap: () => _openDetail(d),
                      ),
                      const SizedBox(height: 10),
                    ],
                ],
              ),
            ),
    );
  }
}

/// Answering (with a confirmation) and «Call me», shared by the list and the detail.
mixin _DecisionActions<T extends StatefulWidget> on State<T> {
  DecisionsNative get native;
  DecisionStates states = const DecisionStates();
  final sending = <String>{};

  DecisionState stateOf(Decision d) => d.id == null
      ? const DecisionState(DecisionPhase.pending)
      : states.of(d.id!);

  Future<void> answer(Decision d, String option) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: Palette.surfaceHigh,
        title: const Text('Decision'),
        content: Text('Answer "$option" to ${d.text}?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _send(d, () => native.answer(d.id!, option, d.text));
  }

  Future<void> callMe(Decision d) =>
      _send(d, () => native.callMe(d.id!, d.text));

  Future<void> _send(Decision d, Future<String?> Function() request) async {
    setState(() => sending.add(d.id!));
    try {
      await request();
    } catch (_) {}
    final fresh = await native.states().catchError((_) => states);
    if (!mounted) return;
    setState(() {
      sending.remove(d.id!);
      states = fresh;
    });
  }
}

/// One decision on its own: the full text and the same actions as in the list.
class DecisionDetailScreen extends StatefulWidget {
  const DecisionDetailScreen({
    super.key,
    required this.decision,
    this.native = const DecisionsChannel(),
  });

  final Decision decision;
  final DecisionsNative native;

  @override
  State<DecisionDetailScreen> createState() => _DecisionDetailScreenState();
}

class _DecisionDetailScreenState extends State<DecisionDetailScreen>
    with _DecisionActions {
  @override
  DecisionsNative get native => widget.native;

  @override
  void initState() {
    super.initState();
    native.states().then((s) {
      if (mounted) setState(() => states = s);
    }, onError: (_) {});
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.decision;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Decision'),
        backgroundColor: Palette.background,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
        children: [
          _DecisionRow(
            decision: d,
            state: stateOf(d),
            sending: sending.contains(d.id),
            onOption: (o) => answer(d, o),
            onCallMe: () => callMe(d),
          ),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(32),
    child: Center(
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: const TextStyle(color: Palette.textDim),
      ),
    ),
  );
}

class _DecisionRow extends StatelessWidget {
  const _DecisionRow({
    required this.decision,
    required this.state,
    required this.sending,
    required this.onOption,
    required this.onCallMe,
    this.onTap,
  });

  final Decision decision;
  final DecisionState state;
  final bool sending;
  final void Function(String option) onOption;
  final VoidCallback onCallMe;

  /// In the list it opens the detail; the detail has none.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final color = switch (state.phase) {
      DecisionPhase.answered => Palette.green,
      DecisionPhase.error => Palette.red,
      _ => Palette.accent,
    };
    final buttons = decision.answerable &&
        state.phase != DecisionPhase.answered &&
        !sending;
    final card = Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Palette.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Padding(
                padding: EdgeInsets.only(top: 5),
                child: Icon(Icons.diamond, size: 10, color: Palette.amber),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  decision.text,
                  style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600),
                ),
              ),
            ],
          ),
          if (state.text != null) ...[
            const SizedBox(height: 6),
            Text(state.text!, style: TextStyle(color: color, fontSize: 13)),
          ],
          if (sending) ...[
            const SizedBox(height: 8),
            const LinearProgressIndicator(minHeight: 2),
          ],
          if (buttons) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final o in decision.options)
                  OutlinedButton(
                    onPressed: () => onOption(o),
                    child: Text(o),
                  ),
                TextButton(
                  onPressed: onCallMe,
                  child: const Text('Call me'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
    return onTap == null
        ? card
        : GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: onTap,
            child: card,
          );
  }
}
