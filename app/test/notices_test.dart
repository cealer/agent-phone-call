import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/notices.dart';

Map<String, dynamic> notice([Map<String, dynamic> changes = const {}]) => {
  'type': 'notice',
  'id': 'meeting-42',
  'category': 'meeting',
  'title': 'Meeting in 10 minutes',
  'body': 'Daily stand-up with the team',
  'urgent': '1',
  ...changes,
};

void main() {
  test('reads a complete notice', () {
    final n = Notice.parse(notice())!;
    expect(n.id, 'meeting-42');
    expect(n.category, 'meeting');
    expect(n.title, 'Meeting in 10 minutes');
    expect(n.body, 'Daily stand-up with the team');
    expect(n.urgent, isTrue);
    expect(n.channelId, urgentChannel);
  });

  test('urgent missing or other than 1 is normal', () {
    final without = Notice.parse(notice()..remove('urgent'))!;
    expect(without.urgent, isFalse);
    expect(without.channelId, normalChannel);
    expect(Notice.parse(notice({'urgent': '0'}))!.urgent, isFalse);
  });

  test('an unknown or missing category falls back to summary', () {
    expect(Notice.parse(notice({'category': 'other'}))!.category, 'summary');
    expect(Notice.parse(notice()..remove('category'))!.category, 'summary');
  });

  test('without an id or without text it is not a notice', () {
    expect(Notice.parse(notice()..remove('id')), isNull);
    expect(Notice.parse(notice({'id': ' '})), isNull);
    expect(
      Notice.parse(
        notice()
          ..remove('title')
          ..remove('body'),
      ),
      isNull,
    );
  });

  test('with only one of title or body it is still shown', () {
    final onlyBody = Notice.parse(notice()..remove('title'))!;
    expect(onlyBody.title, isNotEmpty);
    expect(Notice.parse(notice()..remove('body'))!.body, '');
  });

  test('other message types are ignored', () {
    expect(Notice.parse({'type': 'incoming_call', 'call_id': 'x'}), isNull);
    expect(Notice.parse({}), isNull);
  });

  test('the same id always gives the same notification number', () {
    final n = Notice.parse(notice())!;
    expect(
      Notice.parse(notice({'title': 'other'}))!.notificationId,
      n.notificationId,
    );
    expect(
      Notice.parse(notice({'id': 'other'}))!.notificationId,
      isNot(n.notificationId),
    );
    expect(n.notificationId, inInclusiveRange(0, 0x7fffffff));
  });
}
