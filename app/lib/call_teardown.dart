// Hanging up gives the phone back: everything a call took (socket, WebRTC connection, microphone, the system
// call, the phone's call audio mode and audio focus) is released however the call ends.
import 'dart:async';

typedef Release = Future<void> Function();

/// What a call took from the phone, released when it ends.
///
/// - [take] registers something as it is taken; [close] releases it, last taken first.
/// - [onEnd] steps (end the system call, put the phone's audio back to normal, ...) run after that, always,
///   and again after anything taken once the call had already ended: hanging up while the call was still
///   connecting must not leave a microphone, or the call audio mode it switched on, behind.
/// - A step that fails or hangs is given up ([limit]) and reported ([onFailure]); the rest still run.
/// - Closing again (button, server's end, socket dropped, screen disposed) does nothing more.
class CallTeardown {
  CallTeardown({this.limit = const Duration(seconds: 2), this.onFailure});

  final Duration limit;
  final void Function(String step, Object error)? onFailure;

  final _taken = <(String, Release)>[];
  final _onEnd = <(String, Release)>[];
  Future<void>? _closing;

  bool get closed => _closing != null;

  void onEnd(String name, Release step) => _onEnd.add((name, step));

  /// Registers something the call took; if the call already ended, it is released right away.
  Future<void> take(String name, Release release) {
    final closing = _closing;
    if (closing == null) {
      _taken.add((name, release));
      return Future.value();
    }
    return _closing = closing.then((_) => _release([(name, release)]));
  }

  Future<void> close() => _closing ??= _release(_taken.reversed.toList());

  Future<void> _release(List<(String, Release)> taken) async {
    _taken.clear();
    for (final (name, step) in [...taken, ..._onEnd]) {
      try {
        await step().timeout(limit);
      } catch (error) {
        onFailure?.call(name, error);
      }
    }
  }
}
