import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/audio_route.dart';

void main() {
  test('starts on the loudspeaker when nothing is plugged in', () {
    expect(startOnSpeaker(['speaker', 'earpiece']), isTrue);
    expect(startOnSpeaker([]), isTrue);
  });

  test('uses the headphones when bluetooth or wired ones are connected', () {
    expect(startOnSpeaker(['speaker', 'earpiece', 'bluetooth']), isFalse);
    expect(startOnSpeaker(['wired-headset', 'speaker']), isFalse);
  });

  test('tells the server where the sound goes, so it only nods where there is no echo', () {
    expect(routeMessage(speaker: true), {'type': 'route', 'speaker': true});
    expect(routeMessage(speaker: false), {'type': 'route', 'speaker': false});
  });
}
