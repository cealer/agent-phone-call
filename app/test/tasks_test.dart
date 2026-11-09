import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/tasks.dart';

final now = DateTime.utc(2026, 10, 21, 12);

Map<String, dynamic> message([
  Object? tasks,
  String? generated = '2026-10-21T11:30:00Z',
]) => {
  'type': 'tasks',
  'generated': ?generated,
  'tasks': tasks is String ? tasks : jsonEncode(tasks),
};

void main() {
  test('reads the three lists and the snapshot time', () {
    final t = Tasks.parse(
      message({
        'running': ['a', 'b'],
        'waiting': ['c'],
        'queued': ['d', 'e', 'f'],
      }),
      now,
    )!;
    expect(t.running, ['a', 'b']);
    expect(t.waiting.map((d) => d.title), ['c']);
    expect(t.queued, ['d', 'e', 'f']);
    expect(t.generated, DateTime.utc(2026, 10, 21, 11, 30));
  });

  test('invalid JSON, or JSON that is not an object, is not a snapshot', () {
    expect(Tasks.parse(message('{not json'), now), isNull);
    expect(Tasks.parse(message('[1,2]'), now), isNull);
    expect(Tasks.parse(message('"text"'), now), isNull);
    expect(Tasks.parse({'type': 'tasks', 'generated': 'x'}, now), isNull);
  });

  test('missing or malformed lists are empty', () {
    final t = Tasks.parse(
      message({
        'running': 'not a list',
        'queued': [1, null, '  ', ' ok '],
      }),
      now,
    )!;
    expect(t.running, isEmpty);
    expect(t.waiting, isEmpty);
    expect(t.queued, ['ok']);
    expect(Tasks.parse(message(<String, Object>{}), now)!.queued, isEmpty);
  });

  test('without a valid time the arrival time is used', () {
    expect(Tasks.parse(message({}, null), now)!.generated, now);
    expect(Tasks.parse(message({}, 'yesterday'), now)!.generated, now);
  });

  test('other message types are ignored', () {
    expect(Tasks.parse({'type': 'notice', 'tasks': '{}'}, now), isNull);
    expect(Tasks.parse({}, now), isNull);
  });

  test('expires after 2 hours', () {
    final t = Tasks.parse(message({}, '2026-10-21T10:00:00Z'), now)!;
    expect(t.expired(now), isFalse); // exactly 2 h: still good
    expect(t.expired(now.add(const Duration(minutes: 1))), isTrue);
  });

  test('the JSON for the widgets keeps everything', () {
    final t = Tasks.parse(
      message({
        'running': ['a'],
        'waiting': ['b'],
        'queued': ['c'],
      }),
      now,
    )!;
    final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
    expect(j['generated'], '2026-10-21T11:30:00.000Z');
    expect(
      [j['running'], j['waiting'], j['queued']],
      [
        ['a'],
        ['b'],
        ['c'],
      ],
    );
  });

  group('decisions with an id', () {
    Decision single(Object? item) => Tasks.parse(
      message({
        'waiting': [item],
      }),
      now,
    )!.waiting.single;

    test('a string is only a title, with no id and no buttons', () {
      final d = single('  Approve the deploy  ');
      expect(d.title, 'Approve the deploy');
      expect(d.id, isNull);
      expect(d.answerable, isFalse);
      expect(d.options, isEmpty);
    });

    test('an object with an id can be answered', () {
      final d = single({
        'id': 'd7',
        'title': 'Ship to production',
        'options': ['Ship', 'Wait'],
      });
      expect(d.id, 'd7');
      expect(d.title, 'Ship to production');
      expect(d.options, ['Ship', 'Wait']);
      expect(d.answerable, isTrue);
    });

    test('without options (or empty or malformed ones) they are Yes and No', () {
      expect(single({'id': 'a', 'title': 't'}).options, ['Yes', 'No']);
      expect(single({'id': 'a', 'title': 't', 'options': []}).options, [
        'Yes',
        'No',
      ]);
      expect(single({'id': 'a', 'title': 't', 'options': 'Yes'}).options, [
        'Yes',
        'No',
      ]);
      expect(
        single({
          'id': 'a',
          'title': 't',
          'options': [1, ' '],
        }).options,
        ['Yes', 'No'],
      );
    });

    test('more than 3 options are cut to the first 3', () {
      final d = single({
        'id': 'a',
        'title': 't',
        'options': ['one', 'two', 'three', 'four', 'five'],
      });
      expect(d.options, ['one', 'two', 'three']);
    });

    test('an object without an id shows with no buttons; without a title it is dropped', () {
      final noId = single({
        'title': 'text only',
        'options': ['Yes'],
      });
      expect(noId.answerable, isFalse);
      expect(noId.title, 'text only');
      expect(
        Tasks.parse(
          message({
            'waiting': [
              {'id': 'a'},
              {'id': 'b', 'title': ' '},
              5,
            ],
          }),
          now,
        )!.waiting,
        isEmpty,
      );
    });

    test('a mix of strings and objects keeps the order', () {
      final t = Tasks.parse(
        message({
          'waiting': [
            'a',
            {'id': 'x', 'title': 'b'},
          ],
        }),
        now,
      )!;
      expect(t.waiting.map((d) => d.title), ['a', 'b']);
      expect(t.waiting.map((d) => d.id), [null, 'x']);
    });

    test(
      'the JSON for the widgets: a string, or an object with its options already resolved',
      () {
        final t = Tasks.parse(
          message({
            'waiting': [
              'a',
              {'id': 'x', 'title': 'b'},
            ],
          }),
          now,
        )!;
        final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
        expect(j['waiting'], [
          'a',
          {
            'id': 'x',
            'title': 'b',
            'options': ['Yes', 'No'],
          },
        ]);
      },
    );
  });

  group('optional detail', () {
    Decision single(Object? item) => Tasks.parse(
      message({
        'waiting': [item],
      }),
      now,
    )!.waiting.single;

    test('with a detail, the full text is the detail', () {
      final d = single({
        'id': 'd1',
        'title': 'Ship to production',
        'detail': '  Ship version 2.3 to production (database migration included)  ',
      });
      expect(d.title, 'Ship to production');
      expect(d.detail, 'Ship version 2.3 to production (database migration included)');
      expect(d.text, 'Ship version 2.3 to production (database migration included)');
    });

    test('without a detail (missing, blank or not text) the full text is the title', () {
      for (final detail in [null, '  ', 5]) {
        final d = single({'id': 'd1', 'title': 'Ship', 'detail': ?detail});
        expect(d.detail, isNull);
        expect(d.text, 'Ship');
      }
      expect(single('Text only').text, 'Text only');
    });

    test('a decision without an id also keeps its detail', () {
      final d = single({'title': 'Notice', 'detail': 'A long notice'});
      expect(d.answerable, isFalse);
      expect(d.text, 'A long notice');
    });

    test('the JSON for the widgets carries the detail only when there is one', () {
      final t = Tasks.parse(
        message({
          'waiting': [
            {'id': 'x', 'title': 'b', 'detail': 'b in full'},
            {'title': 'c', 'detail': 'c in full'},
          ],
        }),
        now,
      )!;
      final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
      expect(j['waiting'], [
        {
          'id': 'x',
          'title': 'b',
          'detail': 'b in full',
          'options': ['Yes', 'No'],
        },
        {'title': 'c', 'detail': 'c in full'},
      ]);
      final stored = Tasks.fromStored(t.toJsonText())!;
      expect(stored.waiting.map((d) => d.text), ['b in full', 'c in full']);
    });
  });

  group('texts sent to /talk', () {
    test('answer', () {
      expect(
        decisionAnswerText('d7', 'Yes', 'Ship to production'),
        '[widget-decision] id=d7 answer=Yes | The user answers «Yes» to the decision: Ship to production',
      );
    });

    test('call me', () {
      expect(
        decisionCallMeText('d7', 'Ship to production'),
        '[widget-callme] id=d7 | The user asks you to call them to explain the decision: Ship to production',
      );
    });
  });
}
