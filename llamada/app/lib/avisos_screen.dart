// Pantallas de la bandeja: lista de avisos y un aviso a tamaño completo.
import 'package:flutter/material.dart';

import 'bandeja.dart';
import 'ui.dart';

String etiquetaCategoria(String categoria) => switch (categoria) {
  'reunion' => 'Reunión',
  'correo' => 'Correo',
  'decision' => 'Decisión',
  'salud' => 'Salud',
  _ => 'Resumen',
};

IconData iconoCategoria(String categoria) => switch (categoria) {
  'reunion' => Icons.event_rounded,
  'correo' => Icons.mail_outline_rounded,
  'decision' => Icons.help_outline_rounded,
  'salud' => Icons.favorite_border_rounded,
  _ => Icons.wb_sunny_outlined,
};

/// «hoy 08:30», «ayer 19:05» o «3 oct 08:30».
String horaAviso(DateTime t, DateTime ahora) {
  String dos(int n) => n.toString().padLeft(2, '0');
  final hora = '${dos(t.hour)}:${dos(t.minute)}';
  final dias = DateTime(
    ahora.year,
    ahora.month,
    ahora.day,
  ).difference(DateTime(t.year, t.month, t.day)).inDays;
  if (dias == 0) return 'hoy $hora';
  if (dias == 1) return 'ayer $hora';
  const meses = [
    'ene',
    'feb',
    'mar',
    'abr',
    'may',
    'jun',
    'jul',
    'ago',
    'sep',
    'oct',
    'nov',
    'dic',
  ];
  return '${t.day} ${meses[t.month - 1]} $hora';
}

/// Fila de la pantalla principal que lleva a la bandeja.
class AvisosEntrada extends StatelessWidget {
  const AvisosEntrada({super.key, required this.sinLeer, required this.onTap});
  final int sinLeer;
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
              const Icon(
                Icons.notifications_none_rounded,
                color: Palette.accent,
                size: 22,
              ),
              const SizedBox(width: 14),
              const Expanded(
                child: Text(
                  'Avisos',
                  style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w500),
                ),
              ),
              if (sinLeer > 0)
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 9,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: Palette.accent,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '$sinLeer',
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
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

class AvisosScreen extends StatefulWidget {
  const AvisosScreen({super.key});

  @override
  State<AvisosScreen> createState() => _AvisosScreenState();
}

class _AvisosScreenState extends State<AvisosScreen>
    with WidgetsBindingObserver {
  List<AvisoGuardado>? _avisos;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _cargar();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _cargar();
  }

  Future<void> _cargar() async {
    final avisos = await leerBandeja();
    if (mounted) setState(() => _avisos = avisos);
  }

  Future<void> _abrir(AvisoGuardado a) async {
    await Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => AvisoScreen(id: a.aviso.id)));
    _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final avisos = _avisos;
    final ahora = DateTime.now();
    return Scaffold(
      appBar: AppBar(
        title: const Text('Avisos'),
        backgroundColor: Palette.background,
      ),
      body: avisos == null
          ? const SizedBox.shrink()
          : avisos.isEmpty
          ? const Center(
              child: Text(
                'Aún no hay avisos.',
                style: TextStyle(color: Palette.textDim),
              ),
            )
          : RefreshIndicator(
              color: Palette.accent,
              backgroundColor: Palette.surfaceHigh,
              onRefresh: _cargar,
              child: ListView.separated(
                padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
                itemCount: avisos.length,
                separatorBuilder: (_, _) => const SizedBox(height: 10),
                itemBuilder: (_, i) => _FilaAviso(
                  guardado: avisos[i],
                  ahora: ahora,
                  onTap: () => _abrir(avisos[i]),
                ),
              ),
            ),
    );
  }
}

class _FilaAviso extends StatelessWidget {
  const _FilaAviso({
    required this.guardado,
    required this.ahora,
    required this.onTap,
  });
  final AvisoGuardado guardado;
  final DateTime ahora;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final aviso = guardado.aviso;
    final sinLeer = !guardado.leido;
    return Material(
      color: Palette.surface,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: Palette.outline.withValues(alpha: 0.6)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                iconoCategoria(aviso.categoria),
                color: aviso.urgente ? Palette.orange : Palette.textDim,
                size: 22,
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${etiquetaCategoria(aviso.categoria)} · ${horaAviso(guardado.recibido, ahora)}',
                      style: const TextStyle(
                        color: Palette.textDim,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      aviso.titulo,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 15.5,
                        fontWeight: sinLeer ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                    if (aviso.cuerpo.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        aviso.cuerpo,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Palette.textDim,
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (sinLeer) ...[
                const SizedBox(width: 10),
                Container(
                  key: const ValueKey('sin-leer'),
                  margin: const EdgeInsets.only(top: 6),
                  width: 10,
                  height: 10,
                  decoration: const BoxDecoration(
                    color: Palette.accent,
                    shape: BoxShape.circle,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Un aviso a tamaño completo; al abrirlo se marca como leído.
class AvisoScreen extends StatefulWidget {
  const AvisoScreen({super.key, required this.id});
  final String id;

  @override
  State<AvisoScreen> createState() => _AvisoScreenState();
}

class _AvisoScreenState extends State<AvisoScreen> {
  AvisoGuardado? _aviso;
  bool _cargado = false;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    final aviso = (await leerBandeja())
        .where((a) => a.aviso.id == widget.id)
        .firstOrNull;
    if (aviso != null) await marcarLeido(widget.id);
    if (!mounted) return;
    setState(() {
      _aviso = aviso;
      _cargado = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    final guardado = _aviso;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Aviso'),
        backgroundColor: Palette.background,
      ),
      body: !_cargado
          ? const SizedBox.shrink()
          : guardado == null
          ? const Center(
              child: Text(
                'Este aviso ya no está guardado.',
                style: TextStyle(color: Palette.textDim),
              ),
            )
          : AvisoDetalle(guardado: guardado),
    );
  }
}

/// Título, categoría, hora y cuerpo completo (texto seleccionable) de un aviso.
class AvisoDetalle extends StatelessWidget {
  const AvisoDetalle({super.key, required this.guardado});
  final AvisoGuardado guardado;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(22, 12, 22, 32),
        children: [
          Row(
            children: [
              Icon(
                iconoCategoria(guardado.aviso.categoria),
                color: guardado.aviso.urgente ? Palette.orange : Palette.accent,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                '${etiquetaCategoria(guardado.aviso.categoria).toUpperCase()}${guardado.aviso.urgente ? ' · URGENTE' : ''}',
                style: const TextStyle(
                  color: Palette.textDim,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 1.2,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          SelectableText(
            guardado.aviso.titulo,
            style: const TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.4,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            horaAviso(guardado.recibido, DateTime.now()),
            style: const TextStyle(color: Palette.textDim, fontSize: 13.5),
          ),
          const SizedBox(height: 22),
          SelectableText(
            guardado.aviso.cuerpo,
            style: const TextStyle(fontSize: 17, height: 1.5),
          ),
        ],
      ),
    );
  }
}
