// Baked in at build time: flutter build apk --dart-define=SERVER_URL=... --dart-define=PHONE_TOKEN=...
const serverUrl = String.fromEnvironment(
  'SERVER_URL',
); // e.g. http://100.x.y.z:8771 (the Mac's Tailscale IP)
const phoneToken = String.fromEnvironment('PHONE_TOKEN');

Uri serverUri(String path, [Map<String, String>? query]) {
  final base = Uri.parse(serverUrl);
  return base.replace(
    path: path,
    queryParameters: {'token': phoneToken, ...?query},
  );
}

Uri wsUri(String callId) {
  final http = serverUri('/ws', {'call_id': callId});
  return http.replace(scheme: http.scheme == 'https' ? 'wss' : 'ws');
}

/// What the app hands to the native home-screen widgets; null when the URL or the token is missing (an APK built
/// without --dart-define), so a good configuration the widgets already have is not overwritten.
Map<String, String>? widgetConfig(String url, String token) =>
    url.trim().isEmpty || token.trim().isEmpty
    ? null
    : {'url': url, 'token': token};
