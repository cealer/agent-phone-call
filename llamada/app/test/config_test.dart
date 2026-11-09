import 'package:flutter_test/flutter_test.dart';
import 'package:llamada/config.dart';

void main() {
  test('la config del widget solo sale con URL y token', () {
    expect(configParaWidget('http://mac:8771', 'abc'), {
      'url': 'http://mac:8771',
      'token': 'abc',
    });
    // Un APK compilado sin --dart-define no debe borrar la que ya tiene el widget.
    expect(configParaWidget('', 'abc'), isNull);
    expect(configParaWidget('http://mac:8771', ''), isNull);
    expect(configParaWidget(' ', ' '), isNull);
  });
}
