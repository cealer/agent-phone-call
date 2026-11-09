// The call in text: what the agent says and what the user said, line by line, to reread while talking.
// Lives only in memory while the call screen is open; nothing is stored.
import 'package:flutter/material.dart';

import 'ui.dart';

@immutable
class LineaLlamada {
  const LineaLlamada({required this.delAgente, required this.texto});
  final bool delAgente;
  final String texto;

  @override
  bool operator ==(Object other) =>
      other is LineaLlamada && other.delAgente == delAgente && other.texto == texto;

  @override
  int get hashCode => Object.hash(delAgente, texto);

  @override
  String toString() => '${delAgente ? 'agente' : 'él'}: $texto';
}

/// A `{type: line, who: agent|user, text}` message from the server, or null for anything else.
LineaLlamada? lineaDesdeMensaje(Map<String, dynamic> data) {
  if (data['type'] != 'line') return null;
  final texto = (data['text'] as String? ?? '').trim();
  final quien = data['who'];
  if (texto.isEmpty || (quien != 'agent' && quien != 'user')) return null;
  return LineaLlamada(delAgente: quien == 'agent', texto: texto);
}

/// The conversation as a scrollable chat that follows the last line, unless he scrolled up to reread:
/// then it stays where he is and a «Nuevo» pill takes him back to the end.
class TextoLlamada extends StatefulWidget {
  const TextoLlamada({super.key, required this.lineas, required this.nombre});
  final List<LineaLlamada> lineas;
  final String nombre;

  @override
  State<TextoLlamada> createState() => _TextoLlamadaState();
}

class _TextoLlamadaState extends State<TextoLlamada> {
  static const _holgura = 40.0; // this close to the end still counts as "at the end"
  final _scroll = ScrollController();
  bool _siguiendo = true;
  bool _hayNuevo = false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_alMover);
    _alFinalLuego();
  }

  @override
  void didUpdateWidget(TextoLlamada old) {
    super.didUpdateWidget(old);
    if (widget.lineas.length == old.lineas.length) return;
    if (_siguiendo) {
      _alFinalLuego();
    } else {
      setState(() => _hayNuevo = true);
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _alMover() {
    final p = _scroll.position;
    final alFinal = p.maxScrollExtent - p.pixels <= _holgura;
    if (alFinal != _siguiendo || (alFinal && _hayNuevo)) {
      setState(() {
        _siguiendo = alFinal;
        if (alFinal) _hayNuevo = false;
      });
    }
  }

  void _alFinalLuego() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  void _irAlFinal() {
    setState(() {
      _siguiendo = true;
      _hayNuevo = false;
    });
    _scroll.jumpTo(_scroll.position.maxScrollExtent);
    _alFinalLuego(); // the last lines may still be laying out
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        ShaderMask(
          shaderCallback: (rect) => const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Colors.transparent, Colors.white, Colors.white],
            stops: [0, 0.05, 1],
          ).createShader(rect),
          blendMode: BlendMode.dstIn,
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.fromLTRB(0, 14, 0, 8),
            itemCount: widget.lineas.length,
            itemBuilder: (context, i) {
              final linea = widget.lineas[i];
              final mismoQueAntes = i > 0 && widget.lineas[i - 1].delAgente == linea.delAgente;
              return _Burbuja(linea: linea, nombre: widget.nombre, conNombre: !mismoQueAntes);
            },
          ),
        ),
        if (_hayNuevo)
          Positioned(
            bottom: 8,
            left: 0,
            right: 0,
            child: Center(
              child: Material(
                color: Palette.surfaceHigh,
                shape: const StadiumBorder(side: BorderSide(color: Palette.outline)),
                child: InkWell(
                  customBorder: const StadiumBorder(),
                  onTap: _irAlFinal,
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.arrow_downward_rounded, size: 16, color: Palette.text),
                        SizedBox(width: 6),
                        Text(
                          'Nuevo',
                          style: TextStyle(color: Palette.text, fontWeight: FontWeight.w600),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _Burbuja extends StatelessWidget {
  const _Burbuja({required this.linea, required this.nombre, required this.conNombre});
  final LineaLlamada linea;
  final String nombre;
  final bool conNombre;

  @override
  Widget build(BuildContext context) {
    final agente = linea.delAgente;
    return Padding(
      padding: EdgeInsets.only(top: conNombre ? 12 : 4),
      child: Column(
        crossAxisAlignment: agente ? CrossAxisAlignment.start : CrossAxisAlignment.end,
        children: [
          if (conNombre)
            Padding(
              padding: const EdgeInsets.only(bottom: 4, left: 4, right: 4),
              child: Text(
                agente ? nombre : 'Tú',
                style: TextStyle(
                  color: agente ? Palette.accent : Palette.green,
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.2,
                ),
              ),
            ),
          ConstrainedBox(
            constraints: BoxConstraints(
              maxWidth: MediaQuery.sizeOf(context).width * (agente ? 0.86 : 0.78),
            ),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: agente
                    ? Palette.surfaceHigh.withValues(alpha: 0.92)
                    : Palette.green.withValues(alpha: 0.16),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(18),
                  topRight: const Radius.circular(18),
                  bottomLeft: Radius.circular(agente ? 6 : 18),
                  bottomRight: Radius.circular(agente ? 18 : 6),
                ),
              ),
              child: SelectableText(
                linea.texto,
                style: const TextStyle(color: Palette.text, fontSize: 17, height: 1.38),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
