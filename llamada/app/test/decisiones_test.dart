import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/decisiones.dart';
import 'package:llamada/tareas.dart';
import 'package:llamada/ui.dart';

final ahora = DateTime.utc(2026, 10, 21, 12);

Tareas instantanea(List<Object> espera, [String generado = '2026-10-21T11:30:00Z']) =>
    Tareas.parse({
      'type': 'tareas',
      'generado': generado,
      'tareas': jsonEncode({'espera': espera}),
    }, ahora)!;

class NativoFalso implements DecisionesNativo {
  NativoFalso({this.estado = const EstadosDecisiones(), this.fallo});
  EstadosDecisiones estado;
  String? fallo;
  final llamadas = <String>[];

  @override
  Future<EstadosDecisiones> estados() async => estado;

  @override
  Future<String?> responder(String id, String opcion, String titulo) async {
    llamadas.add('responder $id $opcion | $titulo');
    if (fallo == null) {
      estado = EstadosDecisiones(respondidas: {id: opcion});
    } else {
      estado = EstadosDecisiones(errores: {id: fallo!});
    }
    return fallo;
  }

  @override
  Future<String?> llamame(String id, String titulo) async {
    llamadas.add('llamame $id | $titulo');
    estado = EstadosDecisiones(llamames: {id});
    return null;
  }
}

Future<void> abrir(
  WidgetTester tester,
  NativoFalso nativo,
  Tareas? t, {
  String? abrirDecision,
}) => tester.pumpWidget(
  MaterialApp(
    key: UniqueKey(), // navegador nuevo: no hereda el detalle abierto en otra prueba
    theme: llamadaTheme(),
    home: DecisionesScreen(
      key: UniqueKey(),
      nativo: nativo,
      cargar: () async => t,
      ahora: () => ahora,
      abrirDecision: abrirDecision,
    ),
  ),
);

