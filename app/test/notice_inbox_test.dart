import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/notice_inbox.dart';
import 'package:agent_phone_call/notices.dart';
import 'package:agent_phone_call/notices_screen.dart';
import 'package:agent_phone_call/ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

Notice notice(
  String id, {
  String title = 'Title',
  String body = 'Body',
  String category = 'email',
}) => Notice.parse({
  'type': 'notice',
  'id': id,
  'title': title,
  'body': body,
  'category': category,
  'urgent': '1',
})!;

final now = DateTime.utc(2026, 10, 21, 12);

void main() {
  test('storing keeps it, replaces by id and marks read in the phone storage', () async {
    SharedPreferences.setMockInitialValues({});
    await storeNotice(notice('a', title: 'one'));
    await storeNotice(notice('b'));
    await storeNotice(notice('a', title: 'two'));
    var list = await readInbox();
    expect(list.map((n) => n.notice.id), unorderedEquals(['a', 'b']));
    expect(list.firstWhere((n) => n.notice.id == 'a').notice.title, 'two');
    await markRead('a');
    list = await readInbox();
    expect(list.firstWhere((n) => n.notice.id == 'a').read, isTrue);
    expect(list.firstWhere((n) => n.notice.id == 'b').read, isFalse);
  });

  test('a new notice goes first', () {
    final list = addToInbox([], notice('a'), now);
    final list2 = addToInbox(
      list,
      notice('b'),
      now.add(const Duration(minutes: 1)),
    );
    expect(list2.map((n) => n.notice.id), ['b', 'a']);
    expect(list2.first.read, isFalse);
  });

  test('the same id replaces the entry and makes it unread again', () {
    var list = addToInbox([], notice('a', title: 'old'), now);
    list = [list.single.withRead(true)];
    list = addToInbox(
      list,
      notice('a', title: 'new'),
      now.add(const Duration(hours: 1)),
    );
    expect(list, hasLength(1));
    expect(list.single.notice.title, 'new');
    expect(list.single.read, isFalse);
  });

  test('keeps only the last $maxNotices', () {
    var list = <StoredNotice>[];
    for (var i = 0; i < maxNotices + 15; i++) {
      list = addToInbox(
        list,
        notice('id$i'),
        now.add(Duration(minutes: i)),
      );
    }
    expect(list, hasLength(maxNotices));
    expect(list.first.notice.id, 'id${maxNotices + 14}');
    expect(list.any((n) => n.notice.id == 'id0'), isFalse);
  });

  test('drops anything older than $maxNoticeDays days', () {
    var list = addToInbox([], notice('old'), now);
    list = addToInbox(
      list,
      notice('new'),
      now.add(const Duration(days: maxNoticeDays + 1)),
    );
    expect(list.map((n) => n.notice.id), ['new']);
  });

  test('round trip through JSON, and damaged data is tolerated', () {
    final list = [
      addToInbox([], notice('a'), now).single.withRead(true),
    ];
    final text = jsonEncode([for (final n in list) n.toJson()]);
    final decoded = decodeInbox(text);
    expect(decoded.single.notice.id, 'a');
    expect(decoded.single.notice.urgent, isTrue);
    expect(decoded.single.read, isTrue);
    expect(decoded.single.received.toUtc(), now);
    expect(decodeInbox(null), isEmpty);
    expect(decodeInbox('not json'), isEmpty);
    expect(decodeInbox('{"a":1}'), isEmpty);
    expect(decodeInbox('[1, {"id":"x"}]'), isEmpty);
  });

  test('notice time: today, yesterday and a date', () {
    final t = DateTime(2026, 10, 21, 8, 5);
    expect(noticeTime(t, DateTime(2026, 10, 21, 20)), 'today 08:05');
    expect(noticeTime(t, DateTime(2026, 10, 22, 1)), 'yesterday 08:05');
    expect(noticeTime(t, DateTime(2026, 10, 25)), '21 Oct 08:05');
  });

  testWidgets(
    'a notice screen shows the whole body, selectable',
    (tester) async {
      final body = ('a long line ' * 40).trim();
      await tester.pumpWidget(
        MaterialApp(
          theme: appTheme(),
          home: Scaffold(
            body: NoticeDetail(
              stored: addToInbox(
                [],
                notice('a', body: body),
                now,
              ).single,
            ),
          ),
        ),
      );
      expect(find.byType(SelectableText), findsNWidgets(2)); // title and body
      expect(find.text(body), findsOneWidget);
    },
  );
}
