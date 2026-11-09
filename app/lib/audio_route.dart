/// Output ids as flutter_webrtc reports them on Android ("bluetooth", "wired-headset", "speaker", "earpiece").
/// Hands-free is the default, except when headphones are connected: forcing the loudspeaker
/// would send the call's sound there instead of to them.
bool startOnSpeaker(Iterable<String> outputIds) =>
    !outputIds.any((id) => id == 'bluetooth' || id == 'wired-headset');

/// What the app tells the server about the output in use (the loudspeaker or not).
Map<String, dynamic> routeMessage({required bool speaker}) => {'type': 'route', 'speaker': speaker};
