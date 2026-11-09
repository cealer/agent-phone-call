// Hanging up gives the phone back: everything a call took (socket, WebRTC connection, microphone, the system
// call, the phone's call audio mode and audio focus) is released however the call ends.
import 'dart:async';

typedef Soltar = Future<void> Function();

/// What a call took from the phone, released when it ends.
///
/// - [tomar] registers something as it is taken; [cerrar] releases it, last taken first.
/// - [alTerminar] steps (end the system call, put the phone's audio back to normal, ...) run after that, always,
///   and again after anything taken once the call had already ended: hanging up while the call was still
///   connecting must not leave a microphone, or the call audio mode it switched on, behind.
/// - A step that fails or hangs is given up ([limite]) and reported ([alFallar]); the rest still run.
/// - Closing again (button, server's end, socket dropped, screen disposed) does nothing more.
class CierreLlamada {
  CierreLlamada({this.limite = const Duration(seconds: 2), this.alFallar});

  final Duration limite;
  final void Function(String paso, Object error)? alFallar;

  final _tomado = <(String, Soltar)>[];
  final _alTerminar = <(String, Soltar)>[];
  Future<void>? _cerrando;

  bool get cerrada => _cerrando != null;

  void alTerminar(String nombre, Soltar paso) => _alTerminar.add((nombre, paso));

  /// Registers something the call took; if the call already ended, it is released right away.
  Future<void> tomar(String nombre, Soltar soltar) {
    final cerrando = _cerrando;
    if (cerrando == null) {
      _tomado.add((nombre, soltar));
      return Future.value();
    }
    return _cerrando = cerrando.then((_) => _soltar([(nombre, soltar)]));
  }

  Future<void> cerrar() => _cerrando ??= _soltar(_tomado.reversed.toList());

  Future<void> _soltar(List<(String, Soltar)> tomado) async {
    _tomado.clear();
    for (final (nombre, paso) in [...tomado, ..._alTerminar]) {
      try {
        await paso().timeout(limite);
      } catch (error) {
        alFallar?.call(nombre, error);
      }
    }
  }
}
