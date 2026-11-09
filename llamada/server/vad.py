"""Streaming turn detection on 16 kHz mono PCM16 with Silero VAD (like OpenAI's server_vad).

feed() returns events as they happen:
  ("start", None)   the user started talking (used for barge-in)
  ("stop", pcm)     the user stopped; pcm holds the utterance with a little lead-in
With pause_ms set, while he talks on (the turn still open):
  ("pause", None)   he has been quiet for pause_ms (once per pause): a natural moment for a nod
  ("resume", None)  his voice came back after a reported pause
"""
from pathlib import Path

import numpy as np
import onnxruntime

MODEL = Path(__file__).resolve().parent / "models" / "silero_vad.onnx"
RATE = 16000
WINDOW = 512            # samples per model step (32 ms)
CONTEXT = 64            # Silero v5 needs the previous 64 samples in front of each window
STEP_MS = WINDOW * 1000 // RATE

THRESHOLD = 0.5
START_MS = 250          # sustained speech before "start" (ignores clicks and short coughs)
END_SILENCE_MS = 800    # silence that ends the utterance
PREFIX_MS = 300         # audio kept from before "start" so first syllables reach Whisper
MAX_UTTERANCE_MS = 90_000
# While Claude talks the phone's echo control chops the user's voice into bursts, so a
# barge-in counts voice accumulated over a short window instead of requiring it unbroken.
BARGE_THRESHOLD = 0.35
BARGE_WINDOW_MS = 600
BARGE_VOICED_MS = 200

# While a nod of ours may be coming back through the microphone, only clear voice counts as his.
ECHO_THRESHOLD = 0.85

_session = None


def _model() -> onnxruntime.InferenceSession:
    global _session
    if _session is None:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        _session = onnxruntime.InferenceSession(str(MODEL), sess_options=options,
                                                providers=["CPUExecutionProvider"])
    return _session


class SileroVad:
    def __init__(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT), dtype=np.float32)
        self.sr = np.array(RATE, dtype=np.int64)

    def probability(self, window: np.ndarray) -> float:
        x = np.concatenate([self.context, window.reshape(1, -1)], axis=1)
        out, self.state = _model().run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[:, -CONTEXT:]
        return float(out[0][0])


class TurnDetector:
    def __init__(self, threshold: float = THRESHOLD, pause_ms: int | None = None) -> None:
        self.vad = SileroVad()
        self.pause_ms = pause_ms
        self.pause_reported = False
        self.echo_ms = 0                 # while > 0 the echo of audio we played may be in the microphone
        self.threshold = threshold
        self.pending = np.zeros(0, dtype=np.int16)
        self.history: list[np.ndarray] = []   # recent windows, for the lead-in
        self.utterance: list[np.ndarray] = []
        self.speaking = False
        self.voiced_ms = 0
        self.silence_ms = 0
        self.barge_mode = False          # set by the caller while Claude's audio plays
        self.recent: list[bool] = []     # barge-mode voiced flags, newest last
        self.recent_p: list[float] = []  # the same window's raw probabilities
        self.last_start: dict = {}       # how the last "start" fired, for the log

    @property
    def spoken_ms(self) -> int:
        """How long he has been talking in this turn (lead-in included, the trailing silence not)."""
        return len(self.utterance) * STEP_MS - self.silence_ms if self.speaking else 0

    def mask_echo(self, ms: int) -> None:
        """Audio of ours is going out: for `ms`, weak voice-like sound is taken for its echo, not for him."""
        self.echo_ms = max(self.echo_ms, ms)

    def feed(self, pcm: bytes) -> list[tuple[str, bytes | None]]:
        events = []
        self.pending = np.concatenate([self.pending, np.frombuffer(pcm, dtype="<i2")])
        while self.pending.size >= WINDOW:
            window = self.pending[:WINDOW]
            self.pending = self.pending[WINDOW:]
            event = self._step(window)
            if event:
                events.append(event)
        return events

    def _step(self, window: np.ndarray):
        probability = self.vad.probability(window.astype(np.float32) / 32768.0)
        voiced = probability >= self.threshold
        echo = self.echo_ms > 0 and probability < ECHO_THRESHOLD
        if self.echo_ms > 0:
            self.echo_ms -= STEP_MS
        if echo:
            voiced = False
            probability = 0.0
        if not self.speaking:
            self.history.append(window)
            keep = (PREFIX_MS + START_MS) // STEP_MS + 1
            del self.history[:-keep]
            self.voiced_ms = self.voiced_ms + STEP_MS if voiced else 0
            self.recent.append(probability >= BARGE_THRESHOLD)
            self.recent_p.append(round(probability, 2))
            del self.recent[:-(BARGE_WINDOW_MS // STEP_MS)]
            del self.recent_p[:-(BARGE_WINDOW_MS // STEP_MS)]
            barged = self.barge_mode and sum(self.recent) * STEP_MS >= BARGE_VOICED_MS
            if self.voiced_ms >= START_MS or barged:
                self.last_start = {"barged": barged and self.voiced_ms < START_MS,
                                   "voiced_ms": sum(self.recent) * STEP_MS,
                                   "max_p": max(self.recent_p, default=0.0)}
                self.recent = []
                self.recent_p = []
                self.speaking = True
                self.silence_ms = 0
                self.utterance = list(self.history)
                self.history = []
                return ("start", None)
            return None
        self.utterance.append(np.zeros_like(window) if echo else window)
        self.silence_ms = 0 if voiced else self.silence_ms + STEP_MS
        too_long = len(self.utterance) * STEP_MS >= MAX_UTTERANCE_MS
        if self.silence_ms >= END_SILENCE_MS or too_long:
            pcm = np.concatenate(self.utterance).astype("<i2").tobytes()
            self.speaking = False
            self.voiced_ms = 0
            self.utterance = []
            self.pause_reported = False
            return ("stop", pcm)
        if self.pause_ms:
            if voiced and self.pause_reported:
                self.pause_reported = False
                return ("resume", None)
            if not voiced and not self.pause_reported and self.silence_ms >= self.pause_ms:
                self.pause_reported = True
                return ("pause", None)
        return None
