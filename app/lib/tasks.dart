// Tasks of the main coordinator agent (it runs on the VPS): they arrive over FCM and are kept for the widgets.
import 'dart:convert';

import 'package:home_widget/home_widget.dart';

/// Past this age the widget warns that the snapshot is not current.
const tasksExpireAfter = Duration(hours: 2);

/// Answer options when a decision brings no `options`.
const defaultOptions = ['Yes', 'No'];

/// Most answer buttons per decision.
const maxOptions = 3;

/// One `waiting` item. With an [id] it can be answered from the widget; without one (a plain string) it is only shown.
class Decision {
  const Decision({
    this.id,
    required this.title,
    this.detail,
    this.options = const [],
  });

  final String? id;
  final String title;

  /// Optional full text; the sender may shorten [title] so the snapshot fits in FCM.
  final String? detail;
  final List<String> options;

  bool get answerable => id != null;

  /// What is shown in full and sent when answering or asking «Call me».
  String get text => detail ?? title;

  /// A string (the title) or an object `{"id", "title", "detail"?, "options"}`; null without a title.
  static Decision? parse(Object? e) {
    if (e is String) {
      return e.trim().isEmpty ? null : Decision(title: e.trim());
    }
    if (e is! Map) return null;
    final title = e['title'];
    if (title is! String || title.trim().isEmpty) return null;
    final raw = e['detail'];
    final detail = raw is String && raw.trim().isNotEmpty
        ? raw.trim()
        : null;
    final id = e['id'];
    final idText = id is String || id is num ? '$id'.trim() : '';
    if (idText.isEmpty) {
      return Decision(title: title.trim(), detail: detail);
    }
    final rawOptions = e['options'];
    final options = [
      if (rawOptions is List)
        for (final o in rawOptions)
          if (o is String && o.trim().isNotEmpty) o.trim(),
    ].take(maxOptions).toList();
    return Decision(
      id: idText,
      title: title.trim(),
      detail: detail,
      options: options.isEmpty ? defaultOptions : options,
    );
  }

  Object toJson() => id == null && detail == null
      ? title
      : {
          'id': ?id,
          'title': title,
          'detail': ?detail,
          if (id != null) 'options': options,
        };
}

/// The text the widget sends to `POST /talk` when an answer is confirmed.
String decisionAnswerText(String id, String option, String title) =>
    '[widget-decision] id=$id answer=$option | The user answers «$option» to the decision: $title';

/// The «Call me» text: asks for a call to explain the decision.
String decisionCallMeText(String id, String title) =>
    '[widget-callme] id=$id | The user asks you to call them to explain the decision: $title';

/// The snapshot in an FCM data message `{"type": "tasks", "generated": ISO 8601 UTC, "tasks": "<JSON string>"}`.
class Tasks {
  const Tasks({
    required this.generated,
    required this.running,
    required this.waiting,
    required this.queued,
  });

  final DateTime generated;
  final List<String> running;
  final List<Decision> waiting;
  final List<String> queued;

  /// Null when the message is not a tasks message or `tasks` is not a JSON object.
  /// A missing list, or one that is not a list, is empty; anything that is not text inside a list is dropped.
  /// `waiting` items may also be [Decision] objects.
  /// Without a valid `generated`, [now] (the arrival time) is used.
  static Tasks? parse(Map<String, dynamic> data, DateTime now) {
    if (data['type'] != 'tasks') return null;
    final Object? decoded;
    try {
      final raw = data['tasks'];
      decoded = raw is String ? jsonDecode(raw) : raw;
    } catch (_) {
      return null;
    }
    if (decoded is! Map) return null;
    return Tasks(
      generated:
          DateTime.tryParse('${data['generated']}')?.toUtc() ?? now.toUtc(),
      running: _titles(decoded['running']),
      waiting: _decisions(decoded['waiting']),
      queued: _titles(decoded['queued']),
    );
  }

  /// What [toJsonText] left (key `tasks` of HomeWidgetPreferences); null when missing or damaged.
  static Tasks? fromStored(String? text) {
    if (text == null) return null;
    final Object? j;
    try {
      j = jsonDecode(text);
    } catch (_) {
      return null;
    }
    if (j is! Map) return null;
    final generated = DateTime.tryParse('${j['generated']}');
    if (generated == null) return null;
    return Tasks(
      generated: generated.toUtc(),
      running: _titles(j['running']),
      waiting: _decisions(j['waiting']),
      queued: _titles(j['queued']),
    );
  }

  bool expired(DateTime now) =>
      now.toUtc().difference(generated) > tasksExpireAfter;

  /// What the widgets' native code reads (key `tasks` of HomeWidgetPreferences).
  String toJsonText() => jsonEncode({
    'generated': generated.toUtc().toIso8601String(),
    'running': running,
    'waiting': [for (final d in waiting) d.toJson()],
    'queued': queued,
  });
}

List<String> _titles(Object? value) {
  if (value is! List) return [];
  return [
    for (final e in value)
      if (e is String && e.trim().isNotEmpty) e.trim(),
  ];
}

List<Decision> _decisions(Object? value) {
  if (value is! List) return [];
  return [for (final e in value) ?Decision.parse(e)];
}

const _widgetsWithTasks = [
  'com.example.agentphonecall.FleetWidget',
  'com.example.agentphonecall.DecisionsWidget',
];

/// Stores the latest snapshot and refreshes the widgets; ignores anything that is not a tasks message.
/// Also works from the background.
Future<void> storeTasks(Map<String, dynamic> data) async {
  final tasks = Tasks.parse(data, DateTime.now());
  if (tasks == null) return;
  await HomeWidget.saveWidgetData<String>('tasks', tasks.toJsonText());
  for (final widget in _widgetsWithTasks) {
    await HomeWidget.updateWidget(qualifiedAndroidName: widget);
  }
}
