// Baked in at build time: flutter build apk --dart-define=SERVER_URL=... --dart-define=PHONE_TOKEN=...
const serverUrl = String.fromEnvironment(
  'SERVER_URL',
); // e.g. http://mac.tailnet.ts.net:8771
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

/// Lo que la app deja a los widgets nativos; null si falta la URL o el token (APK compilado sin --dart-define),
/// para no pisar la configuración buena que ya tengan.
Map<String, String>? configParaWidget(String url, String token) =>
    url.trim().isEmpty || token.trim().isEmpty
    ? null
    : {'url': url, 'token': token};
