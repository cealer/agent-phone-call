// Agent Call: lets your AI agent (through the server on your Mac) phone this Android and talk.
import 'dart:async';
import 'dart:convert';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_callkit_incoming/entities/entities.dart';
import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';

import 'call_screen.dart';
import 'config.dart';
import 'decisions.dart';
import 'incoming.dart';
import 'notice_inbox.dart';
import 'notices.dart';
import 'notices_screen.dart';
import 'ui.dart';

final navigatorKey = GlobalKey<NavigatorState>();
String? _openCallId;

@pragma('vm:entry-point')
Future<void> onBackgroundMessage(RemoteMessage message) async {
  await Firebase.initializeApp();
  await handlePush(message.data);
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // WebRTC's own echo canceller (AEC3) instead of the vendor's: on speakerphone the
  // vendor's mutes the mic while audio plays, so the user could not interrupt.
  await WebRTC.initialize(
    options: {'androidUseHardwareAudioProcessing': false},
  );
  await Firebase.initializeApp();
  await initNotices(onTap: (response) => _openNotice(response.payload));
  FirebaseMessaging.onBackgroundMessage(onBackgroundMessage);
  FirebaseMessaging.onMessage.listen((message) async {
    await handlePush(message.data);
    if (message.data['type'] == 'notice') noticesChanged.value++;
    if (message.data['type'] == 'tasks') tasksChanged.value++;
  });
  FlutterCallkitIncoming.onEvent.listen(_onCallEvent);
  // The «Fleet» widget reads from the server with these; the app hands them over when it opens.
  final config = widgetConfig(serverUrl, phoneToken);
  try {
    if (config != null) await _widgetChannel.invokeMethod('setConfig', config);
  } catch (_) {}
  runApp(const AgentCallApp());
  unawaited(dialFromWidget());
  unawaited(openDecisionsFromWidget());
  // Opened by tapping a notice's notification while the app was closed.
  unawaited(noticeFromLaunch().then(_openNotice));
  // Answered from the lock screen while the app was not running: open the call now.
  await openAnsweredCall();
}

/// Android keeps accepted calls around after they end; only open the one the server
/// still has waiting, and clear the rest so the app never reopens a dead call.
Future<void> openAnsweredCall() async {
  for (final call in await FlutterCallkitIncoming.activeCalls()) {
    final callId = call.extra?['call_id'];
    if (callId is! String) {
      await _endOrphan(call.id);
      continue;
    }
    if (!call.isAccepted) continue; // still ringing: the accept event will open it
    if (await isAnswerable(callId)) {
      _openCall(callId);
    } else {
      await _endOrphan(call.id);
    }
  }
}

/// A call that ended without being cleared: end it in the system (and its ongoing-call service) and give the
/// phone's audio back, in case the app was left running with it taken.
Future<void> _endOrphan(String uuid) async {
  try {
    await FlutterCallkitIncoming.endCall(uuid);
  } catch (_) {}
  try {
    await const MethodChannel('phonecall/audio').invokeMethod<bool>('release', {'callId': uuid});
  } catch (_) {}
}

void _onCallEvent(CallEvent? event) {
  switch (event) {
    case CallEventActionCallAccept(:final callKitParams):
      final callId = callKitParams.extra?['call_id'];
      if (callId is String) {
        _openCall(callId);
      } else {
        FlutterCallkitIncoming.endCall(callKitParams.id);
      }
    case CallEventActionCallEnded():
      // Hung up from Android's ongoing-call notification.
      hangUpCurrentCall?.call();
    case CallEventActionCallDecline(:final callKitParams):
      final callId = callKitParams.extra?['call_id'];
      if (callId is String) declineOnServer(callId);
    default:
      break;
  }
}

/// Bumped whenever the inbox changes with the app open, so the home screen recounts the unread notices.
final noticesChanged = ValueNotifier<int>(0);

/// Bumped when a `tasks` snapshot arrives with the app open, so the home screen recounts the decisions.
final tasksChanged = ValueNotifier<int>(0);
String? _openNoticeId;

/// Opens the notice in full (from its notification), waiting for the navigator to exist if the app is starting.
void _openNotice(String? id) {
  if (id == null || id.isEmpty) return;
  void push() {
    final navigator = navigatorKey.currentState;
    if (navigator == null) {
      Future.delayed(const Duration(milliseconds: 100), push);
      return;
    }
    // Already on screen: do not stack the same notice again.
    if (_openNoticeId == id) {
      navigator.popUntil((r) => r.settings.name != 'notice/$id');
    }
    _openNoticeId = id;
    navigator
        .push(
          MaterialPageRoute(
            settings: RouteSettings(name: 'notice/$id'),
            builder: (_) => NoticeScreen(id: id),
          ),
        )
        .then((_) {
          if (_openNoticeId == id) _openNoticeId = null;
          noticesChanged.value++;
        });
  }

  push();
}

