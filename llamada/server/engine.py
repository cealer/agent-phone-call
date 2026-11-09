"""Voice engine for one phone call: keeps the TTS voice (Qwen3-TTS) and Whisper loaded while the call lasts.

This is the default engine; any other program that speaks the same protocol can replace it
(LLAMADA_ENGINE / LLAMADA_ENGINE_PYTHON in server.py; engine_openai.py is a ready-made alternative).

Runs under the repository root venv (.venv). Protocol: one JSON object per line on stdin, one reply per
line on stdout. Logs go to stderr. Exits when stdin closes, which frees all model memory.

  {"op": "tts", "text": "...", "out": "/path.wav"}  -> {"ok": true, "out": "/path.wav"}
  {"op": "tts_stream", "text": "...", "out": "/prefix"} -> {"ok": true, "chunk": "/prefix.0.pcm"} ... {"ok": true, "final": true}
  {"op": "stt", "pcm": "/path.pcm"}                 -> {"ok": true, "text": "..."}
  (stt pcm = raw 16 kHz mono signed 16-bit little-endian; tts wav and tts_stream chunks = 48 kHz mono 16-bit)
Every request carries an "id" and every reply echoes it. The first line written is {"ok": true, "ready": true}
once the models are loaded (or {"ok": false, "error": ...}).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

os.environ["HF_HUB_OFFLINE"] = "1"

VOICE_TTS = Path(os.environ.get("VOICE_TTS_ROOT", Path(__file__).resolve().parents[2]))
# The voice: model, reference clip and settings (see voices/README.md). LLAMADA_VOICE points to another voice.json.
VOICE = Path(os.environ.get("LLAMADA_VOICE") or VOICE_TTS / "voices" / "my-voice" / "voice.json")
WHISPER = os.environ.get("LLAMADA_WHISPER", "mlx-community/whisper-large-v3-turbo")
LANGUAGE = os.environ.get("LLAMADA_WHISPER_LANGUAGE", "es")  # the language the user speaks
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
INITIAL_PROMPT = os.environ.get("LLAMADA_WHISPER_PROMPT") or (
    "Conversación telefónica en español entre una persona y su asistente de IA: firstmate, secondmate, "
    "llamada, Tailscale, Herdr, Claude.")
# Stock phrases Whisper invents on silence and noise (large-v3-turbo does it with no_speech = 0.0).
HALLUCINATION = re.compile(
    r"gracias por (ver|mirar) (el|este) v[ií]deo|suscr[ií]be|subt[ií]tulos|amara\.org|sincronizaci[oó]n\b",
    re.IGNORECASE)
SPEECH_TAG = re.compile(r"\[[A-Za-z][A-Za-z -]*\]|</?[A-Za-z][A-Za-z-]*>|<\s*>")


def log(message: str) -> None:
    print(f"[engine] {message}", file=sys.stderr, flush=True)


def clean(text: str) -> str:
    text = SPEECH_TAG.sub(" ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def repetitive(text: str) -> bool:
    """True for loops like "no, no, no, no, …" (one word making up most of a long text)."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < 8:
        return False
    most = max(words.count(w) for w in set(words))
    return most / len(words) > 0.5


# ---- streaming synthesis: the voice arrives in ~1 s chunks, finished here chunk by chunk ----
STREAM_GAIN = 4.8        # the voice is generated quiet (rms ~0.03); the whole-file path's loudnorm adds ~4.9x
STREAM_INTERVAL = 1.0    # seconds of audio per chunk: first sound ~0.7 s after the request
FADE_SECONDS = 0.08      # the generated speech can end abruptly: fade the last 80 ms (as the whole-file path does)
TAIL_SECONDS = 0.25      # a short silence after each phrase, also as the whole-file path does
OUT_RATE = 48000


