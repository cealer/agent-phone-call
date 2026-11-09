import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/decisions.dart';
import 'package:agent_phone_call/tasks.dart';
import 'package:agent_phone_call/ui.dart';

final now = DateTime.utc(2026, 10, 21, 12);

Tasks snapshot(List<Object> waiting, [String generated = '2026-10-21T11:30:00Z']) =>
    Tasks.parse({
      'type': 'tasks',
      'generated': generated,
      'tasks': jsonEncode({'waiting': waiting}),
    }, now)!;

class FakeNative implements DecisionsNative {
  FakeNative({this.state = const DecisionStates(), this.failure});
  DecisionStates state;
  String? failure;
  final calls = <String>[];

  @override
  Future<DecisionStates> states() async => state;

  @override
  Future<String?> answer(String id, String option, String title) async {
    calls.add('answer $id $option | $title');
    if (failure == null) {
      state = DecisionStates(answered: {id: option});
    } else {
      state = DecisionStates(errors: {id: failure!});
    }
    return failure;
  }

  @override
  Future<String?> callMe(String id, String title) async {
    calls.add('callMe $id | $title');
    state = DecisionStates(callMes: {id});
    return null;
  }
}

Future<void> open(
  WidgetTester tester,
  FakeNative native,
  Tasks? t, {
  String? openDecision,
}) => tester.pumpWidget(
  MaterialApp(
    key: UniqueKey(), // a fresh navigator: it does not inherit a detail opened in another test
    theme: appTheme(),
    home: DecisionsScreen(
      key: UniqueKey(),
      native: native,
      load: () async => t,
      now: () => now,
      openDecision: openDecision,
    ),
  ),
);

