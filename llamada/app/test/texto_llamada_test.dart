import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/texto_llamada.dart';
import 'package:llamada/ui.dart';

List<LineaLlamada> lineas(int n) => [
  for (var i = 0; i < n; i++)
    LineaLlamada(
      delAgente: i.isEven,
      texto: 'Línea $i: una frase bastante larga para que ocupe sitio en la pantalla de la llamada.',
    ),
];

Widget pantalla(List<LineaLlamada> l) => MaterialApp(
  theme: llamadaTheme(),
  home: Scaffold(
    body: SizedBox(height: 400, child: TextoLlamada(lineas: l, nombre: 'Firstmate')),
  ),
);

// The chat's own Scrollable (each selectable line has one inside too).
Finder chat() => find.byType(Scrollable).first;

ScrollPosition posicion(WidgetTester tester) =>
    tester.state<ScrollableState>(chat()).position;

void main() {
  group('lineaDesdeMensaje', () {
    test('lo que dice el agente y lo que dijo él, distinguidos', () {
      expect(
        lineaDesdeMensaje({'type': 'line', 'who': 'agent', 'text': 'Ya está.'}),
        const LineaLlamada(delAgente: true, texto: 'Ya está.'),
      );
      expect(
        lineaDesdeMensaje({'type': 'line', 'who': 'user', 'text': 'Vale, gracias.'}),
        const LineaLlamada(delAgente: false, texto: 'Vale, gracias.'),
      );
    });

    test('ignora otros mensajes, quien desconocido y texto vacío', () {
      expect(lineaDesdeMensaje({'type': 'state', 'value': 'speaking'}), isNull);
      expect(lineaDesdeMensaje({'type': 'line', 'who': 'otro', 'text': 'x'}), isNull);
      expect(lineaDesdeMensaje({'type': 'line', 'who': 'agent', 'text': '  '}), isNull);
      expect(lineaDesdeMensaje({'type': 'line', 'who': 'agent'}), isNull);
    });
  });

  group('TextoLlamada', () {
    testWidgets('muestra cada línea y quién la dijo', (tester) async {
      await tester.pumpWidget(
        pantalla(const [
          LineaLlamada(delAgente: true, texto: 'La doce ya está lista.'),
          LineaLlamada(delAgente: false, texto: '¿Y la diecisiete?'),
        ]),
      );
      expect(find.text('La doce ya está lista.'), findsOneWidget);
      expect(find.text('¿Y la diecisiete?'), findsOneWidget);
      expect(find.text('Firstmate'), findsOneWidget);
      expect(find.text('Tú'), findsOneWidget);
    });

    testWidgets('baja sola al final cuando llega una línea nueva', (tester) async {
      await tester.pumpWidget(pantalla(lineas(12)));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, posicion(tester).maxScrollExtent);
      await tester.pumpWidget(pantalla(lineas(13)));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, posicion(tester).maxScrollExtent);
      expect(find.textContaining('Línea 12:'), findsOneWidget);
    });

    testWidgets('si él subió a releer no lo mueve; un aviso lo lleva al final', (tester) async {
      await tester.pumpWidget(pantalla(lineas(12)));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, 300));
      await tester.pumpAndSettle();
      final leyendo = posicion(tester).pixels;
      expect(leyendo, lessThan(posicion(tester).maxScrollExtent));
      expect(find.text('Nuevo'), findsNothing);

      await tester.pumpWidget(pantalla(lineas(13)));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, leyendo);
      expect(find.text('Nuevo'), findsOneWidget);

      await tester.tap(find.text('Nuevo'));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, posicion(tester).maxScrollExtent);
      expect(find.text('Nuevo'), findsNothing);

      await tester.pumpWidget(pantalla(lineas(14)));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, posicion(tester).maxScrollExtent);
    });

    testWidgets('volver abajo a mano reanuda el seguimiento', (tester) async {
      await tester.pumpWidget(pantalla(lineas(12)));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, 300));
      await tester.pumpAndSettle();
      await tester.drag(chat(), const Offset(0, -2000));
      await tester.pumpAndSettle();
      await tester.pumpWidget(pantalla(lineas(13)));
      await tester.pumpAndSettle();
      expect(posicion(tester).pixels, posicion(tester).maxScrollExtent);
      expect(find.text('Nuevo'), findsNothing);
    });
  });

  group('CallLayout', () {
    Widget llamada(List<LineaLlamada> l) => MaterialApp(
      theme: llamadaTheme(),
      home: Scaffold(
        body: CallLayout(
          name: 'Firstmate',
          elapsed: '1:02',
          state: 'speaking',
          label: 'Hablando',
          hint: ' ',
          muted: false,
          speaker: true,
          onMute: () {},
          onHangUp: () {},
          onSpeaker: () {},
          lines: l,
        ),
      ),
    );

    void telefono(WidgetTester tester) {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.625;
      addTearDown(tester.view.reset);
    }

    testWidgets('sin líneas no hay texto de la conversación', (tester) async {
      telefono(tester);
      await tester.pumpWidget(llamada(const []));
      expect(find.byType(TextoLlamada), findsNothing);
      expect(find.text('Colgar'), findsOneWidget);
    });

    testWidgets('con líneas la conversación se lee en la pantalla de llamada', (tester) async {
      telefono(tester);
      await tester.pumpWidget(llamada(lineas(20)));
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.byType(TextoLlamada), findsOneWidget);
      expect(find.textContaining('Línea 19:'), findsOneWidget);
      expect(find.text('Colgar'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
