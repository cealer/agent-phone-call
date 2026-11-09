import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/config.dart';

void main() {
  test('the widget config is only handed over with a URL and a token', () {
    expect(widgetConfig('http://mac:8771', 'abc'), {
      'url': 'http://mac:8771',
      'token': 'abc',
    });
    // An APK built without --dart-define must not erase the one the widget already has.
    expect(widgetConfig('', 'abc'), isNull);
    expect(widgetConfig('http://mac:8771', ''), isNull);
    expect(widgetConfig(' ', ' '), isNull);
  });
}
