// Notice inbox: keeps every notice received on the phone, to read it in full and come back to it.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'notices.dart';

/// A notice with the time it arrived and whether it was opened.
class StoredNotice {
  const StoredNotice({
    required this.notice,
    required this.received,
    this.read = false,
  });

  final Notice notice;
  final DateTime received;
  final bool read;

  StoredNotice withRead(bool value) =>
      StoredNotice(notice: notice, received: received, read: value);

  Map<String, dynamic> toJson() => {
    'id': notice.id,
    'category': notice.category,
    'title': notice.title,
    'body': notice.body,
    'urgent': notice.urgent ? '1' : '0',
    'received': received.toUtc().toIso8601String(),
    'read': read,
  };

  /// Null when the stored entry is damaged.
  static StoredNotice? fromJson(Object? json) {
    if (json is! Map) return null;
    final notice = Notice.parse({
      ...json.cast<String, dynamic>(),
      'type': 'notice',
    });
    final received = DateTime.tryParse('${json['received']}');
    if (notice == null || received == null) return null;
    return StoredNotice(
      notice: notice,
      received: received.toLocal(),
      read: json['read'] == true,
    );
  }
}

const maxNotices = 200;
const maxNoticeDays = 30;

/// Adds the notice first; one already there with the same `id` is replaced (unread again) instead of duplicated.
/// Keeps the last [maxNotices] and drops anything older than [maxNoticeDays] days. Returns newest to oldest.
List<StoredNotice> addToInbox(
  List<StoredNotice> current,
  Notice notice,
  DateTime now,
) {
  final list = [
    StoredNotice(notice: notice, received: now),
    ...current.where((n) => n.notice.id != notice.id),
  ];
  list.sort((a, b) => b.received.compareTo(a.received));
  final cutoff = now.subtract(const Duration(days: maxNoticeDays));
  return [
    for (final n
        in list.where((n) => !n.received.isBefore(cutoff)).take(maxNotices))
      n,
  ];
}

const _key = 'notice_inbox';

/// Reads the stored inbox; `reload` because the background handler writes from another isolate.
Future<List<StoredNotice>> readInbox() async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  return decodeInbox(prefs.getString(_key));
}

List<StoredNotice> decodeInbox(String? text) {
  if (text == null) return [];
  try {
    final list = jsonDecode(text);
    if (list is! List) return [];
    return [for (final e in list) ?StoredNotice.fromJson(e)];
  } catch (_) {
    return [];
  }
}

Future<void> _write(SharedPreferences prefs, List<StoredNotice> list) =>
    prefs.setString(_key, jsonEncode([for (final n in list) n.toJson()]));

/// Stores a received notice (also from the background).
Future<void> storeNotice(Notice notice) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  await _write(
    prefs,
    addToInbox(
      decodeInbox(prefs.getString(_key)),
      notice,
      DateTime.now(),
    ),
  );
}

Future<void> markRead(String id) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  final list = decodeInbox(prefs.getString(_key));
  await _write(prefs, [
    for (final n in list) n.notice.id == id ? n.withRead(true) : n,
  ]);
}