void _openCall(String callId) {
  if (_openCallId == callId) return;
  _openCallId = callId;
  void push() {
    final navigator = navigatorKey.currentState;
    if (navigator == null) {
      Future.delayed(const Duration(milliseconds: 100), push);
      return;
    }
    navigator.push(
      MaterialPageRoute(builder: (_) => CallScreen(callId: callId)),
    );
  }

  push();
}

const _widgetChannel = MethodChannel('phonecall/widget');

/// The «Talk» widget opens the app with the contact to call: checked on launch and when coming back to the foreground.
Future<void> dialFromWidget() async {
  try {
    final id = await _widgetChannel.invokeMethod<String>('takeDial');
    if (id == null) return;
    for (var i = 0; i < 50 && navigatorKey.currentContext == null; i++) {
      await Future.delayed(const Duration(milliseconds: 100));
    }
    final context = navigatorKey.currentContext;
    if (context == null || !context.mounted) return;
    final contacts = await fetchContacts();
    final contact = contacts.where((c) => c.id == id).firstOrNull;
    if (contact != null && context.mounted) await dial(context, contact);
  } catch (_) {}
}

/// Tapping the «Decisions» widget's title opens the app on that section, and tapping a decision also opens its
/// detail: checked on launch and when coming back to the foreground.
Future<void> openDecisionsFromWidget() async {
  try {
    final request = await _widgetChannel.invokeMethod<String>(
      'takeOpenDecisions',
    );
    if (request == null) return;
    for (var i = 0; i < 50 && navigatorKey.currentState == null; i++) {
      await Future.delayed(const Duration(milliseconds: 100));
    }
    final navigator = navigatorKey.currentState;
    if (navigator == null) return;
    // Whatever was already open (list or detail) closes: they do not stack.
    navigator.popUntil((r) => r.isFirst);
    await navigator.push(
      MaterialPageRoute(
        settings: const RouteSettings(name: 'decisions'),
        builder: (_) =>
            DecisionsScreen(openDecision: request.isEmpty ? null : request),
      ),
    );
    tasksChanged.value++;
  } catch (_) {}
}

Future<void> dial(BuildContext context, Contact contact) async {
  final messenger = ScaffoldMessenger.of(context);
  final navigator = Navigator.of(context);
  try {
    final response = await http
        .post(
          serverUri('/dial'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'contact': contact.id}),
        )
        .timeout(const Duration(seconds: 10));
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode != 200) {
      final reason = switch (body['error']) {
        'offline' => '${contact.name} is not connected in Herdr.',
        'busy' => 'A call is already in progress.',
        _ => 'Could not call (${body['error']}).',
      };
      messenger.showSnackBar(SnackBar(content: Text(reason)));
      return;
    }
    navigator.push(
      MaterialPageRoute(
        builder: (_) => CallScreen(
          callId: body['call_id'] as String,
          name: contact.name,
          incoming: false,
        ),
      ),
    );
  } catch (_) {
    messenger.showSnackBar(
      const SnackBar(content: Text('No connection to your Mac.')),
    );
  }
}

class Contact {
  Contact(this.id, this.name, this.subtitle, this.status);
  final String id;
  final String name;
  final String subtitle;
  final String status;

  bool get online => status != 'offline';

  String get statusLabel => switch (status) {
    'offline' => 'Offline',
    'working' => 'Working',
    'blocked' => 'Waiting for approval',
    _ => 'Available',
  };
}

Future<List<Contact>> fetchContacts() async {
  final response = await http
      .get(serverUri('/contacts'))
      .timeout(const Duration(seconds: 8));
  final list =
      (jsonDecode(response.body) as Map<String, dynamic>)['contacts'] as List;
  return [
    for (final c in list.cast<Map<String, dynamic>>())
      Contact(
        c['id'] as String,
        c['name'] as String,
        c['subtitle'] as String? ?? '',
        c['status'] as String,
      ),
  ];
}

