// Llamada: lets Claude (running on the user's Mac) phone this Android and talk.
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

import 'avisos.dart';
import 'avisos_screen.dart';
import 'bandeja.dart';
import 'call_screen.dart';
import 'config.dart';
import 'decisiones.dart';
import 'incoming.dart';
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
  await initAvisos(onTap: (respuesta) => _abrirAviso(respuesta.payload));
  FirebaseMessaging.onBackgroundMessage(onBackgroundMessage);
  FirebaseMessaging.onMessage.listen((message) async {
    await handlePush(message.data);
    if (message.data['type'] == 'aviso') avisosCambiaron.value++;
    if (message.data['type'] == 'tareas') tareasCambiaron.value++;
  });
  FlutterCallkitIncoming.onEvent.listen(_onCallEvent);
  // El widget «Flota» lee del servidor con estos datos; la app se los pasa al abrirse.
  final config = configParaWidget(serverUrl, phoneToken);
  try {
    if (config != null) await _widgetChannel.invokeMethod('setConfig', config);
  } catch (_) {}
  runApp(const LlamadaApp());
  unawaited(dialFromWidget());
  unawaited(abrirDecisionesDeWidget());
  // Abierta tocando la notificación de un aviso con la app cerrada.
  unawaited(avisoDeLanzamiento().then(_abrirAviso));
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
    if (!call.isAccepted)
      continue; // still ringing: the accept event will open it
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
    await const MethodChannel('llamada/audio').invokeMethod<bool>('liberar', {'callId': uuid});
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

/// Sube cada vez que cambia la bandeja con la app abierta, para que la pantalla principal recuente los no leídos.
final avisosCambiaron = ValueNotifier<int>(0);

/// Sube al llegar una instantánea `tareas` con la app abierta, para que la pantalla principal recuente las decisiones.
final tareasCambiaron = ValueNotifier<int>(0);
String? _avisoAbierto;

