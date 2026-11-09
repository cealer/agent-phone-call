// The live call: WebRTC audio both ways with the Mac, signaling over a WebSocket.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'audio_route.dart';
import 'cierre_llamada.dart';
import 'config.dart';
import 'incoming.dart';
import 'texto_llamada.dart';
import 'ui.dart';

/// Set while a call screen is open, so the system call notification can hang it up.
VoidCallback? hangUpCurrentCall;

class CallScreen extends StatefulWidget {
  const CallScreen({
    super.key,
    required this.callId,
    this.name = 'Claude',
    this.incoming = true,
  });
  final String callId;
  final String name;
  final bool
  incoming; // rang on the lock screen (CallKit UI) vs dialed from the app

  @override
  State<CallScreen> createState() => _CallScreenState();
}

const _audio = MethodChannel('llamada/audio');

class _CallScreenState extends State<CallScreen> {
  WebSocketChannel? _ws;
  RTCPeerConnection? _pc;
  MediaStream? _mic;
  String _state = 'connecting';
  bool _speaker =
      true; // hands-free by default; the button switches to the earpiece
  bool _muted = false;
  bool _closed = false;
  // Everything the call takes from the phone, given back however it ends (see CierreLlamada).
  final _cierre = CierreLlamada();
  // The call in text: only in memory, only while this screen lives; never stored.
  final List<LineaLlamada> _lines = [];
  final _started = DateTime.now();
  Timer? _clock;

  @override
  void initState() {
    super.initState();
    WakelockPlus.enable();
    hangUpCurrentCall = () => _hangUp(notify: true);
    // Run at the end of every hang-up, and again if something is taken after it (hung up while connecting).
    _cierre.alTerminar('llamada del sistema', () async {
      await FlutterCallkitIncoming.endCall(uuidFor(widget.callId));
      await FlutterCallkitIncoming.endAllCalls();
    });
    _cierre.alTerminar(
      'audio del teléfono',
      () => _audio.invokeMethod<bool>('liberar', {'callId': uuidFor(widget.callId)}),
    );
    _cierre.alTerminar('pantalla encendida', WakelockPlus.disable);
    _clock = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
    _connect();
  }

  Future<void> _connect() async {
    try {
      // Call mode: phone echo cancellation, and audio routing that honours the speaker
      // button (some devices ignore routing outside inCommunication unless forced).
      await Helper.setAndroidAudioConfiguration(
        AndroidAudioConfiguration(
          manageAudioFocus: true,
          androidAudioMode: AndroidAudioMode.inCommunication,
          androidAudioFocusMode: AndroidAudioFocusMode.gain,
          androidAudioStreamType: AndroidAudioStreamType.voiceCall,
          androidAudioAttributesUsageType:
              AndroidAudioAttributesUsageType.voiceCommunication,
          androidAudioAttributesContentType:
              AndroidAudioAttributesContentType.speech,
          forceHandleAudioRouting: true,
        ),
      );
      try {
        _speaker = startOnSpeaker(
          (await Helper.audiooutputs).map((d) => d.deviceId),
        );
      } catch (_) {}
      final mic = await navigator.mediaDevices.getUserMedia({
        'audio': {
          'echoCancellation': true,
          'noiseSuppression': true,
          'autoGainControl': true,
        },
        'video': false,
      });
      _mic = mic;
      await _cierre.tomar('micrófono', () async {
        for (final track in mic.getTracks()) {
          await track.stop();
        }
        await mic.dispose();
      });
      if (_cierre.cerrada) return; // hung up while the microphone was opening: already given back
      final pc = await createPeerConnection({
        'iceServers': [],
        'sdpSemantics': 'unified-plan',
      });
      _pc = pc;
      // close() alone keeps WebRTC's audio session (call mode, audio focus): only dispose() lets go of it.
      await _cierre.tomar('conexión WebRTC', () async {
        try {
          await pc.close();
        } catch (_) {}
        await pc.dispose();
      });
      if (_cierre.cerrada) return;
      for (final track in mic.getAudioTracks()) {
        await pc.addTrack(track, mic);
      }
      pc.onConnectionState = (state) {
        if (state == RTCPeerConnectionState.RTCPeerConnectionStateConnected) {
          _applySpeaker(); // Android may reset routing as audio starts
        }
        if (state == RTCPeerConnectionState.RTCPeerConnectionStateFailed ||
            state == RTCPeerConnectionState.RTCPeerConnectionStateClosed) {
          _hangUp(notify: false);
        }
      };
      await _applySpeaker();

      final ws = WebSocketChannel.connect(wsUri(widget.callId));
      _ws = ws;
      await _cierre.tomar('socket', () => ws.sink.close());
      await ws.ready;
      if (_cierre.cerrada) return;
      ws.stream.listen(
        _onMessage,
        onDone: () => _hangUp(notify: false),
        onError: (_) => _hangUp(notify: false),
      );

      // Non-trickle ICE: wait for all candidates (Tailscale addresses included) then offer.
      final gathered = Completer<void>();
      pc.onIceGatheringState = (state) {
        if (state == RTCIceGatheringState.RTCIceGatheringStateComplete &&
            !gathered.isCompleted) {
          gathered.complete();
        }
      };
      await pc.setLocalDescription(
        await pc.createOffer({'offerToReceiveAudio': true}),
      );
      await gathered.future.timeout(
        const Duration(seconds: 3),
        onTimeout: () {},
      );
      if (_cierre.cerrada) return;
      final offer = await pc.getLocalDescription();
      ws.sink.add(jsonEncode({'type': 'offer', 'sdp': offer!.sdp}));
      _sendRoute();
      // Not after a hang-up: it would mark the ended system call as connected again.
      if (widget.incoming && !_cierre.cerrada) {
        await FlutterCallkitIncoming.setCallConnected(uuidFor(widget.callId));
      }
    } catch (error) {
      if (_cierre.cerrada) return; // hung up while connecting: nothing more to show
      // Most often the call already ended (answered too late, or a stale call).
      if (mounted) setState(() => _state = 'error');
      await Future.delayed(const Duration(milliseconds: 1500));
      _hangUp(notify: false);
    }
  }

