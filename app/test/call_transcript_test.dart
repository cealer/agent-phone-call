import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/call_transcript.dart';
import 'package:agent_phone_call/ui.dart';

List<CallLine> lines(int n) => [
  for (var i = 0; i < n; i++)
    CallLine(
      fromAgent: i.isEven,
      text: 'Line $i: a fairly long sentence so that it takes up room on the call screen.',
    ),
];

Widget screen(List<CallLine> l) => MaterialApp(
  theme: appTheme(),
  home: Scaffold(
    body: SizedBox(height: 400, child: CallTranscript(lines: l, name: 'Firstmate')),
  ),
);

// The chat's own Scrollable (each selectable line has one inside too).
Finder chat() => find.byType(Scrollable).first;

ScrollPosition position(WidgetTester tester) =>
    tester.state<ScrollableState>(chat()).position;

void main() {
  group('lineFromMessage', () {
    test('what the agent says and what the user said, told apart', () {
      expect(
        lineFromMessage({'type': 'line', 'who': 'agent', 'text': 'Done.'}),
        const CallLine(fromAgent: true, text: 'Done.'),
      );
      expect(
        lineFromMessage({'type': 'line', 'who': 'user', 'text': 'Okay, thanks.'}),
        const CallLine(fromAgent: false, text: 'Okay, thanks.'),
      );
    });

    test('ignores other messages, an unknown speaker and empty text', () {
      expect(lineFromMessage({'type': 'state', 'value': 'speaking'}), isNull);
      expect(lineFromMessage({'type': 'line', 'who': 'other', 'text': 'x'}), isNull);
      expect(lineFromMessage({'type': 'line', 'who': 'agent', 'text': '  '}), isNull);
      expect(lineFromMessage({'type': 'line', 'who': 'agent'}), isNull);
    });
  });

  group('CallTranscript', () {
    testWidgets('shows each line and who said it', (tester) async {
      await tester.pumpWidget(
        screen(const [
          CallLine(fromAgent: true, text: 'Task twelve is ready.'),
          CallLine(fromAgent: false, text: 'And seventeen?'),
        ]),
      );
      expect(find.text('Task twelve is ready.'), findsOneWidget);
      expect(find.text('And seventeen?'), findsOneWidget);
      expect(find.text('Firstmate'), findsOneWidget);
      expect(find.text('You'), findsOneWidget);
    });

    testWidgets('scrolls to the end by itself when a new line arrives', (tester) async {
      await tester.pumpWidget(screen(lines(12)));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, position(tester).maxScrollExtent);
      await tester.pumpWidget(screen(lines(13)));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, position(tester).maxScrollExtent);
      expect(find.textContaining('Line 12:'), findsOneWidget);
    });

    testWidgets('does not move when the user scrolled up to reread; a pill takes them to the end', (tester) async {
      await tester.pumpWidget(screen(lines(12)));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, 300));
      await tester.pumpAndSettle();
      final reading = position(tester).pixels;
      expect(reading, lessThan(position(tester).maxScrollExtent));
      expect(find.text('New'), findsNothing);

      await tester.pumpWidget(screen(lines(13)));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, reading);
      expect(find.text('New'), findsOneWidget);

      await tester.tap(find.text('New'));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, position(tester).maxScrollExtent);
      expect(find.text('New'), findsNothing);

      await tester.pumpWidget(screen(lines(14)));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, position(tester).maxScrollExtent);
    });

    testWidgets('scrolling back down by hand resumes following', (tester) async {
      await tester.pumpWidget(screen(lines(12)));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, 300));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, -2000));
      await tester.pumpAndSettle();
      await tester.pumpWidget(screen(lines(13)));
      await tester.pumpAndSettle();
      expect(position(tester).pixels, position(tester).maxScrollExtent);
      expect(find.text('New'), findsNothing);
    });
  });

  group('CallLayout', () {
    Widget call(List<CallLine> l) => MaterialApp(
      theme: appTheme(),
      home: Scaffold(
        body: CallLayout(
          name: 'Firstmate',
          elapsed: '1:02',
          state: 'speaking',
          label: 'Speaking',
          hint: ' ',
          muted: false,
          speaker: true,
          onMute: () {},
          onHangUp: () {},
          onSpeaker: () {},
          lines: l,
        ),
      ),
    );

    void phone(WidgetTester tester) {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.625;
      addTearDown(tester.view.reset);
    }

    testWidgets('with no lines there is no conversation text', (tester) async {
      phone(tester);
      await tester.pumpWidget(call(const []));
      expect(find.byType(CallTranscript), findsNothing);
      expect(find.text('Hang up'), findsOneWidget);
    });

    testWidgets('with lines the conversation can be read on the call screen', (tester) async {
      phone(tester);
      await tester.pumpWidget(call(lines(20)));
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.byType(CallTranscript), findsOneWidget);
      expect(find.textContaining('Line 19:'), findsOneWidget);
      expect(find.text('Hang up'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
