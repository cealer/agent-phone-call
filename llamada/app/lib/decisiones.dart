// Sección «Decisiones»: lo que espera la respuesta del capitán según la última instantánea `tareas` del principal.
// El envío y el estado (respondido, error, «Llámame») viven en el código nativo que comparte el widget Decisiones.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:home_widget/home_widget.dart';

import 'tareas.dart';
import 'ui.dart';

enum FaseDecision { pendiente, respondida, error, llamame }

/// Cómo está una decisión en este teléfono y la línea que se muestra bajo su título.
class EstadoDecision {
  const EstadoDecision(this.fase, [this.texto]);
  final FaseDecision fase;
  final String? texto;
}

/// Lo respondido, fallido o pedido por «Llámame», tal como lo guarda `DecisionesEstado` (Kotlin).
class EstadosDecisiones {
  const EstadosDecisiones({
    this.respondidas = const {},
    this.errores = const {},
    this.llamames = const {},
  });

  final Map<String, String> respondidas;
  final Map<String, String> errores;
  final Set<String> llamames;

  static EstadosDecisiones fromJson(Object? json) {
    if (json is! Map) return const EstadosDecisiones();
    Map<String, String> mapa(Object? m) => {
      if (m is Map)
        for (final e in m.entries) '${e.key}': '${e.value}',
    };
    final ll = json['llamames'];
    return EstadosDecisiones(
      respondidas: mapa(json['respondidas']),
      errores: mapa(json['errores']),
      llamames: {if (ll is List) for (final e in ll) '$e'},
    );
  }

  /// Igual precedencia que el widget: respondida, luego error, luego «Llámame».
  EstadoDecision de(String id) {
    final r = respondidas[id];
    if (r != null) {
      return EstadoDecision(FaseDecision.respondida, 'Respondido: $r');
    }
    final e = errores[id];
    if (e != null) {
      return EstadoDecision(
        FaseDecision.error,
        'No se envió: $e Toca para reintentar.',
      );
    }
    if (llamames.contains(id)) {
      return const EstadoDecision(
        FaseDecision.llamame,
        'Pedido: te llamarán',
      );
    }
    return const EstadoDecision(FaseDecision.pendiente);
  }
}

/// «11:30», o «11:30 · hace 3 h» / «hace 2 d» cuando la instantánea ya no es actual (misma regla que el widget).
String etiquetaInstantanea(DateTime generado, DateTime ahora) {
  String dos(int n) => n.toString().padLeft(2, '0');
  final local = generado.toLocal();
  final hora = '${dos(local.hour)}:${dos(local.minute)}';
  final edad = ahora.difference(generado);
  if (edad <= tareasCaducan) return hora;
  return edad.inHours >= 48
      ? '$hora · hace ${edad.inDays} d'
      : '$hora · hace ${edad.inHours} h';
}

/// Envío y estado de las decisiones, compartidos con el widget (`DecisionEnvio` y `DecisionesEstado` en Kotlin).
/// `responder` y `llamame` devuelven null si el servidor aceptó el envío, o el motivo del fallo.
abstract class DecisionesNativo {
  Future<EstadosDecisiones> estados();
  Future<String?> responder(String id, String opcion, String titulo);
  Future<String?> llamame(String id, String titulo);
}

class DecisionesCanal implements DecisionesNativo {
  const DecisionesCanal();
  static const _canal = MethodChannel('llamada/decisiones');

  @override
  Future<EstadosDecisiones> estados() async {
    final texto = await _canal.invokeMethod<String>('estados');
    try {
      return EstadosDecisiones.fromJson(texto == null ? null : jsonDecode(texto));
    } catch (_) {
      return const EstadosDecisiones();
    }
  }

  @override
  Future<String?> responder(String id, String opcion, String titulo) =>
      _canal.invokeMethod<String>('responder', {
        'id': id,
        'opcion': opcion,
        'titulo': titulo,
      });

  @override
  Future<String?> llamame(String id, String titulo) => _canal.invokeMethod<String>(
    'llamame',
    {'id': id, 'titulo': titulo},
  );
}

/// La última instantánea que dejó el mensaje FCM (la misma que leen los widgets).
Future<Tareas?> leerTareasGuardadas() async =>
    Tareas.fromStored(await HomeWidget.getWidgetData<String>('tareas'));

