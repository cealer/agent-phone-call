import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/cierre_llamada.dart';

void main() {
  test('al colgar suelta todo lo que tomó la llamada, lo último primero', () async {
    final hecho = <String>[];
    final cierre = CierreLlamada();
    for (final nombre in ['audio del teléfono', 'micrófono', 'conexión WebRTC', 'socket']) {
      await cierre.tomar(nombre, () async => hecho.add(nombre));
    }
    await cierre.cerrar();
    expect(hecho, ['socket', 'conexión WebRTC', 'micrófono', 'audio del teléfono']);
  });

  test('un paso que falla no impide soltar los demás, y se avisa', () async {
    final hecho = <String>[];
    final fallos = <String>[];
    final cierre = CierreLlamada(alFallar: (paso, _) => fallos.add(paso));
    await cierre.tomar('audio del teléfono', () async => hecho.add('audio del teléfono'));
    await cierre.tomar('conexión WebRTC', () async => throw StateError('ya cerrada'));
    await cierre.tomar('socket', () async => hecho.add('socket'));
    await cierre.cerrar();
    expect(hecho, ['socket', 'audio del teléfono']);
    expect(fallos, ['conexión WebRTC']);
  });

  test('un paso que se cuelga se abandona tras el límite y los demás se sueltan igual', () async {
    final hecho = <String>[];
    final fallos = <String>[];
    final cierre = CierreLlamada(
      limite: const Duration(milliseconds: 50),
      alFallar: (paso, _) => fallos.add(paso),
    );
    await cierre.tomar('audio del teléfono', () async => hecho.add('audio del teléfono'));
    await cierre.tomar('socket', () => Completer<void>().future); // el servidor nunca contesta al cierre
    await cierre.cerrar().timeout(const Duration(seconds: 1));
    expect(hecho, ['audio del teléfono']);
    expect(fallos, ['socket']);
  });

  test('colgar dos veces (botón, fin del servidor, socket caído) suelta una sola vez', () async {
    var veces = 0;
    final cierre = CierreLlamada();
    await cierre.tomar('micrófono', () async => veces++);
    await Future.wait([cierre.cerrar(), cierre.cerrar()]);
    await cierre.cerrar();
    expect(veces, 1);
    expect(cierre.cerrada, isTrue);
  });

  test('lo que se toma después de colgar (colgó mientras conectaba) se suelta en el acto, '
      'y el audio del teléfono se vuelve a dejar normal detrás', () async {
    final hecho = <String>[];
    final cierre = CierreLlamada();
    cierre.alTerminar('audio del teléfono', () async => hecho.add('audio del teléfono'));
    await cierre.tomar('socket', () async => hecho.add('socket'));
    await cierre.cerrar();
    expect(hecho, ['socket', 'audio del teléfono']);
    // getUserMedia volvió después de colgar: el micrófono (y el modo llamada que activó) no se quedan tomados.
    await cierre.tomar('micrófono', () async => hecho.add('micrófono'));
    expect(hecho, ['socket', 'audio del teléfono', 'micrófono', 'audio del teléfono']);
  });

  test('lo de terminar va siempre después de soltar lo tomado, aunque se registre antes', () async {
    final hecho = <String>[];
    final cierre = CierreLlamada();
    cierre.alTerminar('llamada del sistema', () async => hecho.add('llamada del sistema'));
    cierre.alTerminar('audio del teléfono', () async => hecho.add('audio del teléfono'));
    await cierre.tomar('micrófono', () async => hecho.add('micrófono'));
    await cierre.tomar('conexión WebRTC', () async => throw StateError('falló'));
    await cierre.cerrar();
    expect(hecho, ['micrófono', 'llamada del sistema', 'audio del teléfono']);
  });
}
