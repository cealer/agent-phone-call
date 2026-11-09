// Notices from the coordinator agent (summary, meeting, email, decision, health) as Android notifications.
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'notice_inbox.dart';

/// An FCM data message `{"type": "notice", ...}`, already validated.
class Notice {
  const Notice({
    required this.id,
    required this.category,
    required this.title,
    required this.body,
    required this.urgent,
  });

  static const categories = {
    'summary',
    'meeting',
    'email',
    'decision',
    'health',
  };

  final String id;
  final String category;
  final String title;
  final String body;
  final bool urgent;

  /// Null when the message is not a notice or has no `id` or no text to show.
  /// An unknown or missing category falls back to `summary`; a missing `urgent` is `0`.
  static Notice? parse(Map<String, dynamic> data) {
    if (data['type'] != 'notice') return null;
    final id = _text(data['id']);
    final title = _text(data['title']);
    final body = _text(data['body']);
    if (id == null || (title == null && body == null)) return null;
    final category = _text(data['category']);
    return Notice(
      id: id,
      category: categories.contains(category) ? category! : 'summary',
      title: title ?? 'Agent Call',
      body: body ?? '',
      urgent: _text(data['urgent']) == '1',
    );
  }

  /// Android's numeric id: the same `id` always gives the same number, so the notification is replaced.
  int get notificationId {
    var hash = 0x811c9dc5; // 32-bit FNV-1a: stable across runs, unlike hashCode.
    for (final unit in id.codeUnits) {
      hash = ((hash ^ unit) * 0x01000193) & 0x7fffffff;
    }
    return hash;
  }

  String get channelId => urgent ? urgentChannel : normalChannel;
}

String? _text(Object? value) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty ? null : text;
}

const urgentChannel = 'urgent_notices';
const normalChannel = 'notices';

final _plugin = FlutterLocalNotificationsPlugin();
bool _ready = false;

/// Idempotent; also called from the background handler, where `main` did not run.
Future<void> initNotices({void Function(NotificationResponse)? onTap}) async {
  if (!_ready) {
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@drawable/ic_notice'),
      ),
      onDidReceiveNotificationResponse: onTap,
    );
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        urgentChannel,
        'Urgent notices',
        description: 'Notices that need attention now: meetings, emails and important decisions.',
        importance: Importance.high,
      ),
    );
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        normalChannel,
        'Notices',
        description: 'Morning summary and notices that can wait.',
        importance: Importance.defaultImportance,
      ),
    );
    _ready = true;
  }
}

/// Shows the notice, or ignores the message if it is not a notice.
Future<void> showNotice(Map<String, dynamic> data) async {
  final notice = Notice.parse(data);
  if (notice == null) return;
  await storeNotice(notice);
  await initNotices();
  await _plugin.show(
    id: notice.notificationId,
    title: notice.title,
    body: notice.body,
    notificationDetails: NotificationDetails(
      android: AndroidNotificationDetails(
        notice.channelId,
        notice.urgent ? 'Urgent notices' : 'Notices',
        importance: notice.urgent
            ? Importance.high
            : Importance.defaultImportance,
        priority: notice.urgent ? Priority.high : Priority.defaultPriority,
        category: notice.urgent ? AndroidNotificationCategory.reminder : null,
        styleInformation: BigTextStyleInformation(notice.body),
        tag: 'notice',
      ),
    ),
    payload: notice.id, // tapping the notification opens this notice in the app
  );
}

/// Asks for Android 13+'s notification permission (does nothing if already granted).
Future<void> requestNoticePermission() async {
  await initNotices();
  await _plugin
      .resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >()
      ?.requestNotificationsPermission();
}

/// The id of the notice whose notification launched the app from closed, or null if it was opened another way.
Future<String?> noticeFromLaunch() async {
  await initNotices();
  final details = await _plugin.getNotificationAppLaunchDetails();
  return details?.didNotificationLaunchApp == true
      ? details?.notificationResponse?.payload
      : null;
}
