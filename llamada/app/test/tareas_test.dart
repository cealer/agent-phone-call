import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/tareas.dart';

final ahora = DateTime.utc(2026, 10, 21, 12);

Map<String, dynamic> mensaje([
  Object? tareas,
  String? generado = '2026-10-21T11:30:00Z',
]) => {
  'type': 'tareas',
  'generado': ?generado,
  'tareas': tareas is String ? tareas : jsonEncode(tareas),
};

void main() {
  test('lee las tres listas y la hora de la instantánea', () {
    final t = Tareas.parse(
      mensaje({
        'marcha': ['a', 'b'],
        'espera': ['c'],
        'cola': ['d', 'e', 'f'],
      }),
      ahora,
    )!;
    expect(t.marcha, ['a', 'b']);
    expect(t.espera.map((d) => d.titulo), ['c']);
    expect(t.cola, ['d', 'e', 'f']);
    expect(t.generado, DateTime.utc(2026, 10, 21, 11, 30));
  });

  test('JSON inválido o que no es un objeto no es una instantánea', () {
    expect(Tareas.parse(mensaje('{no es json'), ahora), isNull);
    expect(Tareas.parse(mensaje('[1,2]'), ahora), isNull);
    expect(Tareas.parse(mensaje('"texto"'), ahora), isNull);
    expect(Tareas.parse({'type': 'tareas', 'generado': 'x'}, ahora), isNull);
  });

  test('listas ausentes o mal formadas quedan vacías', () {
    final t = Tareas.parse(
      mensaje({
        'marcha': 'no es lista',
        'cola': [1, null, '  ', ' ok '],
      }),
      ahora,
    )!;
    expect(t.marcha, isEmpty);
    expect(t.espera, isEmpty);
    expect(t.cola, ['ok']);
    expect(Tareas.parse(mensaje(<String, Object>{}), ahora)!.cola, isEmpty);
  });

  test('sin hora válida se usa la de llegada', () {
    expect(Tareas.parse(mensaje({}, null), ahora)!.generado, ahora);
    expect(Tareas.parse(mensaje({}, 'ayer'), ahora)!.generado, ahora);
  });

  test('otros tipos de mensaje se ignoran', () {
    expect(Tareas.parse({'type': 'aviso', 'tareas': '{}'}, ahora), isNull);
    expect(Tareas.parse({}, ahora), isNull);
  });

  test('caduca a las 2 horas', () {
    final t = Tareas.parse(mensaje({}, '2026-10-21T10:00:00Z'), ahora)!;
    expect(t.caducada(ahora), isFalse); // justo 2 h: todavía vale
    expect(t.caducada(ahora.add(const Duration(minutes: 1))), isTrue);
  });

  test('el JSON para los widgets conserva todo', () {
    final t = Tareas.parse(
      mensaje({
        'marcha': ['a'],
        'espera': ['b'],
        'cola': ['c'],
      }),
      ahora,
    )!;
    final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
    expect(j['generado'], '2026-10-21T11:30:00.000Z');
    expect(
      [j['marcha'], j['espera'], j['cola']],
      [
        ['a'],
        ['b'],
        ['c'],
      ],
    );
  });

  group('decisiones con id', () {
    Decision unica(Object? elemento) => Tareas.parse(
      mensaje({
        'espera': [elemento],
      }),
      ahora,
    )!.espera.single;

    test('una cadena es solo título, sin id ni botones', () {
      final d = unica('  Aprobar deploy  ');
      expect(d.titulo, 'Aprobar deploy');
      expect(d.id, isNull);
      expect(d.respondible, isFalse);
      expect(d.opciones, isEmpty);
    });

    test('un objeto con id admite respuesta', () {
      final d = unica({
        'id': 'd7',
        'titulo': 'Subir a producción',
        'opciones': ['Subir', 'Esperar'],
      });
      expect(d.id, 'd7');
      expect(d.titulo, 'Subir a producción');
      expect(d.opciones, ['Subir', 'Esperar']);
      expect(d.respondible, isTrue);
    });

    test('sin opciones (o vacías o mal formadas) son Sí y No', () {
      expect(unica({'id': 'a', 'titulo': 't'}).opciones, ['Sí', 'No']);
      expect(unica({'id': 'a', 'titulo': 't', 'opciones': []}).opciones, [
        'Sí',
        'No',
      ]);
      expect(unica({'id': 'a', 'titulo': 't', 'opciones': 'Sí'}).opciones, [
        'Sí',
        'No',
      ]);
      expect(
        unica({
          'id': 'a',
          'titulo': 't',
          'opciones': [1, ' '],
        }).opciones,
        ['Sí', 'No'],
      );
    });

    test('más de 3 opciones se recortan a las 3 primeras', () {
      final d = unica({
        'id': 'a',
        'titulo': 't',
        'opciones': ['uno', 'dos', 'tres', 'cuatro', 'cinco'],
      });
      expect(d.opciones, ['uno', 'dos', 'tres']);
    });

    test('objeto sin id se muestra sin botones; sin título se descarta', () {
      final sinId = unica({
        'titulo': 'solo texto',
        'opciones': ['Sí'],
      });
      expect(sinId.respondible, isFalse);
      expect(sinId.titulo, 'solo texto');
      expect(
        Tareas.parse(
          mensaje({
            'espera': [
              {'id': 'a'},
              {'id': 'b', 'titulo': ' '},
              5,
            ],
          }),
          ahora,
        )!.espera,
        isEmpty,
      );
    });

    test('mezcla de cadenas y objetos conserva el orden', () {
      final t = Tareas.parse(
        mensaje({
          'espera': [
            'a',
            {'id': 'x', 'titulo': 'b'},
          ],
        }),
        ahora,
      )!;
      expect(t.espera.map((d) => d.titulo), ['a', 'b']);
      expect(t.espera.map((d) => d.id), [null, 'x']);
    });

    test(
      'el JSON para los widgets: cadena u objeto con opciones ya resueltas',
      () {
        final t = Tareas.parse(
          mensaje({
            'espera': [
              'a',
              {'id': 'x', 'titulo': 'b'},
            ],
          }),
          ahora,
        )!;
        final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
        expect(j['espera'], [
          'a',
          {
            'id': 'x',
            'titulo': 'b',
            'opciones': ['Sí', 'No'],
          },
        ]);
      },
    );
  });

  group('detalle opcional', () {
    Decision unica(Object? elemento) => Tareas.parse(
      mensaje({
        'espera': [elemento],
      }),
      ahora,
    )!.espera.single;

    test('con detalle, el texto completo es el detalle', () {
      final d = unica({
        'id': 'd1',
        'titulo': 'Subir a producción',
        'detalle': '  Subir a producción la versión 2.3 (migración de la base incluida)  ',
      });
      expect(d.titulo, 'Subir a producción');
      expect(d.detalle, 'Subir a producción la versión 2.3 (migración de la base incluida)');
      expect(d.texto, 'Subir a producción la versión 2.3 (migración de la base incluida)');
    });

    test('sin detalle (ausente, en blanco o no texto) el texto completo es el título', () {
      for (final detalle in [null, '  ', 5]) {
        final d = unica({'id': 'd1', 'titulo': 'Subir', 'detalle': ?detalle});
        expect(d.detalle, isNull);
        expect(d.texto, 'Subir');
      }
      expect(unica('Solo texto').texto, 'Solo texto');
    });

    test('una decisión sin id también conserva su detalle', () {
      final d = unica({'titulo': 'Aviso', 'detalle': 'Aviso largo'});
      expect(d.respondible, isFalse);
      expect(d.texto, 'Aviso largo');
    });

    test('el JSON para los widgets lleva el detalle solo si existe', () {
      final t = Tareas.parse(
        mensaje({
          'espera': [
            {'id': 'x', 'titulo': 'b', 'detalle': 'b completo'},
            {'titulo': 'c', 'detalle': 'c completo'},
          ],
        }),
        ahora,
      )!;
      final j = jsonDecode(t.toJsonText()) as Map<String, dynamic>;
      expect(j['espera'], [
        {
          'id': 'x',
          'titulo': 'b',
          'detalle': 'b completo',
          'opciones': ['Sí', 'No'],
        },
        {'titulo': 'c', 'detalle': 'c completo'},
      ]);
      final leida = Tareas.fromStored(t.toJsonText())!;
      expect(leida.espera.map((d) => d.texto), ['b completo', 'c completo']);
    });
  });

  group('textos enviados a /hablar', () {
    test('respuesta', () {
      expect(
        textoRespuestaDecision('d7', 'Sí', 'Subir a producción'),
        '[widget-decision] id=d7 respuesta=Sí | El capitán responde «Sí» a la decisión: Subir a producción',
      );
    });

    test('llámame', () {
      expect(
        textoLlamameDecision('d7', 'Subir a producción'),
        '[widget-llamame] id=d7 | El capitán pide que lo llames para explicarle la decisión: Subir a producción',
      );
    });
  });
}
