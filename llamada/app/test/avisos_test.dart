import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/avisos.dart';

Map<String, dynamic> aviso([Map<String, dynamic> cambios = const {}]) => {
  'type': 'aviso',
  'id': 'reunion-42',
  'categoria': 'reunion',
  'titulo': 'Reunión en 10 minutos',
  'cuerpo': 'Daily con el equipo',
  'urgente': '1',
  ...cambios,
};

void main() {
  test('lee un aviso completo', () {
    final a = Aviso.parse(aviso())!;
    expect(a.id, 'reunion-42');
    expect(a.categoria, 'reunion');
    expect(a.titulo, 'Reunión en 10 minutos');
    expect(a.cuerpo, 'Daily con el equipo');
    expect(a.urgente, isTrue);
    expect(a.channelId, canalUrgente);
  });

  test('urgente ausente o distinto de 1 es normal', () {
    final sin = Aviso.parse(aviso()..remove('urgente'))!;
    expect(sin.urgente, isFalse);
    expect(sin.channelId, canalNormal);
    expect(Aviso.parse(aviso({'urgente': '0'}))!.urgente, isFalse);
  });

  test('categoría desconocida o ausente cae en resumen', () {
    expect(Aviso.parse(aviso({'categoria': 'otra'}))!.categoria, 'resumen');
    expect(Aviso.parse(aviso()..remove('categoria'))!.categoria, 'resumen');
  });

  test('sin id o sin texto no es un aviso', () {
    expect(Aviso.parse(aviso()..remove('id')), isNull);
    expect(Aviso.parse(aviso({'id': ' '})), isNull);
    expect(
      Aviso.parse(
        aviso()
          ..remove('titulo')
          ..remove('cuerpo'),
      ),
      isNull,
    );
  });

  test('con solo uno de título o cuerpo se muestra igual', () {
    final soloCuerpo = Aviso.parse(aviso()..remove('titulo'))!;
    expect(soloCuerpo.titulo, isNotEmpty);
    expect(Aviso.parse(aviso()..remove('cuerpo'))!.cuerpo, '');
  });

  test('otros tipos de mensaje se ignoran', () {
    expect(Aviso.parse({'type': 'incoming_call', 'call_id': 'x'}), isNull);
    expect(Aviso.parse({}), isNull);
  });

  test('el mismo id da siempre el mismo número de notificación', () {
    final a = Aviso.parse(aviso())!;
    expect(
      Aviso.parse(aviso({'titulo': 'otro'}))!.notificationId,
      a.notificationId,
    );
    expect(
      Aviso.parse(aviso({'id': 'otro'}))!.notificationId,
      isNot(a.notificationId),
    );
    expect(a.notificationId, inInclusiveRange(0, 0x7fffffff));
  });
}
