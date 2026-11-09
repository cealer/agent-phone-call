import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:agent_phone_call/call_teardown.dart';

void main() {
  test('hanging up releases everything the call took, last taken first', () async {
    final done = <String>[];
    final teardown = CallTeardown();
    for (final name in ['phone audio', 'microphone', 'WebRTC connection', 'socket']) {
      await teardown.take(name, () async => done.add(name));
    }
    await teardown.close();
    expect(done, ['socket', 'WebRTC connection', 'microphone', 'phone audio']);
  });

  test('a failing step does not stop the others from being released, and it is reported', () async {
    final done = <String>[];
    final failures = <String>[];
    final teardown = CallTeardown(onFailure: (step, _) => failures.add(step));
    await teardown.take('phone audio', () async => done.add('phone audio'));
    await teardown.take('WebRTC connection', () async => throw StateError('already closed'));
    await teardown.take('socket', () async => done.add('socket'));
    await teardown.close();
    expect(done, ['socket', 'phone audio']);
    expect(failures, ['WebRTC connection']);
  });

  test('a step that hangs is given up after the limit and the others are still released', () async {
    final done = <String>[];
    final failures = <String>[];
    final teardown = CallTeardown(
      limit: const Duration(milliseconds: 50),
      onFailure: (step, _) => failures.add(step),
    );
    await teardown.take('phone audio', () async => done.add('phone audio'));
    await teardown.take('socket', () => Completer<void>().future); // the server never answers the close
    await teardown.close().timeout(const Duration(seconds: 1));
    expect(done, ['phone audio']);
    expect(failures, ['socket']);
  });

  test('hanging up twice (button, server end, dropped socket) releases only once', () async {
    var times = 0;
    final teardown = CallTeardown();
    await teardown.take('microphone', () async => times++);
    await Future.wait([teardown.close(), teardown.close()]);
    await teardown.close();
    expect(times, 1);
    expect(teardown.closed, isTrue);
  });

  test('what is taken after hanging up (hung up while connecting) is released at once, '
      'and the phone audio is put back to normal behind it', () async {
    final done = <String>[];
    final teardown = CallTeardown();
    teardown.onEnd('phone audio', () async => done.add('phone audio'));
    await teardown.take('socket', () async => done.add('socket'));
    await teardown.close();
    expect(done, ['socket', 'phone audio']);
    // getUserMedia returned after the hang-up: the microphone (and the call mode it turned on) is not left taken.
    await teardown.take('microphone', () async => done.add('microphone'));
    expect(done, ['socket', 'phone audio', 'microphone', 'phone audio']);
  });

  test('the end steps always run after releasing what was taken, even when registered first', () async {
    final done = <String>[];
    final teardown = CallTeardown();
    teardown.onEnd('system call', () async => done.add('system call'));
    teardown.onEnd('phone audio', () async => done.add('phone audio'));
    await teardown.take('microphone', () async => done.add('microphone'));
    await teardown.take('WebRTC connection', () async => throw StateError('failed'));
    await teardown.close();
    expect(done, ['microphone', 'system call', 'phone audio']);
  });
}