def _half_band(taps: int = 31) -> np.ndarray:
    """Windowed-sinc low-pass at the original Nyquist, for 24 kHz -> 48 kHz."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(n / 2) * np.hamming(taps)
    return (h / h.sum() * 2).astype(np.float64)   # x2: zero-stuffing halves the level


class Upsampler2x:
    """24 kHz -> 48 kHz that carries its filter state across chunks, so joins are seamless."""

    def __init__(self) -> None:
        self.h = _half_band()
        self.history = np.zeros(len(self.h) - 1)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        stuffed = np.zeros(2 * len(chunk))
        stuffed[::2] = chunk
        joined = np.concatenate([self.history, stuffed])
        self.history = joined[-(len(self.h) - 1):]
        return np.convolve(joined, self.h, mode="valid")


class StreamFinisher:
    """Raw 24 kHz chunks in, finished 48 kHz int16 out: fixed gain, soft limit, and the last 80 ms
    held back so the end of the phrase can be faded once it is known to be the end."""

    def __init__(self) -> None:
        self.up = Upsampler2x()
        self.held = np.zeros(0)

    def push(self, chunk: np.ndarray) -> np.ndarray:
        y = np.tanh(self.up.process(np.asarray(chunk, dtype=np.float64) * STREAM_GAIN) / 0.95) * 0.95
        data = np.concatenate([self.held, y])
        keep = int(FADE_SECONDS * OUT_RATE)
        if len(data) <= keep:
            self.held = data
            return np.zeros(0, dtype="<i2")
        self.held = data[-keep:]
        return np.round(data[:-keep] * 32767).astype("<i2")

    def finish(self) -> np.ndarray:
        tail = self.held * np.linspace(1.0, 0.0, len(self.held), endpoint=False) if len(self.held) else self.held
        self.held = np.zeros(0)
        out = np.round(tail * 32767).astype("<i2")
        return np.concatenate([out, np.zeros(int(TAIL_SECONDS * OUT_RATE), dtype="<i2")])


def keep_segment(segment: dict) -> bool:
    """Whisper's own rule: silence needs BOTH high no-speech probability and low confidence, so
    short real phrases on phone audio (low confidence alone) are kept; loops and garbage are not."""
    if HALLUCINATION.search(segment["text"]):
        return False
    no_speech = segment.get("no_speech_prob", 0)
    logprob = segment.get("avg_logprob", 0)
    if no_speech >= 0.9 or (no_speech >= 0.6 and logprob < -1.0):
        return False
    return not (logprob < -2.0 or segment.get("compression_ratio", 0) > 2.4
                or repetitive(segment["text"].strip()))


class Engine:
    def __init__(self) -> None:
        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from mlx_audio.tts.utils import load_model

        mx.set_cache_limit(256 * 1024 * 1024)
        self.mx = mx
        self.settings = json.loads(VOICE.read_text(encoding="utf-8"))
        self.reference = VOICE.parent / self.settings["reference"]
        cached = snapshot_download(self.settings["model"], revision=self.settings["revision"],
                                   local_files_only=True)
        log("loading the TTS voice")
        self.tts = load_model(cached)
        log("warming up Whisper")
        import numpy as np
        self.transcribe_array(np.zeros(16000, dtype=np.float32))
        log("ready")

    def synthesize(self, text: str, out: Path) -> None:
        import numpy as np
        import soundfile as sf

        text = clean(text)
        if not text:
            raise ValueError("empty text")
        self.mx.random.seed(self.settings["seed"])
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tts-", dir=out.parent) as temporary:
            raw = Path(temporary) / "raw.wav"
            results = self.tts.generate(
                text=text, ref_audio=str(self.reference), ref_text=self.settings["reference_text"],
                lang_code=self.settings["language"], temperature=self.settings["temperature"],
                max_tokens=1024, verbose=False,
            )
            written = 0
            with sf.SoundFile(raw, "w", samplerate=24000, channels=1, subtype="PCM_16") as wav:
                for result in results:
                    audio = np.asarray(result.audio).reshape(-1)
                    if result.token_count >= 1024:
                        raise RuntimeError("generation hit the token limit")
                    wav.write(audio)
                    written += len(audio)
            if not written:
                raise RuntimeError("no audio generated")
            # Same finishing as the original command-line voice: loudness, fade the abrupt end, short tail.
            subprocess.run([
                FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(raw),
                "-af", "loudnorm=I=-18:TP=-1.5:LRA=11,areverse,afade=t=in:d=0.08,areverse,apad=pad_dur=0.25",
                "-ar", "48000", "-ac", "1", str(out),
            ], check=True, timeout=60)

    def synthesize_stream(self, text: str, prefix: Path, emit) -> None:
        """Generate in ~1 s chunks; each finished chunk (48 kHz int16 pcm) is written to
        <prefix>.<n>.pcm and announced through emit(path) as soon as it exists."""
        text = clean(text)
        if not text:
            raise ValueError("empty text")
        self.mx.random.seed(self.settings["seed"])
        prefix.parent.mkdir(parents=True, exist_ok=True)
        finisher, count = StreamFinisher(), 0

        def write(pcm: np.ndarray) -> None:
            nonlocal count
            if not len(pcm):
                return
            path = Path(f"{prefix}.{count}.pcm")
            count += 1
            path.write_bytes(pcm.tobytes())
            emit(path)

        for result in self.tts.generate(
                text=text, ref_audio=str(self.reference), ref_text=self.settings["reference_text"],
                lang_code=self.settings["language"], temperature=self.settings["temperature"],
                max_tokens=1024, verbose=False, stream=True, streaming_interval=STREAM_INTERVAL):
            write(finisher.push(np.asarray(result.audio).reshape(-1)))
        write(finisher.finish())
        if count <= 1:
            raise RuntimeError("no audio generated")

    def transcribe_array(self, audio) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio, path_or_hf_repo=WHISPER, language=LANGUAGE, temperature=0.0,
            condition_on_previous_text=False, no_speech_threshold=0.6,
            initial_prompt=INITIAL_PROMPT,
        )
        kept = []
        for segment in result.get("segments", []):
            text = segment["text"].strip()
            # Whisper hallucinates on noise and tones ("no, no, no, …"): drop unlikely speech.
            if not keep_segment(segment):
                log(f"dropped doubtful transcription: {text[:80]!r} no_speech={segment.get('no_speech_prob', 0):.2f} "
                    f"logprob={segment.get('avg_logprob', 0):.2f} compression={segment.get('compression_ratio', 0):.2f}")
                continue
            kept.append(text)
        return " ".join(kept).strip()

    def transcribe(self, pcm: Path) -> str:
        import numpy as np

        audio = np.frombuffer(pcm.read_bytes(), dtype="<i2").astype(np.float32) / 32768.0
        if audio.size < 8000:
            return ""
        return self.transcribe_array(audio)


def main() -> int:
    # Libraries print to stdout; keep the real stdout for the protocol only.
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def reply_with(obj) -> None:
        protocol.write(json.dumps(obj, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        engine = Engine()
    except Exception as exc:
        reply_with({"ok": False, "error": f"engine failed to load: {exc}"})
        return 1
    reply_with({"ok": True, "ready": True})
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if request["op"] == "tts":
                engine.synthesize(request["text"], Path(request["out"]))
                reply = {"ok": True, "out": request["out"]}
            elif request["op"] == "tts_stream":
                rid = request.get("id")
                engine.synthesize_stream(request["text"], Path(request["out"]),
                                         lambda path: reply_with({"ok": True, "id": rid, "chunk": str(path)}))
                reply = {"ok": True, "final": True}
            elif request["op"] == "stt":
                reply = {"ok": True, "text": engine.transcribe(Path(request["pcm"]))}
            else:
                reply = {"ok": False, "error": f"unknown op {request['op']}"}
        except Exception as exc:
            log(f"error: {exc}")
            reply = {"ok": False, "error": str(exc)}
        reply_with({**reply, "id": request.get("id")} if request else reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