class AgentCallApp extends StatelessWidget {
  const AgentCallApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Agent Call',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: appTheme(),
      themeMode: ThemeMode.dark,
      home: const HomeScreen(),
    );
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  bool? _serverOk;
  bool? _registered;
  bool _notifications = false;
  bool _microphone = false;
  bool _fullScreen = false;
  bool _battery = false;
  List<Contact>? _contacts;
  int _unread = 0;
  int _pending = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    noticesChanged.addListener(_countUnread);
    tasksChanged.addListener(_countPending);
    FirebaseMessaging.instance.onTokenRefresh.listen(_register);
    _setUp();
  }

  @override
  void dispose() {
    noticesChanged.removeListener(_countUnread);
    tasksChanged.removeListener(_countPending);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _refresh();
      unawaited(dialFromWidget());
      unawaited(openDecisionsFromWidget());
    }
  }

  Future<void> _countUnread() async {
    final notices = await readInbox();
    if (mounted) {
      setState(() => _unread = notices.where((n) => !n.read).length);
    }
  }

  Future<void> _countPending() async {
    final tasks = await readStoredTasks();
    if (mounted) setState(() => _pending = tasks?.waiting.length ?? 0);
  }

  Future<void> _openDecisions() async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        settings: const RouteSettings(name: 'decisions'),
        builder: (_) => const DecisionsScreen(),
      ),
    );
    _countPending();
  }

  Future<void> _openNotices() async {
    await Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => const NoticesScreen()));
    _countUnread();
  }

  Future<void> _setUp() async {
    await FirebaseMessaging.instance.requestPermission();
    await Permission.microphone.request();
    await FlutterCallkitIncoming.requestNotificationPermission({
      'title': 'Notifications',
      'rationaleMessagePermission':
          'Needed so your agent\'s calls can ring.',
      'postNotificationMessageRequired':
          'Turn on notifications in Settings to receive calls.',
    });
    await requestNoticePermission(); // already granted by the steps above unless it was denied
    await _refresh();
  }

  Future<void> _refresh() async {
    _notifications = await Permission.notification.isGranted;
    _microphone = await Permission.microphone.isGranted;
    _fullScreen = await FlutterCallkitIncoming.canUseFullScreenIntent();
    _battery = await Permission.ignoreBatteryOptimizations.isGranted;
    await _countUnread();
    await _countPending();
    try {
      final response = await http
          .get(serverUri('/ping'))
          .timeout(const Duration(seconds: 5));
      _serverOk = response.statusCode == 200;
    } catch (_) {
      _serverOk = false;
    }
    if (_serverOk == true) {
      try {
        _contacts = await fetchContacts();
      } catch (_) {
        _contacts = null;
      }
    }
    final token = await FirebaseMessaging.instance.getToken();
    if (token != null && _serverOk == true) await _register(token);
    if (mounted) setState(() {});
  }

  Future<void> _register(String token) async {
    _registered = await registerWithServer(token);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final ready =
        _serverOk == true &&
        _registered == true &&
        _notifications &&
        _microphone &&
        _fullScreen;
    return HomeLayout(
      ready: ready,
      checking: _serverOk == null,
      onRefresh: _refresh,
      notices: NoticesEntry(unread: _unread, onTap: _openNotices),
      decisions: DecisionsEntry(
        pending: _pending,
        onTap: _openDecisions,
      ),
      contacts: [
        for (final contact in _contacts ?? <Contact>[])
          ContactCard(
            name: contact.name,
            subtitle: contact.subtitle,
            status: contact.status,
            statusLabel: contact.statusLabel,
            onCall: contact.online ? () => dial(context, contact) : null,
          ),
      ],
      checks: [
        CheckRow(
          ok: _serverOk,
          title: 'Connection to your Mac',
          detail: _serverOk == false
              ? 'Check that Tailscale is connected on the phone and on the Mac.'
              : serverUrl,
        ),
        CheckRow(ok: _registered, title: 'Phone registered'),
        CheckRow(
          ok: _notifications,
          title: 'Notifications',
          onFix: () => openAppSettings(),
        ),
        CheckRow(
          ok: _microphone,
          title: 'Microphone',
          onFix: () async {
            if (await Permission.microphone.request().isPermanentlyDenied) {
              openAppSettings();
            }
            _refresh();
          },
        ),
        CheckRow(
          ok: _fullScreen,
          title: 'Full-screen calls',
          detail: 'So it rings like a phone call on the lock screen.',
          onFix: () => FlutterCallkitIncoming.requestFullIntentPermission(),
        ),
        CheckRow(
          ok: _battery,
          title: 'No battery restriction',
          detail: 'Recommended: keeps the system from delaying calls.',
          optional: true,
          last: true,
          onFix: () async {
            await Permission.ignoreBatteryOptimizations.request();
            _refresh();
          },
        ),
      ],
    );
  }
}
