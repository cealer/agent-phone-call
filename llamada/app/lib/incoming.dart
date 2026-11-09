// Turning Firebase pushes into a full-screen incoming call, and back.
import 'dart:convert';

import 'package:flutter_callkit_incoming/entities/entities.dart';
import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

import 'avisos.dart';
import 'config.dart';
import 'tareas.dart';

/// The call UI wants a UUID; the server's call id maps to one deterministically, so a
/// later "cancel_call" push finds the same ringing call.
String uuidFor(String callId) =>
    const Uuid().v5(Namespace.url.value, 'llamada:$callId');

Future<void> handlePush(Map<String, dynamic> data) async {
  if (data['type'] == 'aviso') {
    await mostrarAviso(data);
    return;
  }
  if (data['type'] == 'tareas') {
    await guardarTareas(data);
    return;
  }
  final callId = data['call_id'] as String?;
  if (callId == null) return;
  switch (data['type']) {
    case 'incoming_call':
      await showIncoming(
        callId,
        data['caller'] as String? ?? 'Claude',
        data['reason'] as String? ?? '',
      );
    case 'cancel_call':
      await FlutterCallkitIncoming.endCall(uuidFor(callId));
  }
}

Future<void> showIncoming(String callId, String caller, String reason) async {
  final params = CallKitParams(
    id: uuidFor(callId),
    nameCaller: caller,
    appName: 'Llamada',
    handle: reason,
    type: 0,
    duration: 45000,
    extra: {'call_id': callId},
    missedCallNotification: const NotificationParams(
      showNotification: true,
      isShowCallback: false,
      subtitle: 'Llamada perdida de Claude',
    ),
    android: const AndroidParams(
      textAccept: 'Contestar',
      textDecline: 'Rechazar',
      isCustomNotification: true,
      isShowLogo: false,
      ringtonePath: 'system_ringtone_default',
      backgroundColor: '#0D0E12',
      actionColor: '#3DD68C',
      textColor: '#ffffff',
      incomingCallNotificationChannelName: 'Llamadas de Claude',
      missedCallNotificationChannelName: 'Llamadas perdidas',
      isShowFullLockedScreen: true,
    ),
  );
  await FlutterCallkitIncoming.showCallkitIncoming(params);
}

Future<void> declineOnServer(String callId) async {
  try {
    await http
        .post(
          serverUri('/decline'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'call_id': callId}),
        )
        .timeout(const Duration(seconds: 5));
  } catch (_) {
    // The server falls back to "no answer" when it rings out.
  }
}

Future<bool> isAnswerable(String callId) async {
  try {
    final response = await http
        .get(serverUri('/call', {'call_id': callId}))
        .timeout(const Duration(seconds: 5));
    return response.statusCode == 200 &&
        (jsonDecode(response.body) as Map)['answerable'] == true;
  } catch (_) {
    return false;
  }
}

Future<bool> registerWithServer(String fcmToken) async {
  try {
    final response = await http
        .post(
          serverUri('/register'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'fcm_token': fcmToken}),
        )
        .timeout(const Duration(seconds: 8));
    return response.statusCode == 200;
  } catch (_) {
    return false;
  }
}