/// Fila de la pantalla principal que lleva a la sección Decisiones.
class DecisionesEntrada extends StatelessWidget {
  const DecisionesEntrada({super.key, required this.pendientes, required this.onTap});
  final int pendientes;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GroupCard(
    children: [
      InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              const Icon(Icons.help_outline_rounded, color: Palette.amber, size: 22),
              const SizedBox(width: 14),
              const Expanded(
                child: Text(
                  'Decisiones',
                  style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w500),
                ),
              ),
              if (pendientes > 0)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 2),
                  decoration: BoxDecoration(
                    color: Palette.amber,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '$pendientes',
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
                      color: Colors.black,
                    ),
                  ),
                ),
              const SizedBox(width: 6),
              const Icon(Icons.chevron_right_rounded, color: Palette.textDim),
            ],
          ),
        ),
      ),
    ],
  );
}

class DecisionesScreen extends StatefulWidget {
  const DecisionesScreen({
    super.key,
    this.nativo = const DecisionesCanal(),
    this.cargar = leerTareasGuardadas,
    this.ahora = DateTime.now,
    this.abrirDecision,
  });

  final DecisionesNativo nativo;
  final Future<Tareas?> Function() cargar;
  final DateTime Function() ahora;

  /// Decisión que abrir en detalle al cargar (la tocada en el widget): su id, o su texto si no tiene id.
  final String? abrirDecision;

  @override
  State<DecisionesScreen> createState() => _DecisionesScreenState();
}

class _DecisionesScreenState extends State<DecisionesScreen>
    with WidgetsBindingObserver, _AccionesDecision {
  Tareas? _tareas;
  bool _cargado = false;
  bool _abiertaDelWidget = false;

  @override
  DecisionesNativo get nativo => widget.nativo;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _recargar();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _recargar();
  }

  Future<void> _recargar() async {
    Tareas? tareas;
    var nuevos = const EstadosDecisiones();
    try {
      tareas = await widget.cargar();
      nuevos = await nativo.estados();
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _tareas = tareas;
      estados = nuevos;
      _cargado = true;
    });
    final clave = widget.abrirDecision;
    if (clave == null || _abiertaDelWidget || tareas == null) return;
    _abiertaDelWidget = true;
    final d = tareas.espera
        .where((d) => d.id == clave || (d.id == null && d.texto == clave))
        .firstOrNull;
    if (d != null) await _abrirDetalle(d);
  }

  Future<void> _abrirDetalle(Decision d) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => DecisionDetalleScreen(decision: d, nativo: nativo),
      ),
    );
    // Lo respondido o pedido en el detalle se ve al volver.
    final e = await nativo.estados().catchError((_) => estados);
    if (mounted) setState(() => estados = e);
  }

  @override
  Widget build(BuildContext context) {
    final tareas = _tareas;
    final ahora = widget.ahora();
    final caducada = tareas?.caducada(ahora) ?? false;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Decisiones'),
        backgroundColor: Palette.background,
      ),
      body: !_cargado
          ? const SizedBox.shrink()
          : tareas == null
          ? const _Mensaje('Aún no ha llegado ninguna instantánea del principal.')
          : RefreshIndicator(
              color: Palette.accent,
              backgroundColor: Palette.surfaceHigh,
              onRefresh: _recargar,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
                    child: Text(
                      'Principal · ${etiquetaInstantanea(tareas.generado, ahora)}'
                      '${caducada ? ' · puede no estar al día' : ''}',
                      style: TextStyle(
                        color: caducada ? Palette.amber : Palette.textDim,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                  if (tareas.espera.isEmpty)
                    const _Mensaje('Nada espera tu decisión.')
                  else
                    for (final d in tareas.espera) ...[
                      _FilaDecision(
                        decision: d,
                        estado: estadoDe(d),
                        enviando: enviando.contains(d.id),
                        onOpcion: (o) => responder(d, o),
                        onLlamame: () => llamame(d),
                        onTap: () => _abrirDetalle(d),
                      ),
                      const SizedBox(height: 10),
                    ],
                ],
              ),
            ),
    );
  }
}