/// Abre el aviso a tamaño completo (desde la notificación), esperando a que el navegador exista si la app arranca.
void _abrirAviso(String? id) {
  if (id == null || id.isEmpty) return;
  void push() {
    final navigator = navigatorKey.currentState;
    if (navigator == null) {
      Future.delayed(const Duration(milliseconds: 100), push);
      return;
    }
    // Ya está a la vista: no apilar el mismo aviso otra vez.
    if (_avisoAbierto == id)
      navigator.popUntil((r) => r.settings.name != 'aviso/$id');
    _avisoAbierto = id;
    navigator
        .push(
          MaterialPageRoute(
            settings: RouteSettings(name: 'aviso/$id'),
            builder: (_) => AvisoScreen(id: id),
          ),
        )
        .then((_) {
          if (_avisoAbierto == id) _avisoAbierto = null;
          avisosCambiaron.value++;
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

const _widgetChannel = MethodChannel('llamada/widget');

/// El widget «Firstmate» abre la app con el contacto a llamar: se consulta al abrir y al volver a primer plano.
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

/// Tocar el título del widget «Decisiones» abre la app en esa sección, y tocar una decisión abre además su detalle:
/// se consulta al abrir y al volver a primer plano.
Future<void> abrirDecisionesDeWidget() async {
  try {
    final pedido = await _widgetChannel.invokeMethod<String>(
      'takeAbrirDecisiones',
    );
    if (pedido == null) return;
    for (var i = 0; i < 50 && navigatorKey.currentState == null; i++) {
      await Future.delayed(const Duration(milliseconds: 100));
    }
    final navigator = navigatorKey.currentState;
    if (navigator == null) return;
    // Lo que ya estuviera abierto (lista o detalle) se cierra: no se apilan.
    navigator.popUntil((r) => r.isFirst);
    await navigator.push(
      MaterialPageRoute(
        settings: const RouteSettings(name: 'decisiones'),
        builder: (_) =>
            DecisionesScreen(abrirDecision: pedido.isEmpty ? null : pedido),
      ),
    );
    tareasCambiaron.value++;
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
        'offline' => '${contact.name} no está conectado en herdr.',
        'busy' => 'Ya hay una llamada en curso.',
        _ => 'No se pudo llamar (${body['error']}).',
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
      const SnackBar(content: Text('No hay conexión con tu Mac.')),
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
    'offline' => 'Desconectado',
    'working' => 'Trabajando',
    'blocked' => 'Esperando aprobación',
    _ => 'Disponible',
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

class LlamadaApp extends StatelessWidget {
  const LlamadaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Llamada',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: llamadaTheme(),
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
  int _sinLeer = 0;
  int _pendientes = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    avisosCambiaron.addListener(_contarSinLeer);
    tareasCambiaron.addListener(_contarPendientes);
    FirebaseMessaging.instance.onTokenRefresh.listen(_register);
    _setUp();
  }

  @override
  void dispose() {
    avisosCambiaron.removeListener(_contarSinLeer);
    tareasCambiaron.removeListener(_contarPendientes);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _refresh();
      unawaited(dialFromWidget());
      unawaited(abrirDecisionesDeWidget());
    }
  }

  Future<void> _contarSinLeer() async {
    final avisos = await leerBandeja();
    if (mounted)
      setState(() => _sinLeer = avisos.where((a) => !a.leido).length);
  }

  Future<void> _contarPendientes() async {
    final tareas = await leerTareasGuardadas();
    if (mounted) setState(() => _pendientes = tareas?.espera.length ?? 0);
  }

  Future<void> _abrirDecisiones() async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        settings: const RouteSettings(name: 'decisiones'),
        builder: (_) => const DecisionesScreen(),
      ),
    );
    _contarPendientes();
  }

  Future<void> _abrirAvisos() async {
    await Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => const AvisosScreen()));
    _contarSinLeer();
  }

  Future<void> _setUp() async {
    await FirebaseMessaging.instance.requestPermission();
    await Permission.microphone.request();
    await FlutterCallkitIncoming.requestNotificationPermission({
      'title': 'Notificaciones',
      'rationaleMessagePermission':
          'Hacen falta para que suenen las llamadas de Claude.',
      'postNotificationMessageRequired':
          'Activa las notificaciones en Ajustes para recibir llamadas.',
    });
    await pedirPermisoAvisos(); // ya concedido por los pasos de arriba salvo que se haya negado
    await _refresh();
  }

  Future<void> _refresh() async {
    _notifications = await Permission.notification.isGranted;
    _microphone = await Permission.microphone.isGranted;
    _fullScreen = await FlutterCallkitIncoming.canUseFullScreenIntent();
    _battery = await Permission.ignoreBatteryOptimizations.isGranted;
    await _contarSinLeer();
    await _contarPendientes();
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
      avisos: AvisosEntrada(sinLeer: _sinLeer, onTap: _abrirAvisos),
      decisiones: DecisionesEntrada(
        pendientes: _pendientes,
        onTap: _abrirDecisiones,
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
          title: 'Conexión con tu Mac',
          detail: _serverOk == false
              ? 'Revisa que Tailscale esté conectado en el teléfono y en la Mac.'
              : serverUrl,
        ),
        CheckRow(ok: _registered, title: 'Teléfono registrado'),
        CheckRow(
          ok: _notifications,
          title: 'Notificaciones',
          onFix: () => openAppSettings(),
        ),
        CheckRow(
          ok: _microphone,
          title: 'Micrófono',
          onFix: () async {
            if (await Permission.microphone.request().isPermanentlyDenied)
              openAppSettings();
            _refresh();
          },
        ),
        CheckRow(
          ok: _fullScreen,
          title: 'Llamada en pantalla completa',
          detail: 'Para que suene como llamada con la pantalla bloqueada.',
          onFix: () => FlutterCallkitIncoming.requestFullIntentPermission(),
        ),
        CheckRow(
          ok: _battery,
          title: 'Sin restricción de batería',
          detail: 'Recomendado: evita que el sistema retrase las llamadas.',
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