  void _onMessage(dynamic raw) async {
    final data = jsonDecode(raw as String) as Map<String, dynamic>;
    switch (data['type']) {
      case 'answer':
        await _pc?.setRemoteDescription(
          RTCSessionDescription(data['sdp'] as String, 'answer'),
        );
      case 'state':
        if (mounted) setState(() => _state = data['value'] as String);
      case 'line':
        final line = lineaDesdeMensaje(data);
        if (line != null && mounted) setState(() => _lines.add(line));
      case 'end':
        _hangUp(notify: false);
    }
  }

  Future<void> _hangUp({required bool notify}) async {
    if (_closed) return;
    _closed = true;
    hangUpCurrentCall = null;
    _lines.clear();
    _clock?.cancel();
    if (notify) {
      try {
        _ws?.sink.add(jsonEncode({'type': 'hangup'}));
      } catch (_) {}
    }
    // Every step runs even if one fails or hangs: before, a throw here skipped the rest and left the
    // phone in call mode with the audio focus taken.
    await _cierre.cerrar();
    if (mounted) {
      Navigator.of(context).popUntil((route) => route.isFirst);
      // Answered from the lock screen: go back there. Dialed from the app: stay in it.
      if (widget.incoming) SystemNavigator.pop();
    }
  }

  void _toggleSpeaker() async {
    _speaker = !_speaker;
    await _applySpeaker();
    setState(() {});
  }

  /// Lock-screen calls live in Android Telecom, which owns their audio route; calls
  /// dialed from the app are plain WebRTC and route through the plugin.
  Future<void> _applySpeaker() async {
    var routed = false;
    if (widget.incoming) {
      try {
        routed =
            await _audio.invokeMethod<bool>('setSpeaker', {
              'callId': uuidFor(widget.callId),
              'on': _speaker,
            }) ??
            false;
      } catch (_) {}
    }
    if (!routed) await Helper.setSpeakerphoneOn(_speaker);
    _sendRoute();
  }

  void _sendRoute() {
    try {
      _ws?.sink.add(jsonEncode(routeMessage(speaker: _speaker)));
    } catch (_) {}
  }

  void _toggleMute() {
    _muted = !_muted;
    for (final track in _mic?.getAudioTracks() ?? <MediaStreamTrack>[]) {
      track.enabled = !_muted;
    }
    setState(() {});
  }

  @override
  void dispose() {
    _hangUp(notify: true);
    super.dispose();
  }

  String get _label => switch (_state) {
    'connecting' => 'Conectando…',
    'speaking' => 'Hablando',
    'listening' => 'Te escucho',
    'thinking' => 'Pensando…',
    'error' => 'La llamada ya terminó',
    _ => '',
  };

  String get _elapsed {
    final seconds = DateTime.now().difference(_started).inSeconds;
    return '${seconds ~/ 60}:${(seconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      child: Scaffold(
        body: CallLayout(
          name: widget.name,
          elapsed: _elapsed,
          state: _state,
          label: _label,
          hint: _state == 'speaking' ? 'Puedes interrumpirle hablando' : ' ',
          muted: _muted,
          speaker: _speaker,
          onMute: _toggleMute,
          onHangUp: () => _hangUp(notify: true),
          onSpeaker: _toggleSpeaker,
          lines: List.unmodifiable(_lines),
        ),
      ),
    );
  }
}
