"""Alternative voice engine: speech-to-text and text-to-speech through any OpenAI-compatible HTTP API.

Use it instead of engine.py when you would rather not run Whisper and Qwen3-TTS on this Mac, or when you
want another model. It speaks the same JSON-lines protocol as engine.py (see its docstring), so the server
does not change. Standard library + ffmpeg only, so it runs under the server's own venv:

  PHONECALL_ENGINE=server/engine_openai.py
  PHONECALL_ENGINE_PYTHON=server/.venv/bin/python

Settings (environment variables, read by the server process that launches this engine):
  PHONECALL_API_BASE     base URL ending in /v1 (default https://api.openai.com/v1). A local server works
                       too, e.g. http://127.0.0.1:8000/v1 for speaches, LocalAI or Kokoro-FastAPI.
  PHONECALL_API_KEY      bearer key, if the API needs one (a local server usually does not)
  PHONECALL_STT_MODEL    default whisper-1
  PHONECALL_TTS_MODEL    default tts-1
  PHONECALL_TTS_VOICE    default alloy
  PHONECALL_LANGUAGE     the language the user speaks: en (default) or es (see language.py)

A cloud API bills every request; with a local server nothing leaves the machine and nothing is billed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import wave

API_BASE = os.environ.get("PHONECALL_API_BASE", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.environ.get("PHONECALL_API_KEY", "")
STT_MODEL = os.environ.get("PHONECALL_STT_MODEL", "whisper-1")
TTS_MODEL = os.environ.get("PHONECALL_TTS_MODEL", "tts-1")
TTS_VOICE = os.environ.get("PHONECALL_TTS_VOICE", "alloy")
LANGUAGE = (os.environ.get("PHONECALL_LANGUAGE") or "en").strip().lower()
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
TIMEOUT = 60
OUT_RATE = 48000
TAIL_SECONDS = 0.25  # a short silence after each phrase, as engine.py does


def log(message: str) -> None:
    print(f"[engine_openai] {message}", file=sys.stderr, flush=True)


def _post(path: str, body: bytes, content_type: str) -> bytes:
    headers = {"Content-Type": content_type}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    request = urllib.request.Request(f"{API_BASE}{path}", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _multipart(fields: dict[str, str], filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(pcm_path: Path) -> str:
    """Raw 16 kHz mono int16 in, text out."""
    pcm = pcm_path.read_bytes()
    if len(pcm) < 16000:  # under half a second: nothing worth sending
        return ""
    wav_path = pcm_path.with_suffix(".stt.wav")
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    try:
        body, content_type = _multipart({"model": STT_MODEL, "language": LANGUAGE, "response_format": "json"},
                                        "audio.wav", wav_path.read_bytes(), "audio/wav")
        return json.loads(_post("/audio/transcriptions", body, content_type)).get("text", "").strip()
    finally:
        wav_path.unlink(missing_ok=True)


def _speech(text: str) -> bytes:
    body = json.dumps({"model": TTS_MODEL, "voice": TTS_VOICE, "input": text, "response_format": "wav"}).encode()
    return _post("/audio/speech", body, "application/json")


def _convert(audio: bytes, args: list[str]) -> bytes:
    """Any audio the API returned -> 48 kHz mono 16-bit with a short silent tail, in the format args ask for."""
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0",
         "-af", f"apad=pad_dur={TAIL_SECONDS}", "-ar", str(OUT_RATE), "-ac", "1", *args],
        input=audio, capture_output=True, check=True, timeout=60)
    return result.stdout


def synthesize(text: str, out: Path) -> None:
    text = text.strip()
    if not text:
        raise ValueError("empty text")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_convert(_speech(text), ["-f", "wav", "-acodec", "pcm_s16le", "pipe:1"]))


def synthesize_stream(text: str, prefix: Path, emit) -> None:
    """Not truly streamed: the whole phrase comes back as one chunk (fine for short phrases)."""
    text = text.strip()
    if not text:
        raise ValueError("empty text")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    path = Path(f"{prefix}.0.pcm")
    path.write_bytes(_convert(_speech(text), ["-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]))
    emit(path)


def main() -> int:
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def reply_with(obj) -> None:
        protocol.write(json.dumps(obj, ensure_ascii=False) + "\n")
        protocol.flush()

    log(f"using {API_BASE} (stt {STT_MODEL}, tts {TTS_MODEL}/{TTS_VOICE})")
    reply_with({"ok": True, "ready": True})
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if request["op"] == "tts":
                synthesize(request["text"], Path(request["out"]))
                reply = {"ok": True, "out": request["out"]}
            elif request["op"] == "tts_stream":
                rid = request.get("id")
                synthesize_stream(request["text"], Path(request["out"]),
                                  lambda path: reply_with({"ok": True, "id": rid, "chunk": str(path)}))
                reply = {"ok": True, "final": True}
            elif request["op"] == "stt":
                reply = {"ok": True, "text": transcribe(Path(request["pcm"]))}
            else:
                reply = {"ok": False, "error": f"unknown op {request['op']}"}
        except Exception as exc:  # noqa: BLE001 - one failed request must not kill the engine
            log(f"error: {type(exc).__name__}: {exc}")
            reply = {"ok": False, "error": str(exc)}
        reply_with({**reply, "id": request.get("id")} if request else reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
