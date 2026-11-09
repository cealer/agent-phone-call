// Tareas del firstmate principal (corre en el VPS): llegan por FCM y se guardan para los widgets.
import 'dart:convert';

import 'package:home_widget/home_widget.dart';

/// Más allá de esto el widget avisa de que la instantánea no es actual.
const tareasCaducan = Duration(hours: 2);

/// Opciones de respuesta cuando la decisión no trae `opciones`.
const opcionesPorDefecto = ['Sí', 'No'];

/// Máximo de botones de respuesta por decisión.
const maxOpciones = 3;

/// Un elemento de `espera`. Con [id] admite respuesta desde el widget; sin él (cadena) solo se muestra.
class Decision {
  const Decision({
    this.id,
    required this.titulo,
    this.detalle,
    this.opciones = const [],
  });

  final String? id;
  final String titulo;

  /// Texto completo opcional; el emisor puede acortar [titulo] para que la instantánea quepa en FCM.
  final String? detalle;
  final List<String> opciones;

  bool get respondible => id != null;

  /// Lo que se muestra completo y se envía al responder o pedir «Llámame».
  String get texto => detalle ?? titulo;

  /// Cadena (título) u objeto `{"id", "titulo", "detalle"?, "opciones"}`; null si no tiene título.
  static Decision? parse(Object? e) {
    if (e is String)
      return e.trim().isEmpty ? null : Decision(titulo: e.trim());
    if (e is! Map) return null;
    final titulo = e['titulo'];
    if (titulo is! String || titulo.trim().isEmpty) return null;
    final crudo = e['detalle'];
    final detalle = crudo is String && crudo.trim().isNotEmpty
        ? crudo.trim()
        : null;
    final id = e['id'];
    final idTexto = id is String || id is num ? '$id'.trim() : '';
    if (idTexto.isEmpty) {
      return Decision(titulo: titulo.trim(), detalle: detalle);
    }
    final crudas = e['opciones'];
    final opciones = [
      if (crudas is List)
        for (final o in crudas)
          if (o is String && o.trim().isNotEmpty) o.trim(),
    ].take(maxOpciones).toList();
    return Decision(
      id: idTexto,
      titulo: titulo.trim(),
      detalle: detalle,
      opciones: opciones.isEmpty ? opcionesPorDefecto : opciones,
    );
  }

  Object toJson() => id == null && detalle == null
      ? titulo
      : {
          'id': ?id,
          'titulo': titulo,
          'detalle': ?detalle,
          if (id != null) 'opciones': opciones,
        };
}

/// Texto que el widget manda a `POST /hablar` al confirmar una respuesta.
String textoRespuestaDecision(String id, String opcion, String titulo) =>
    '[widget-decision] id=$id respuesta=$opcion | El capitán responde «$opcion» a la decisión: $titulo';

/// Texto de «Llámame»: pide una llamada para explicar la decisión.
String textoLlamameDecision(String id, String titulo) =>
    '[widget-llamame] id=$id | El capitán pide que lo llames para explicarle la decisión: $titulo';

/// Instantánea de un mensaje FCM de datos `{"type": "tareas", "generado": ISO 8601 UTC, "tareas": "<cadena JSON>"}`.
class Tareas {
  const Tareas({
    required this.generado,
    required this.marcha,
    required this.espera,
    required this.cola,
  });

  final DateTime generado;
  final List<String> marcha;
  final List<Decision> espera;
  final List<String> cola;

  /// Null si el mensaje no es de tareas o `tareas` no es un JSON con forma de objeto.
  /// Una lista ausente o que no es lista queda vacía; lo que no es texto dentro de una lista se descarta.
  /// Los elementos de `espera` pueden ser además objetos [Decision].
  /// Sin `generado` válido se usa [ahora] (la hora de llegada).
  static Tareas? parse(Map<String, dynamic> data, DateTime ahora) {
    if (data['type'] != 'tareas') return null;
    final Object? decodificado;
    try {
      final crudo = data['tareas'];
      decodificado = crudo is String ? jsonDecode(crudo) : crudo;
    } catch (_) {
      return null;
    }
    if (decodificado is! Map) return null;
    return Tareas(
      generado:
          DateTime.tryParse('${data['generado']}')?.toUtc() ?? ahora.toUtc(),
      marcha: _titulos(decodificado['marcha']),
      espera: _decisiones(decodificado['espera']),
      cola: _titulos(decodificado['cola']),
    );
  }

  /// Lo que dejó [toJsonText] (clave `tareas` de HomeWidgetPreferences); null si falta o está dañado.
  static Tareas? fromStored(String? texto) {
    if (texto == null) return null;
    final Object? j;
    try {
      j = jsonDecode(texto);
    } catch (_) {
      return null;
    }
    if (j is! Map) return null;
    final generado = DateTime.tryParse('${j['generado']}');
    if (generado == null) return null;
    return Tareas(
      generado: generado.toUtc(),
      marcha: _titulos(j['marcha']),
      espera: _decisiones(j['espera']),
      cola: _titulos(j['cola']),
    );
  }

  bool caducada(DateTime ahora) =>
      ahora.toUtc().difference(generado) > tareasCaducan;

  /// Lo que lee el código nativo de los widgets (clave `tareas` de HomeWidgetPreferences).
  String toJsonText() => jsonEncode({
    'generado': generado.toUtc().toIso8601String(),
    'marcha': marcha,
    'espera': [for (final d in espera) d.toJson()],
    'cola': cola,
  });
}

List<String> _titulos(Object? valor) {
  if (valor is! List) return [];
  return [
    for (final e in valor)
      if (e is String && e.trim().isNotEmpty) e.trim(),
  ];
}

List<Decision> _decisiones(Object? valor) {
  if (valor is! List) return [];
  return [for (final e in valor) ?Decision.parse(e)];
}

const _widgetsConTareas = [
  'com.example.agentphonecall.FlotaWidget',
  'com.example.agentphonecall.DecisionesWidget',
];

/// Guarda la última instantánea y refresca los widgets; ignora el mensaje si no es de tareas. Sirve también en segundo plano.
Future<void> guardarTareas(Map<String, dynamic> data) async {
  final tareas = Tareas.parse(data, DateTime.now());
  if (tareas == null) return;
  await HomeWidget.saveWidgetData<String>('tareas', tareas.toJsonText());
  for (final widget in _widgetsConTareas) {
    await HomeWidget.updateWidget(qualifiedAndroidName: widget);
  }
}
