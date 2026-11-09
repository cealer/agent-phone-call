import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/avisos.dart';
import 'package:llamada/avisos_screen.dart';
import 'package:llamada/bandeja.dart';
import 'package:llamada/ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

Aviso aviso(
  String id, {
  String titulo = 'Título',
  String cuerpo = 'Cuerpo',
  String categoria = 'correo',
}) => Aviso.parse({
  'type': 'aviso',
  'id': id,
  'titulo': titulo,
  'cuerpo': cuerpo,
  'categoria': categoria,
  'urgente': '1',
})!;

final ahora = DateTime.utc(2026, 10, 21, 12);

void main() {
  test('registrar guarda, reemplaza por id y marca leído en el almacén del teléfono', () async {
    SharedPreferences.setMockInitialValues({});
    await registrarAviso(aviso('a', titulo: 'uno'));
    await registrarAviso(aviso('b'));
    await registrarAviso(aviso('a', titulo: 'dos'));
    var lista = await leerBandeja();
    expect(lista.map((a) => a.aviso.id), unorderedEquals(['a', 'b']));
    expect(lista.firstWhere((a) => a.aviso.id == 'a').aviso.titulo, 'dos');
    await marcarLeido('a');
    lista = await leerBandeja();
    expect(lista.firstWhere((a) => a.aviso.id == 'a').leido, isTrue);
    expect(lista.firstWhere((a) => a.aviso.id == 'b').leido, isFalse);
  });

  test('un aviso nuevo va primero', () {
    final lista = guardarEnBandeja([], aviso('a'), ahora);
    final lista2 = guardarEnBandeja(
      lista,
      aviso('b'),
      ahora.add(const Duration(minutes: 1)),
    );
    expect(lista2.map((a) => a.aviso.id), ['b', 'a']);
    expect(lista2.first.leido, isFalse);
  });

  test('el mismo id reemplaza la entrada y vuelve a no leído', () {
    var lista = guardarEnBandeja([], aviso('a', titulo: 'viejo'), ahora);
    lista = [lista.single.conLeido(true)];
    lista = guardarEnBandeja(
      lista,
      aviso('a', titulo: 'nuevo'),
      ahora.add(const Duration(hours: 1)),
    );
    expect(lista, hasLength(1));
    expect(lista.single.aviso.titulo, 'nuevo');
    expect(lista.single.leido, isFalse);
  });

  test('conserva solo los últimos $maxAvisos', () {
    var lista = <AvisoGuardado>[];
    for (var i = 0; i < maxAvisos + 15; i++) {
      lista = guardarEnBandeja(
        lista,
        aviso('id$i'),
        ahora.add(Duration(minutes: i)),
      );
    }
    expect(lista, hasLength(maxAvisos));
    expect(lista.first.aviso.id, 'id${maxAvisos + 14}');
    expect(lista.any((a) => a.aviso.id == 'id0'), isFalse);
  });

  test('descarta lo de más de $maxDiasAvisos días', () {
    var lista = guardarEnBandeja([], aviso('viejo'), ahora);
    lista = guardarEnBandeja(
      lista,
      aviso('nuevo'),
      ahora.add(const Duration(days: maxDiasAvisos + 1)),
    );
    expect(lista.map((a) => a.aviso.id), ['nuevo']);
  });

  test('ida y vuelta por JSON, y tolera datos dañados', () {
    final lista = [
      guardarEnBandeja([], aviso('a'), ahora).single.conLeido(true),
    ];
    final texto = jsonEncode([for (final a in lista) a.toJson()]);
    final leida = decodificarBandeja(texto);
    expect(leida.single.aviso.id, 'a');
    expect(leida.single.aviso.urgente, isTrue);
    expect(leida.single.leido, isTrue);
    expect(leida.single.recibido.toUtc(), ahora);
    expect(decodificarBandeja(null), isEmpty);
    expect(decodificarBandeja('no es json'), isEmpty);
    expect(decodificarBandeja('{"a":1}'), isEmpty);
    expect(decodificarBandeja('[1, {"id":"x"}]'), isEmpty);
  });

  test('hora del aviso: hoy, ayer y fecha', () {
    final t = DateTime(2026, 10, 21, 8, 5);
    expect(horaAviso(t, DateTime(2026, 10, 21, 20)), 'hoy 08:05');
    expect(horaAviso(t, DateTime(2026, 10, 22, 1)), 'ayer 08:05');
    expect(horaAviso(t, DateTime(2026, 10, 25)), '21 oct 08:05');
  });

  testWidgets(
    'la pantalla de un aviso muestra el cuerpo completo y seleccionable',
    (tester) async {
      final cuerpo = ('línea larga ' * 40).trim();
      await tester.pumpWidget(
        MaterialApp(
          theme: llamadaTheme(),
          home: Scaffold(
            body: AvisoDetalle(
              guardado: guardarEnBandeja(
                [],
                aviso('a', cuerpo: cuerpo),
                ahora,
              ).single,
            ),
          ),
        ),
      );
      expect(find.byType(SelectableText), findsNWidgets(2)); // título y cuerpo
      expect(find.text(cuerpo), findsOneWidget);
    },
  );
}
