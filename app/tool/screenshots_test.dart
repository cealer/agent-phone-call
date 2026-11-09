// Renders the app's main screens to PNG with made-up data, for the README (no phone needed).
//
//   flutter test tool/screenshots_test.dart --update-goldens \
//     --dart-define=FONTS="$(dirname "$(dirname "$(readlink -f "$(command -v flutter)")")")/bin/cache/artifacts/material_fonts"
//
// The images land in docs/images/ at the repository root. Everything shown is fictional. Not part of `flutter test` (it lives outside
// test/): font rendering differs between machines, so these are pictures, not pixel-exact checks. The incoming-call
// screen and the home-screen widgets are drawn by Android itself (the call plugin and RemoteViews), not by
// Flutter, so they cannot be rendered here.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/call_transcript.dart';
import 'package:agent_phone_call/decisions.dart';
import 'package:agent_phone_call/notice_inbox.dart';
import 'package:agent_phone_call/notices.dart';
import 'package:agent_phone_call/notices_screen.dart';
import 'package:agent_phone_call/tasks.dart';
import 'package:agent_phone_call/ui.dart';

const _fonts = String.fromEnvironment('FONTS');
const _out = '../../docs/images';

Future<void> _loadFonts() async {
  if (_fonts.isEmpty) {
    throw StateError('pass --dart-define=FONTS=<flutter>/bin/cache/artifacts/material_fonts');
  }
  Future<ByteData> file(String name) async =>
      ByteData.sublistView(await File('$_fonts/$name').readAsBytes());
  final roboto = FontLoader('Roboto');
  for (final weight in ['Regular', 'Medium', 'Bold', 'Light', 'Black']) {
    roboto.addFont(file('Roboto-$weight.ttf'));
  }
  await roboto.load();
  final icons = FontLoader('MaterialIcons')..addFont(file('MaterialIcons-Regular.otf'));
  await icons.load();
}

void _phone(WidgetTester tester) {
  tester.view.physicalSize = const Size(822, 1782); // a 411 x 891 dp phone, at 2x
  tester.view.devicePixelRatio = 2.0;
  addTearDown(tester.view.reset);
}

Widget _app(Widget home) => MaterialApp(debugShowCheckedModeBanner: false, theme: appTheme(), home: home);

Future<void> _shoot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 400));
  await expectLater(find.byType(MaterialApp), matchesGoldenFile('$_out/$name.png'));
}

final _now = DateTime(2026, 10, 21, 9, 41);

class _Native implements DecisionsNative {
  @override
  Future<DecisionStates> states() async => const DecisionStates(answered: {'d3': 'B'});
  @override
  Future<String?> answer(String id, String option, String title) async => null;
  @override
  Future<String?> callMe(String id, String title) async => null;
}

Notice _notice(String id, String category, String title, String body, {bool urgent = false}) => Notice.parse({
  'type': 'notice',
  'id': id,
  'category': category,
  'title': title,
  'body': body,
  'urgent': urgent ? '1' : '0',
})!;

