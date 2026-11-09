// Avisos del firstmate (resumen, reunión, correo, decisión, salud) como notificaciones de Android.
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'bandeja.dart';

/// Un mensaje FCM de datos `{"type": "aviso", ...}` ya validado.
class Aviso {
  const Aviso({
    required this.id,
    required this.categoria,
    required this.titulo,
    required this.cuerpo,
    required this.urgente,
  });

  static const categorias = {
    'resumen',
    'reunion',
    'correo',
    'decision',
    'salud',
  };

  final String id;
  final String categoria;
  final String titulo;
  final String cuerpo;
  final bool urgente;

  /// Devuelve null si el mensaje no es un aviso o no trae `id` ni texto que mostrar.
  /// Una categoría desconocida o ausente cae en `resumen`; `urgente` ausente es `0`.
  static Aviso? parse(Map<String, dynamic> data) {
    if (data['type'] != 'aviso') return null;
    final id = _text(data['id']);
    final titulo = _text(data['titulo']);
    final cuerpo = _text(data['cuerpo']);
    if (id == null || (titulo == null && cuerpo == null)) return null;
    final categoria = _text(data['categoria']);
    return Aviso(
      id: id,
      categoria: categorias.contains(categoria) ? categoria! : 'resumen',
      titulo: titulo ?? 'Llamada',
      cuerpo: cuerpo ?? '',
      urgente: _text(data['urgente']) == '1',
    );
  }

  /// Id numérico de Android: el mismo `id` da siempre el mismo número, así la notificación se reemplaza.
  int get notificationId {
    var hash = 0x811c9dc5; // FNV-1a de 32 bits: estable entre ejecuciones, a diferencia de hashCode.
    for (final unit in id.codeUnits) {
      hash = ((hash ^ unit) * 0x01000193) & 0x7fffffff;
    }
    return hash;
  }

  String get channelId => urgente ? canalUrgente : canalNormal;
}

String? _text(Object? value) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty ? null : text;
}

const canalUrgente = 'avisos_urgentes';
const canalNormal = 'avisos';

final _plugin = FlutterLocalNotificationsPlugin();
bool _ready = false;

/// Idempotente; también se llama desde el manejador de segundo plano, donde `main` no corrió.
Future<void> initAvisos({void Function(NotificationResponse)? onTap}) async {
  if (!_ready) {
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@drawable/ic_aviso'),
      ),
      onDidReceiveNotificationResponse: onTap,
    );
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        canalUrgente,
        'Avisos urgentes',
        description: 'Avisos que piden atención ahora: reuniones, correos y decisiones importantes.',
        importance: Importance.high,
      ),
    );
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        canalNormal,
        'Avisos',
        description: 'Resumen de la mañana y avisos sin prisa.',
        importance: Importance.defaultImportance,
      ),
    );
    _ready = true;
  }
}

/// Muestra el aviso, o ignora el mensaje si no es un aviso.
Future<void> mostrarAviso(Map<String, dynamic> data) async {
  final aviso = Aviso.parse(data);
  if (aviso == null) return;
  await registrarAviso(aviso);
  await initAvisos();
  await _plugin.show(
    id: aviso.notificationId,
    title: aviso.titulo,
    body: aviso.cuerpo,
    notificationDetails: NotificationDetails(
      android: AndroidNotificationDetails(
        aviso.channelId,
        aviso.urgente ? 'Avisos urgentes' : 'Avisos',
        importance: aviso.urgente
            ? Importance.high
            : Importance.defaultImportance,
        priority: aviso.urgente ? Priority.high : Priority.defaultPriority,
        category: aviso.urgente ? AndroidNotificationCategory.reminder : null,
        styleInformation: BigTextStyleInformation(aviso.cuerpo),
        tag: 'aviso',
      ),
    ),
    payload: aviso.id, // al tocar la notificación la app abre este aviso
  );
}

/// Pide el permiso de notificaciones de Android 13+ (no hace nada si ya está concedido).
Future<void> pedirPermisoAvisos() async {
  await initAvisos();
  await _plugin
      .resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >()
      ?.requestNotificationsPermission();
}

/// Id del aviso cuya notificación abrió la app desde cerrada, o null si se abrió de otra forma.
Future<String?> avisoDeLanzamiento() async {
  await initAvisos();
  final detalles = await _plugin.getNotificationAppLaunchDetails();
  return detalles?.didNotificationLaunchApp == true
      ? detalles?.notificationResponse?.payload
      : null;
}