void main() {
  group('Tasks.fromStored', () {
    test('round trip of what toJsonText stores', () {
      final t = snapshot([
        'text only',
        {
          'id': 'd1',
          'title': 'Ship',
          'options': ['Ship', 'Wait'],
        },
      ]);
      final stored = Tasks.fromStored(t.toJsonText())!;
      expect(stored.generated, DateTime.utc(2026, 10, 21, 11, 30));
      expect(stored.waiting.map((d) => d.id), [null, 'd1']);
      expect(stored.waiting.last.options, ['Ship', 'Wait']);
    });

    test('missing or damaged text is null', () {
      expect(Tasks.fromStored(null), isNull);
      expect(Tasks.fromStored('not json'), isNull);
      expect(Tasks.fromStored('[1]'), isNull);
    });
  });

  group('DecisionStates', () {
    test('reads what the native code stores and resolves each decision', () {
      final s = DecisionStates.fromJson({
        'answered': {'a': 'Yes'},
        'errors': {'b': 'No connection to your Mac.'},
        'callMes': ['c'],
      });
      expect(s.of('a').phase, DecisionPhase.answered);
      expect(s.of('a').text, 'Answered: Yes');
      expect(s.of('b').phase, DecisionPhase.error);
      expect(s.of('b').text, 'Not sent: No connection to your Mac. Tap to retry.');
      expect(s.of('c').phase, DecisionPhase.callMe);
      expect(s.of('c').text, 'Requested: you will get a call');
      expect(s.of('z').phase, DecisionPhase.pending);
      expect(s.of('z').text, isNull);
    });

    test('answered wins over an earlier error; damaged data does not break', () {
      final s = DecisionStates.fromJson({
        'answered': {'a': 'Yes'},
        'errors': {'a': 'x'},
      });
      expect(s.of('a').phase, DecisionPhase.answered);
      expect(DecisionStates.fromJson(null).of('a').phase, DecisionPhase.pending);
      expect(DecisionStates.fromJson('x').of('a').phase, DecisionPhase.pending);
    });
  });

  test('snapshot label: the time, and its age once it expires', () {
    final g = DateTime(2026, 10, 21, 11, 30);
    expect(snapshotLabel(g, g.add(const Duration(minutes: 90))), '11:30');
    expect(snapshotLabel(g, g.add(const Duration(hours: 2))), '11:30');
    expect(snapshotLabel(g, g.add(const Duration(hours: 3, minutes: 5))), '11:30 · 3 h ago');
    expect(snapshotLabel(g, g.add(const Duration(hours: 50))), '11:30 · 2 d ago');
  });

  group('Decisions screen', () {
    final waiting = [
      'Text only',
      {
        'id': 'd1',
        'title': 'Ship to production',
        'options': ['Ship', 'Wait'],
      },
      {'id': 'd2', 'title': 'Delete branch'},
      {'id': 'd3', 'title': 'Fourth decision'},
    ];

    testWidgets('lists ALL the decisions with their options and the time', (tester) async {
      await open(tester, FakeNative(), snapshot(waiting.cast<Object>()));
      await tester.pumpAndSettle();
      for (final t in ['Text only', 'Ship to production', 'Delete branch', 'Fourth decision']) {
        expect(find.text(t), findsOneWidget);
      }
      expect(find.text('Ship'), findsOneWidget);
      expect(find.text('Wait'), findsOneWidget);
      expect(find.text('Yes'), findsNWidgets(2));
      expect(find.text('Call me'), findsNWidgets(3)); // the text-only one cannot be answered
      final h = DateTime.utc(2026, 10, 21, 11, 30).toLocal();
      final time = '${h.hour.toString().padLeft(2, '0')}:${h.minute.toString().padLeft(2, '0')}';
      expect(find.textContaining('Main · $time'), findsOneWidget);
    });

    testWidgets('with no snapshot or no decisions it says so', (tester) async {
      await open(tester, FakeNative(), null);
      await tester.pumpAndSettle();
      expect(find.textContaining('No snapshot'), findsOneWidget);
      await open(tester, FakeNative(), snapshot([]));
      await tester.pumpAndSettle();
      expect(find.text('Nothing is waiting for your decision.'), findsOneWidget);
    });

    testWidgets('answering asks for confirmation and only sends once confirmed', (tester) async {
      final native = FakeNative();
      await open(tester, native, snapshot(waiting.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ship'));
      await tester.pumpAndSettle();
      expect(find.text('Answer "Ship" to Ship to production?'), findsOneWidget);
      expect(native.calls, isEmpty);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(native.calls, isEmpty);
      await tester.tap(find.text('Ship'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(native.calls, ['answer d1 Ship | Ship to production']);
      expect(find.text('Answered: Ship'), findsOneWidget);
      expect(find.text('Wait'), findsNothing); // answered: no buttons
    });

    testWidgets('when sending fails the reason shows and the buttons stay', (tester) async {
      final native = FakeNative(failure: 'No connection to your Mac.');
      await open(tester, native, snapshot(waiting.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ship'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Not sent: No connection to your Mac.'), findsOneWidget);
      expect(find.text('Wait'), findsOneWidget);
    });

    testWidgets('«Call me» sends with no confirmation and marks the request', (tester) async {
      final native = FakeNative();
      await open(tester, native, snapshot(waiting.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Call me').first);
      await tester.pumpAndSettle();
      expect(native.calls, ['callMe d1 | Ship to production']);
      expect(find.text('Requested: you will get a call'), findsOneWidget);
    });

    final long =
        'Merge the branch feature/decisions into main now that the tests passed, '
        'or wait until the phone snapshot has been reviewed before installing the new version';

    testWidgets('a long title shows in full, not cut', (tester) async {
      await open(tester, FakeNative(), snapshot([
        {'id': 'd9', 'title': long},
      ]));
      await tester.pumpAndSettle();
      final text = tester.widget<Text>(find.text(long));
      expect(text.maxLines, isNull);
      expect(text.overflow, isNull);
    });

    testWidgets('with a detail, the detail shows and is what is sent, with the id', (tester) async {
      final native = FakeNative();
      final t = snapshot([
        {'id': 'd9', 'title': 'Merge the branch…', 'detail': long, 'options': ['Merge']},
      ]);
      await open(tester, native, t);
      await tester.pumpAndSettle();
      expect(find.text(long), findsOneWidget);
      expect(find.text('Merge the branch…'), findsNothing);
      await tester.tap(find.text('Call me'));
      await tester.pumpAndSettle();
      expect(native.calls, ['callMe d9 | $long']);

      final other = FakeNative();
      await open(tester, other, t);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Merge'));
      await tester.pumpAndSettle();
      expect(find.text('Answer "Merge" to $long?'), findsOneWidget);
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(other.calls, ['answer d9 Merge | $long']);
    });

    group('detail', () {
      final withDetail = [
        {'id': 'd1', 'title': 'Ship to production', 'options': ['Ship', 'Wait']},
        {'id': 'd9', 'title': 'Merge the branch…', 'detail': long, 'options': ['Merge']},
        'Text only',
      ];

      testWidgets('tapping a decision opens its detail with the full text and its actions', (tester) async {
        final native = FakeNative();
        await open(tester, native, snapshot(withDetail.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text(long));
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetailScreen), findsOneWidget);
        final detail = find.byType(DecisionDetailScreen);
        expect(find.descendant(of: detail, matching: find.text(long)), findsOneWidget);
        expect(find.descendant(of: detail, matching: find.text('Merge')), findsOneWidget);
        await tester.tap(find.descendant(of: detail, matching: find.text('Merge')));
        await tester.pumpAndSettle();
        expect(find.text('Answer "Merge" to $long?'), findsOneWidget);
        await tester.tap(find.text('Confirm'));
        await tester.pumpAndSettle();
        expect(native.calls, ['answer d9 Merge | $long']);
        expect(find.descendant(of: detail, matching: find.text('Answered: Merge')), findsOneWidget);
        // Back on the list, it already shows the answer.
        await tester.pageBack();
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetailScreen), findsNothing);
        expect(find.text('Answered: Merge'), findsOneWidget);
      });

      testWidgets('«Call me» from the detail sends the id and the full text', (tester) async {
        final native = FakeNative();
        await open(tester, native, snapshot(withDetail.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text(long));
        await tester.pumpAndSettle();
        await tester.tap(find.descendant(of: find.byType(DecisionDetailScreen), matching: find.text('Call me')));
        await tester.pumpAndSettle();
        expect(native.calls, ['callMe d9 | $long']);
        expect(find.text('Requested: you will get a call'), findsOneWidget);
      });

      testWidgets('a decision without an id opens its detail with no actions', (tester) async {
        await open(tester, FakeNative(), snapshot(withDetail.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Text only'));
        await tester.pumpAndSettle();
        final detail = find.byType(DecisionDetailScreen);
        expect(find.descendant(of: detail, matching: find.text('Text only')), findsOneWidget);
        expect(find.descendant(of: detail, matching: find.text('Call me')), findsNothing);
      });

      testWidgets('from the widget, the requested detail opens directly (by id or by text)', (tester) async {
        await open(tester, FakeNative(), snapshot(withDetail.cast<Object>()), openDecision: 'd9');
        await tester.pumpAndSettle();
        expect(find.descendant(of: find.byType(DecisionDetailScreen), matching: find.text(long)), findsOneWidget);
        await tester.pageBack();
        await tester.pumpAndSettle();
        expect(find.text('Ship to production'), findsOneWidget); // the list is underneath

        await open(tester, FakeNative(), snapshot(withDetail.cast<Object>()), openDecision: 'Text only');
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetailScreen), findsOneWidget);

        await open(tester, FakeNative(), snapshot(withDetail.cast<Object>()), openDecision: 'no longer there');
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetailScreen), findsNothing);
        expect(find.text('Ship to production'), findsOneWidget);
      });
    });

    testWidgets('a snapshot older than 2 h warns it may be out of date', (tester) async {
      await open(tester, FakeNative(), snapshot(waiting.cast<Object>(), '2026-10-21T08:00:00Z'));
      await tester.pumpAndSettle();
      expect(find.textContaining('may be out of date'), findsOneWidget);
    });
  });
}