void main() {
  setUpAll(_loadFonts);

  testWidgets('home', (tester) async {
    _phone(tester);
    await tester.pumpWidget(_app(HomeLayout(
      ready: true,
      checking: false,
      onRefresh: () async {},
      notices: NoticesEntry(unread: 2, onTap: () {}),
      decisions: DecisionsEntry(pending: 3, onTap: () {}),
      contacts: [
        ContactCard(name: 'Firstmate', subtitle: 'Main coordinator (remote server)', status: 'working',
            statusLabel: 'Working', onCall: () {}),
        ContactCard(name: 'Claude Code', subtitle: 'Agent on this Mac', status: 'idle',
            statusLabel: 'Available', onCall: () {}),
        const ContactCard(name: 'Docs Writer', subtitle: 'Drafts the release notes', status: 'offline',
            statusLabel: 'Offline', onCall: null),
      ],
      checks: const [
        CheckRow(ok: true, title: 'Connection to your Mac', detail: 'http://my-mac:8771'),
        CheckRow(ok: true, title: 'Phone registered'),
        CheckRow(ok: true, title: 'Notifications'),
        CheckRow(ok: true, title: 'Microphone'),
        CheckRow(ok: true, title: 'Full-screen calls'),
        CheckRow(ok: true, title: 'No battery restriction', optional: true, last: true),
      ],
    )));
    await _shoot(tester, 'home');
  });

  Widget call(String state, String label, List<CallLine> lines, {String hint = ' '}) => _app(Scaffold(
    body: CallLayout(
      name: 'Firstmate',
      elapsed: lines.isEmpty ? '0:07' : '2:34',
      state: state,
      label: label,
      hint: hint,
      muted: false,
      speaker: true,
      onMute: () {},
      onHangUp: () {},
      onSpeaker: () {},
      lines: lines,
    ),
  ));

  testWidgets('call-speaking', (tester) async {
    _phone(tester);
    await tester.pumpWidget(call('speaking', 'Speaking', const [], hint: 'Just talk to interrupt'));
    await _shoot(tester, 'call-speaking');
  });

  testWidgets('call-subtitles', (tester) async {
    _phone(tester);
    await tester.pumpWidget(call('listening', 'Listening', const [
      CallLine(fromAgent: true, text: "Hi, it's your coordinator. The nightly test run failed on the payments "
          'module. Do you want me to retry it, or open a ticket first?'),
      CallLine(fromAgent: false, text: 'What failed exactly?'),
      CallLine(fromAgent: true, text: 'Two checkout tests time out after the dependency update. Everything else '
          'passes. I recommend pinning the old version and retrying.'),
      CallLine(fromAgent: false, text: 'Okay, pin it and retry. Call me if it fails again.'),
      CallLine(fromAgent: true, text: "Got it: pin the old version, retry, and call you only if it fails. Anything else?"),
    ]));
    await tester.pump(const Duration(milliseconds: 100));
    await _shoot(tester, 'call-subtitles');
  });

  testWidgets('decisions', (tester) async {
    _phone(tester);
    final tasks = Tasks.parse({
      'type': 'tasks',
      'generated': _now.toUtc().subtract(const Duration(minutes: 12)).toIso8601String(),
      'tasks': '{"waiting": ['
          '{"id": "d1", "title": "Ship version 2.4 of the demo app to staging?", "options": ["Ship", "Wait"]},'
          '{"id": "d2", "title": "Rename the reports table to monthly_reports (a migration is included)?"},'
          '{"id": "d3", "title": "Landing page headline: option A or option B?", "options": ["A", "B"]}'
          ']}',
    }, _now)!;
    await tester.pumpWidget(_app(DecisionsScreen(native: _Native(), load: () async => tasks, now: () => _now)));
    await tester.pump(const Duration(milliseconds: 50));
    await _shoot(tester, 'decisions');
  });

  testWidgets('notices', (tester) async {
    _phone(tester);
    final stored = [
      StoredNotice(notice: _notice('n1', 'meeting', 'Weekly sync in 15 minutes',
          'Agenda: release checklist, open bugs, next sprint.', urgent: true), received: _now),
      StoredNotice(notice: _notice('n2', 'decision', 'A decision is waiting for you',
          'Ship version 2.4 of the demo app to staging?'),
          received: _now.subtract(const Duration(minutes: 40))),
      StoredNotice(notice: _notice('n3', 'summary', 'Morning summary',
          'Overnight: 3 tasks finished, 1 waiting for review, all checks green.'),
          received: _now.subtract(const Duration(hours: 2)), read: true),
      StoredNotice(notice: _notice('n4', 'email', 'New email from the design team',
          'The updated mockups for the settings page are ready.'),
          received: _now.subtract(const Duration(days: 1)), read: true),
    ];
    await tester.pumpWidget(_app(NoticesList(notices: stored, now: _now, onOpen: (_) {}, onRefresh: () async {})));
    await _shoot(tester, 'notices');
  });
}
