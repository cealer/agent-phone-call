// Bandeja de avisos: guarda en el teléfono cada aviso recibido para poder verlo completo y volver a él.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'avisos.dart';

/// Un aviso con la hora a la que llegó y si ya se abrió.
class AvisoGuardado {
  const AvisoGuardado({
    required this.aviso,
    required this.recibido,
    this.leido = false,
  });

  final Aviso aviso;
  final DateTime recibido;
  final bool leido;

  AvisoGuardado conLeido(bool valor) =>
      AvisoGuardado(aviso: aviso, recibido: recibido, leido: valor);

  Map<String, dynamic> toJson() => {
    'id': aviso.id,
    'categoria': aviso.categoria,
    'titulo': aviso.titulo,
    'cuerpo': aviso.cuerpo,
    'urgente': aviso.urgente ? '1' : '0',
    'recibido': recibido.toUtc().toIso8601String(),
    'leido': leido,
  };

  /// Null si la entrada guardada está dañada.
  static AvisoGuardado? fromJson(Object? json) {
    if (json is! Map) return null;
    final aviso = Aviso.parse({
      ...json.cast<String, dynamic>(),
      'type': 'aviso',
    });
    final recibido = DateTime.tryParse('${json['recibido']}');
    if (aviso == null || recibido == null) return null;
    return AvisoGuardado(
      aviso: aviso,
      recibido: recibido.toLocal(),
      leido: json['leido'] == true,
    );
  }
}

const maxAvisos = 200;
const maxDiasAvisos = 30;

/// Añade el aviso al principio; si ya había uno con el mismo `id` lo reemplaza (vuelve a no leído) en vez de duplicarlo.
/// Conserva los últimos [maxAvisos] y descarta lo de más de [maxDiasAvisos] días. Devuelve la lista del más reciente al más antiguo.
List<AvisoGuardado> guardarEnBandeja(
  List<AvisoGuardado> actuales,
  Aviso aviso,
  DateTime ahora,
) {
  final lista = [
    AvisoGuardado(aviso: aviso, recibido: ahora),
    ...actuales.where((a) => a.aviso.id != aviso.id),
  ];
  lista.sort((a, b) => b.recibido.compareTo(a.recibido));
  final limite = ahora.subtract(const Duration(days: maxDiasAvisos));
  return [
    for (final a
        in lista.where((a) => !a.recibido.isBefore(limite)).take(maxAvisos))
      a,
  ];
}

const _clave = 'bandeja_avisos';

/// Lee la bandeja guardada; `reload` porque el manejador de segundo plano escribe desde otro isolate.
Future<List<AvisoGuardado>> leerBandeja() async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  return decodificarBandeja(prefs.getString(_clave));
}

List<AvisoGuardado> decodificarBandeja(String? texto) {
  if (texto == null) return [];
  try {
    final lista = jsonDecode(texto);
    if (lista is! List) return [];
    return [for (final e in lista) ?AvisoGuardado.fromJson(e)];
  } catch (_) {
    return [];
  }
}

Future<void> _escribir(SharedPreferences prefs, List<AvisoGuardado> lista) =>
    prefs.setString(_clave, jsonEncode([for (final a in lista) a.toJson()]));

/// Guarda el aviso recibido (también desde segundo plano).
Future<void> registrarAviso(Aviso aviso) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  await _escribir(
    prefs,
    guardarEnBandeja(
      decodificarBandeja(prefs.getString(_clave)),
      aviso,
      DateTime.now(),
    ),
  );
}

Future<void> marcarLeido(String id) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.reload();
  final lista = decodificarBandeja(prefs.getString(_clave));
  await _escribir(prefs, [
    for (final a in lista) a.aviso.id == id ? a.conLeido(true) : a,
  ]);
}
