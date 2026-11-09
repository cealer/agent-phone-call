"""engine_openai.py against a fake OpenAI-compatible API on localhost (needs ffmpeg).

  .venv/bin/python -m unittest test_engine_openai -v
"""
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import wave

HERE = Path(__file__).resolve().parent


def tone_wav(rate: int = 24000, seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x10" * int(rate * seconds))
    return buffer.getvalue()


class FakeApi(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802 - http.server naming
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeApi.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/v1/audio/speech":
            payload, content_type = tone_wav(), "audio/wav"
        elif self.path == "/v1/audio/transcriptions":
            payload, content_type = json.dumps({"text": " hola, ¿me escuchas? "}).encode(), "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class EngineOpenAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def run_engine(self, requests: list[dict]) -> list[dict]:
        env = dict(os.environ, LLAMADA_API_BASE=f"http://127.0.0.1:{self.httpd.server_port}/v1",
                   LLAMADA_API_KEY="test-key")
        lines = "".join(json.dumps(r) + "\n" for r in requests)
        result = subprocess.run([sys.executable, str(HERE / "engine_openai.py")], input=lines, env=env,
                                capture_output=True, text=True, timeout=60)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_speaks_transcribes_and_streams_in_the_engine_protocol(self):
        FakeApi.seen.clear()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pcm = folder / "in.pcm"
            pcm.write_bytes(b"\x00\x00" * 16000)
            replies = self.run_engine([
                {"id": 1, "op": "tts", "text": "Hola.", "out": str(folder / "out.wav")},
                {"id": 2, "op": "stt", "pcm": str(pcm)},
                {"id": 3, "op": "tts_stream", "text": "Hola.", "out": str(folder / "voz")},
            ])
            self.assertEqual(replies[0], {"ok": True, "ready": True})
            self.assertEqual(replies[1], {"ok": True, "out": str(folder / "out.wav"), "id": 1})
            with wave.open(str(folder / "out.wav")) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 1, 2))
            self.assertEqual(replies[2], {"ok": True, "text": "hola, ¿me escuchas?", "id": 2})
            self.assertEqual(replies[3], {"ok": True, "id": 3, "chunk": str(folder / "voz.0.pcm")})
            self.assertEqual(replies[4], {"ok": True, "final": True, "id": 3})
            # 0.5 s of 24 kHz audio + 0.25 s tail at 48 kHz, 2 bytes per sample
            self.assertEqual(len((folder / "voz.0.pcm").read_bytes()), int(0.75 * 48000) * 2)
        self.assertTrue(all(auth == "Bearer test-key" for _, auth, _ in FakeApi.seen))
        self.assertIn(b'name="language"\r\n\r\nes', FakeApi.seen[1][2])

    def test_a_failed_request_answers_an_error_and_the_engine_goes_on(self):
        replies = self.run_engine([{"id": 1, "op": "tts", "text": "  ", "out": "/tmp/x.wav"},
                                   {"id": 2, "op": "nope"}])
        self.assertEqual(replies[1], {"ok": False, "error": "empty text", "id": 1})
        self.assertEqual(replies[2], {"ok": False, "error": "unknown op nope", "id": 2})


if __name__ == "__main__":
    unittest.main()