/// Responder (con confirmación) y «Llámame», compartidos por la lista y el detalle.
mixin _AccionesDecision<T extends StatefulWidget> on State<T> {
  DecisionesNativo get nativo;
  EstadosDecisiones estados = const EstadosDecisiones();
  final enviando = <String>{};

  EstadoDecision estadoDe(Decision d) => d.id == null
      ? const EstadoDecision(FaseDecision.pendiente)
      : estados.de(d.id!);

  Future<void> responder(Decision d, String opcion) async {
    final confirmado = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: Palette.surfaceHigh,
        title: const Text('Decisión'),
        content: Text('¿Responder "$opcion" a ${d.texto}?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancelar'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Confirmar'),
          ),
        ],
      ),
    );
    if (confirmado != true) return;
    await _enviar(d, () => nativo.responder(d.id!, opcion, d.texto));
  }

  Future<void> llamame(Decision d) =>
      _enviar(d, () => nativo.llamame(d.id!, d.texto));

  Future<void> _enviar(Decision d, Future<String?> Function() envio) async {
    setState(() => enviando.add(d.id!));
    try {
      await envio();
    } catch (_) {}
    final nuevos = await nativo.estados().catchError((_) => estados);
    if (!mounted) return;
    setState(() {
      enviando.remove(d.id!);
      estados = nuevos;
    });
  }
}

/// Una decisión sola: el texto completo y las mismas acciones que en la lista.
class DecisionDetalleScreen extends StatefulWidget {
  const DecisionDetalleScreen({
    super.key,
    required this.decision,
    this.nativo = const DecisionesCanal(),
  });

  final Decision decision;
  final DecisionesNativo nativo;

  @override
  State<DecisionDetalleScreen> createState() => _DecisionDetalleScreenState();
}

class _DecisionDetalleScreenState extends State<DecisionDetalleScreen>
    with _AccionesDecision {
  @override
  DecisionesNativo get nativo => widget.nativo;

  @override
  void initState() {
    super.initState();
    nativo.estados().then((e) {
      if (mounted) setState(() => estados = e);
    }, onError: (_) {});
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.decision;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Decisión'),
        backgroundColor: Palette.background,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
        children: [
          _FilaDecision(
            decision: d,
            estado: estadoDe(d),
            enviando: enviando.contains(d.id),
            onOpcion: (o) => responder(d, o),
            onLlamame: () => llamame(d),
          ),
        ],
      ),
    );
  }
}

class _Mensaje extends StatelessWidget {
  const _Mensaje(this.texto);
  final String texto;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(32),
    child: Center(
      child: Text(
        texto,
        textAlign: TextAlign.center,
        style: const TextStyle(color: Palette.textDim),
      ),
    ),
  );
}

class _FilaDecision extends StatelessWidget {
  const _FilaDecision({
    required this.decision,
    required this.estado,
    required this.enviando,
    required this.onOpcion,
    required this.onLlamame,
    this.onTap,
  });

  final Decision decision;
  final EstadoDecision estado;
  final bool enviando;
  final void Function(String opcion) onOpcion;
  final VoidCallback onLlamame;

  /// En la lista abre el detalle; en el detalle no hay.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final color = switch (estado.fase) {
      FaseDecision.respondida => Palette.green,
      FaseDecision.error => Palette.red,
      _ => Palette.accent,
    };
    final botones = decision.respondible &&
        estado.fase != FaseDecision.respondida &&
        !enviando;
    final tarjeta = Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Palette.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Padding(
                padding: EdgeInsets.only(top: 5),
                child: Icon(Icons.diamond, size: 10, color: Palette.amber),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  decision.texto,
                  style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600),
                ),
              ),
            ],
          ),
          if (estado.texto != null) ...[
            const SizedBox(height: 6),
            Text(estado.texto!, style: TextStyle(color: color, fontSize: 13)),
          ],
          if (enviando) ...[
            const SizedBox(height: 8),
            const LinearProgressIndicator(minHeight: 2),
          ],
          if (botones) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final o in decision.opciones)
                  OutlinedButton(
                    onPressed: () => onOpcion(o),
                    child: Text(o),
                  ),
                TextButton(
                  onPressed: onLlamame,
                  child: const Text('Llámame'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
    return onTap == null
        ? tarjeta
        : GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: onTap,
            child: tarjeta,
          );
  }
}