void main() {
  group('Tareas.fromStored', () {
    test('ida y vuelta de lo que guarda toJsonText', () {
      final t = instantanea([
        'solo texto',
        {
          'id': 'd1',
          'titulo': 'Subir',
          'opciones': ['Subir', 'Esperar'],
        },
      ]);
      final leida = Tareas.fromStored(t.toJsonText())!;
      expect(leida.generado, DateTime.utc(2026, 10, 21, 11, 30));
      expect(leida.espera.map((d) => d.id), [null, 'd1']);
      expect(leida.espera.last.opciones, ['Subir', 'Esperar']);
    });

    test('texto ausente o dañado es null', () {
      expect(Tareas.fromStored(null), isNull);
      expect(Tareas.fromStored('no es json'), isNull);
      expect(Tareas.fromStored('[1]'), isNull);
    });
  });

  group('EstadosDecisiones', () {
    test('lee lo que guarda el código nativo y resuelve cada decisión', () {
      final e = EstadosDecisiones.fromJson({
        'respondidas': {'a': 'Sí'},
        'errores': {'b': 'No hay conexión con tu Mac.'},
        'llamames': ['c'],
      });
      expect(e.de('a').fase, FaseDecision.respondida);
      expect(e.de('a').texto, 'Respondido: Sí');
      expect(e.de('b').fase, FaseDecision.error);
      expect(e.de('b').texto, 'No se envió: No hay conexión con tu Mac. Toca para reintentar.');
      expect(e.de('c').fase, FaseDecision.llamame);
      expect(e.de('c').texto, 'Pedido: te llamarán');
      expect(e.de('z').fase, FaseDecision.pendiente);
      expect(e.de('z').texto, isNull);
    });

    test('respondida gana a un error previo; datos dañados no rompen', () {
      final e = EstadosDecisiones.fromJson({
        'respondidas': {'a': 'Sí'},
        'errores': {'a': 'x'},
      });
      expect(e.de('a').fase, FaseDecision.respondida);
      expect(EstadosDecisiones.fromJson(null).de('a').fase, FaseDecision.pendiente);
      expect(EstadosDecisiones.fromJson('x').de('a').fase, FaseDecision.pendiente);
    });
  });

  test('etiqueta de la instantánea: hora, y antigüedad cuando caduca', () {
    final g = DateTime(2026, 10, 21, 11, 30);
    expect(etiquetaInstantanea(g, g.add(const Duration(minutes: 90))), '11:30');
    expect(etiquetaInstantanea(g, g.add(const Duration(hours: 2))), '11:30');
    expect(etiquetaInstantanea(g, g.add(const Duration(hours: 3, minutes: 5))), '11:30 · hace 3 h');
    expect(etiquetaInstantanea(g, g.add(const Duration(hours: 50))), '11:30 · hace 2 d');
  });

  group('pantalla Decisiones', () {
    final espera = [
      'Solo texto',
      {
        'id': 'd1',
        'titulo': 'Subir a producción',
        'opciones': ['Subir', 'Esperar'],
      },
      {'id': 'd2', 'titulo': 'Borrar rama'},
      {'id': 'd3', 'titulo': 'Cuarta decisión'},
    ];

    testWidgets('lista TODAS las decisiones con sus opciones y la hora', (tester) async {
      await abrir(tester, NativoFalso(), instantanea(espera.cast<Object>()));
      await tester.pumpAndSettle();
      for (final t in ['Solo texto', 'Subir a producción', 'Borrar rama', 'Cuarta decisión']) {
        expect(find.text(t), findsOneWidget);
      }
      expect(find.text('Subir'), findsOneWidget);
      expect(find.text('Esperar'), findsOneWidget);
      expect(find.text('Sí'), findsNWidgets(2));
      expect(find.text('Llámame'), findsNWidgets(3)); // la de solo texto no se puede responder
      final h = DateTime.utc(2026, 10, 21, 11, 30).toLocal();
      final hora = '${h.hour.toString().padLeft(2, '0')}:${h.minute.toString().padLeft(2, '0')}';
      expect(find.textContaining('Principal · $hora'), findsOneWidget);
    });

    testWidgets('sin instantánea o sin decisiones lo dice', (tester) async {
      await abrir(tester, NativoFalso(), null);
      await tester.pumpAndSettle();
      expect(find.textContaining('Aún no ha llegado'), findsOneWidget);
      await abrir(tester, NativoFalso(), instantanea([]));
      await tester.pumpAndSettle();
      expect(find.text('Nada espera tu decisión.'), findsOneWidget);
    });

    testWidgets('responder pide confirmación y solo envía al confirmar', (tester) async {
      final nativo = NativoFalso();
      await abrir(tester, nativo, instantanea(espera.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Subir'));
      await tester.pumpAndSettle();
      expect(find.text('¿Responder "Subir" a Subir a producción?'), findsOneWidget);
      expect(nativo.llamadas, isEmpty);
      await tester.tap(find.text('Cancelar'));
      await tester.pumpAndSettle();
      expect(nativo.llamadas, isEmpty);
      await tester.tap(find.text('Subir'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirmar'));
      await tester.pumpAndSettle();
      expect(nativo.llamadas, ['responder d1 Subir | Subir a producción']);
      expect(find.text('Respondido: Subir'), findsOneWidget);
      expect(find.text('Esperar'), findsNothing); // respondida: sin botones
    });

    testWidgets('si falla el envío se muestra el motivo y siguen los botones', (tester) async {
      final nativo = NativoFalso(fallo: 'No hay conexión con tu Mac.');
      await abrir(tester, nativo, instantanea(espera.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Subir'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirmar'));
      await tester.pumpAndSettle();
      expect(find.textContaining('No se envió: No hay conexión con tu Mac.'), findsOneWidget);
      expect(find.text('Esperar'), findsOneWidget);
    });

    testWidgets('«Llámame» envía sin confirmación y marca el pedido', (tester) async {
      final nativo = NativoFalso();
      await abrir(tester, nativo, instantanea(espera.cast<Object>()));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Llámame').first);
      await tester.pumpAndSettle();
      expect(nativo.llamadas, ['llamame d1 | Subir a producción']);
      expect(find.text('Pedido: te llamarán'), findsOneWidget);
    });

    final largo =
        'Fusionar la rama fm/llamada-decisiones en main ahora que pasaron las pruebas, '
        'o esperar a que el capitán revise la instantánea del teléfono antes de instalar la versión nueva';

    testWidgets('un título largo se ve entero, sin recorte', (tester) async {
      await abrir(tester, NativoFalso(), instantanea([
        {'id': 'd9', 'titulo': largo},
      ]));
      await tester.pumpAndSettle();
      final texto = tester.widget<Text>(find.text(largo));
      expect(texto.maxLines, isNull);
      expect(texto.overflow, isNull);
    });

    testWidgets('con detalle se muestra el detalle y es lo que se envía, con el id', (tester) async {
      final nativo = NativoFalso();
      final t = instantanea([
        {'id': 'd9', 'titulo': 'Fusionar la rama…', 'detalle': largo, 'opciones': ['Fusionar']},
      ]);
      await abrir(tester, nativo, t);
      await tester.pumpAndSettle();
      expect(find.text(largo), findsOneWidget);
      expect(find.text('Fusionar la rama…'), findsNothing);
      await tester.tap(find.text('Llámame'));
      await tester.pumpAndSettle();
      expect(nativo.llamadas, ['llamame d9 | $largo']);

      final otro = NativoFalso();
      await abrir(tester, otro, t);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Fusionar'));
      await tester.pumpAndSettle();
      expect(find.text('¿Responder "Fusionar" a $largo?'), findsOneWidget);
      await tester.tap(find.text('Confirmar'));
      await tester.pumpAndSettle();
      expect(otro.llamadas, ['responder d9 Fusionar | $largo']);
    });

    group('detalle', () {
      final conDetalle = [
        {'id': 'd1', 'titulo': 'Subir a producción', 'opciones': ['Subir', 'Esperar']},
        {'id': 'd9', 'titulo': 'Fusionar la rama…', 'detalle': largo, 'opciones': ['Fusionar']},
        'Solo texto',
      ];

      testWidgets('tocar una decisión abre su detalle con el texto completo y sus acciones', (tester) async {
        final nativo = NativoFalso();
        await abrir(tester, nativo, instantanea(conDetalle.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text(largo));
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetalleScreen), findsOneWidget);
        final detalle = find.byType(DecisionDetalleScreen);
        expect(find.descendant(of: detalle, matching: find.text(largo)), findsOneWidget);
        expect(find.descendant(of: detalle, matching: find.text('Fusionar')), findsOneWidget);
        await tester.tap(find.descendant(of: detalle, matching: find.text('Fusionar')));
        await tester.pumpAndSettle();
        expect(find.text('¿Responder "Fusionar" a $largo?'), findsOneWidget);
        await tester.tap(find.text('Confirmar'));
        await tester.pumpAndSettle();
        expect(nativo.llamadas, ['responder d9 Fusionar | $largo']);
        expect(find.descendant(of: detalle, matching: find.text('Respondido: Fusionar')), findsOneWidget);
        // Al volver, la lista ya muestra lo respondido.
        await tester.pageBack();
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetalleScreen), findsNothing);
        expect(find.text('Respondido: Fusionar'), findsOneWidget);
      });

      testWidgets('«Llámame» desde el detalle envía el id y el texto completo', (tester) async {
        final nativo = NativoFalso();
        await abrir(tester, nativo, instantanea(conDetalle.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text(largo));
        await tester.pumpAndSettle();
        await tester.tap(find.descendant(of: find.byType(DecisionDetalleScreen), matching: find.text('Llámame')));
        await tester.pumpAndSettle();
        expect(nativo.llamadas, ['llamame d9 | $largo']);
        expect(find.text('Pedido: te llamarán'), findsOneWidget);
      });

      testWidgets('una decisión sin id abre su detalle sin acciones', (tester) async {
        await abrir(tester, NativoFalso(), instantanea(conDetalle.cast<Object>()));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Solo texto'));
        await tester.pumpAndSettle();
        final detalle = find.byType(DecisionDetalleScreen);
        expect(find.descendant(of: detalle, matching: find.text('Solo texto')), findsOneWidget);
        expect(find.descendant(of: detalle, matching: find.text('Llámame')), findsNothing);
      });

      testWidgets('desde el widget se abre directamente el detalle pedido (por id o por texto)', (tester) async {
        await abrir(tester, NativoFalso(), instantanea(conDetalle.cast<Object>()), abrirDecision: 'd9');
        await tester.pumpAndSettle();
        expect(find.descendant(of: find.byType(DecisionDetalleScreen), matching: find.text(largo)), findsOneWidget);
        await tester.pageBack();
        await tester.pumpAndSettle();
        expect(find.text('Subir a producción'), findsOneWidget); // debajo queda la lista

        await abrir(tester, NativoFalso(), instantanea(conDetalle.cast<Object>()), abrirDecision: 'Solo texto');
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetalleScreen), findsOneWidget);

        await abrir(tester, NativoFalso(), instantanea(conDetalle.cast<Object>()), abrirDecision: 'ya no está');
        await tester.pumpAndSettle();
        expect(find.byType(DecisionDetalleScreen), findsNothing);
        expect(find.text('Subir a producción'), findsOneWidget);
      });
    });

    testWidgets('instantánea de más de 2 h avisa de que puede no estar al día', (tester) async {
      await abrir(tester, NativoFalso(), instantanea(espera.cast<Object>(), '2026-10-21T08:00:00Z'));
      await tester.pumpAndSettle();
      expect(find.textContaining('puede no estar al día'), findsOneWidget);
    });
  });
}
