"""Tests: speech splitting, Silero turn detection, and full real-time calls with a simulated
WebRTC phone (normal turn, barge-in, talking while Claude thinks, hang-up).

The call tests use the real voice engine (TTS voice + Whisper): ~1-2 minutes in total.
Run (from llamada/server, macOS with ffmpeg): .venv/bin/python -m unittest -v
"""
import asyncio
import fractions
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

TMP = tempfile.mkdtemp(prefix="llamada-test-")
os.environ["LLAMADA_CONFIG"] = TMP

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription  # noqa: E402
import av  # noqa: E402

import server  # noqa: E402
import vad  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_speech_cache: dict[tuple[str, int], np.ndarray] = {}


def speech(text: str, rate: int) -> np.ndarray:
    """Real speech (macOS `say`, a Spanish system voice; LLAMADA_TEST_VOICE picks another) as int16 mono at `rate`."""
    key = (text, rate)
    if key not in _speech_cache:
        with tempfile.TemporaryDirectory() as folder:
            source, wav = Path(folder) / "a.txt", Path(folder) / "a.aiff"
            source.write_text(text, encoding="utf-8")
            subprocess.run(["say", "-v", os.environ.get("LLAMADA_TEST_VOICE", "Paulina"), "-f", str(source),
                            "-o", str(wav)], check=True, capture_output=True)
            raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-ar", str(rate),
                                  "-ac", "1", "-f", "s16le", "-"], check=True, capture_output=True).stdout
        _speech_cache[key] = np.frombuffer(raw, dtype="<i2")
    return _speech_cache[key]


def noise(seconds: float, db: float, rate: int = 16000) -> np.ndarray:
    amplitude = 32768 * 10 ** (db / 20)
    return np.random.default_rng(0).normal(0, amplitude, int(seconds * rate)).astype("<i2")


class SplitTests(unittest.TestCase):
    def test_short_text_is_one_piece(self):
        self.assertEqual(server.split_for_speech("Hola, Ana. ¿Me escuchas?"), ["Hola, Ana. ¿Me escuchas?"])

    def test_long_text_splits_at_sentences_within_limit(self):
        text = " ".join(f"Esta es la oración número {i} y tiene algo de contenido." for i in range(12))
        pieces = server.split_for_speech(text)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= server.CHUNK_CHARS for p in pieces))
        self.assertEqual(" ".join(pieces), text)

    def test_first_piece_is_short_so_the_voice_starts_sooner(self):
        sentence = "Esta es una oración de tamaño normal que dice bastante cosas."
        pieces = server.split_for_speech(" ".join([sentence] * 5))
        self.assertLessEqual(len(pieces[0]), server.FIRST_CHUNK_CHARS)
        self.assertEqual(pieces[0], sentence)
        self.assertTrue(all(len(p) <= server.CHUNK_CHARS for p in pieces))
        self.assertEqual(" ".join(pieces), " ".join([sentence] * 5))

    def test_first_piece_of_a_long_sentence_breaks_at_a_comma(self):
        text = ("Listo, terminé de revisar el servidor, actualicé la aplicación y reinstalé el teléfono "
                "por el cable sin ningún problema")
        pieces = server.split_for_speech(text)
        self.assertLessEqual(len(pieces[0]), server.FIRST_CHUNK_CHARS)
        self.assertEqual(" ".join(pieces), text)

    def test_unbroken_word_does_not_hang(self):
        self.assertTrue(server.split_for_speech("a" * 500))

    def test_heard_prefix(self):
        self.assertEqual(server.heard_prefix("uno dos tres cuatro", 0.5), "uno dos")
        self.assertEqual(server.heard_prefix("uno dos", 0), "")


class TurnDetectorTests(unittest.TestCase):
    def test_speech_gives_start_then_stop_with_audio(self):
        detector = vad.TurnDetector()
        voice = speech("Sí, apruébalo.", 16000)
        events = detector.feed((np.concatenate([noise(0.5, -60), voice, noise(1.2, -60)])).tobytes())
        self.assertEqual([e[0] for e in events], ["start", "stop"])
        self.assertGreater(len(events[1][1]), len(voice))  # utterance includes the lead-in

    def test_noise_is_not_speech(self):
        detector = vad.TurnDetector()
        self.assertEqual(detector.feed(noise(3, -30).tobytes()), [])


def level(seconds: float, amplitude: float) -> np.ndarray:
    """A constant-level block: the fake VAD below reads it as voice (loud), echo (middle) or silence."""
    return np.full(int(seconds * 16000), int(amplitude * 32767), dtype="<i2")


VOICE, ECHO, QUIET = 0.5, 0.1, 0.0


class FakeProbability:
    """Replaces Silero in a test: loud blocks are voice (p 0.95), middle ones what an echo leaves (p 0.6)."""

    def __enter__(self):
        self.saved = vad.SileroVad.probability
        vad.SileroVad.probability = lambda vad_self, window: (
            0.95 if np.abs(window).max() > 0.3 else 0.6 if np.abs(window).max() > 0.05 else 0.0)
        return self

    def __exit__(self, *exc):
        vad.SileroVad.probability = self.saved


class PauseEventTests(unittest.TestCase):
    """While he talks on, a short natural pause is reported (it does not end the turn); nothing changes without it."""

    def setUp(self):
        patch = FakeProbability()
        patch.__enter__()
        self.addCleanup(patch.__exit__)

    def kinds(self, detector, *blocks):
        return [kind for kind, _ in detector.feed(np.concatenate(blocks).tobytes())]

    def test_a_pause_inside_his_talk_is_reported_once_and_the_turn_goes_on(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.assertEqual(self.kinds(detector, level(16, VOICE), level(0.5, QUIET)), ["start", "pause"])
        self.assertTrue(detector.speaking)
        self.assertGreaterEqual(detector.spoken_ms, 15_000)

    def test_it_waits_for_the_pause_to_last(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.assertEqual(self.kinds(detector, level(1, VOICE), level(0.2, QUIET)), ["start"])

    def test_voice_coming_back_is_reported_so_the_nod_can_be_cut(self):
        detector = vad.TurnDetector(pause_ms=300)
        events = self.kinds(detector, level(2, VOICE), level(0.4, QUIET), level(1, VOICE), level(0.4, QUIET))
        self.assertEqual(events, ["start", "pause", "resume", "pause"])

    def test_a_long_pause_still_ends_the_turn_after_the_pause_event(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.assertEqual(self.kinds(detector, level(2, VOICE), level(1.2, QUIET)), ["start", "pause", "stop"])

    def test_without_the_option_there_are_no_pause_events(self):
        detector = vad.TurnDetector()
        self.assertEqual(self.kinds(detector, level(2, VOICE), level(0.5, QUIET), level(1, VOICE)), ["start"])

    def test_the_echo_of_a_nod_neither_counts_as_voice_nor_stays_in_the_audio(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.kinds(detector, level(16, VOICE), level(0.4, QUIET))
        detector.mask_echo(900)
        events = detector.feed(np.concatenate([level(0.6, ECHO), level(1.0, QUIET)]).tobytes())
        self.assertEqual([kind for kind, _ in events], ["stop"])  # no "resume": it was only the echo
        audio = np.frombuffer(events[0][1], dtype="<i2")
        self.assertEqual(int(np.abs(audio[-int(0.7 * 16000):]).max()), 0)

    def test_his_clear_voice_over_a_nod_still_counts(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.kinds(detector, level(16, VOICE), level(0.4, QUIET))
        detector.mask_echo(900)
        self.assertEqual(self.kinds(detector, level(0.3, VOICE)), ["resume"])

    def test_echo_after_the_turn_ended_does_not_start_a_new_one(self):
        detector = vad.TurnDetector(pause_ms=300)
        self.kinds(detector, level(2, VOICE), level(1.0, QUIET))
        detector.mask_echo(900)
        self.assertEqual(self.kinds(detector, level(0.8, ECHO)), [])


FAKE_ENGINE = """
import json, sys, time
print(json.dumps({"ok": True, "ready": True}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("op") == "tts_stream":
        for i in range(request.get("chunks", 3)):
            time.sleep(request.get("delay", 0))
            path = f"{request['out']}.{i}.pcm"
            open(path, "wb").write(bytes([i + 1]) * 200)
            print(json.dumps({"ok": True, "id": request.get("id"), "chunk": path}), flush=True)
        print(json.dumps({"ok": True, "id": request.get("id"), "final": True}), flush=True)
        continue
    time.sleep(request.get("delay", 0))
    print(json.dumps({"ok": True, "id": request.get("id"), "text": request.get("say", "")}), flush=True)
"""


class EngineWarmTests(unittest.IsolatedAsyncioTestCase):
    """The engine stays loaded for a while after a call and can be warmed before one."""

    async def asyncSetUp(self):
        script = Path(TMP) / "fake_engine.py"
        script.write_text(FAKE_ENGINE, encoding="utf-8")
        self.saved = server.ENGINE, server.ENGINE_PYTHON
        server.ENGINE, server.ENGINE_PYTHON = script, Path(os.sys.executable)
        self.engine = server.VoiceEngine()

    async def asyncTearDown(self):
        await self.engine.stop()
        server.ENGINE, server.ENGINE_PYTHON = self.saved

    async def test_release_after_keeps_engine_until_timeout(self):
        self.engine.start()
        await self.engine.ready
        process = self.engine.process
        self.engine.release_after(0.3)
        await asyncio.sleep(0.1)
        self.assertIs(self.engine.process, process)  # still warm
        await asyncio.sleep(0.5)
        self.assertIsNone(self.engine.process)  # released

    async def test_start_cancels_pending_release(self):
        self.engine.start()
        await self.engine.ready
        process = self.engine.process
        self.engine.release_after(0.3)
        self.engine.start()  # a new call arrives while warm
        await asyncio.sleep(0.5)
        self.assertIs(self.engine.process, process)  # same warm engine, no reload
        await self.engine.request({"op": "stt", "pcm": "x"})

    async def test_a_cancelled_request_does_not_poison_the_next_one(self):
        """A call can end while the engine is still synthesizing; the warm engine must not hand that
        late reply to whoever asks next."""
        self.engine.start()
        await self.engine.ready
        slow = asyncio.ensure_future(self.engine.request({"op": "tts", "delay": 0.6, "say": "tarde"}))
        await asyncio.sleep(0.2)
        slow.cancel()  # the call ended mid-synthesis
        reply = await self.engine.request({"op": "stt", "say": "mi respuesta"})
        self.assertEqual(reply["text"], "mi respuesta")
        reply = await self.engine.request({"op": "stt", "say": "otra"})
        self.assertEqual(reply["text"], "otra")

    async def test_stream_yields_the_chunks_in_order_then_ends(self):
        self.engine.start()
        await self.engine.ready
        out = str(Path(TMP) / "s1")
        got = []
        async for path in self.engine.request_stream({"op": "tts_stream", "out": out, "chunks": 3}):
            got.append(Path(path).read_bytes()[0])
        self.assertEqual(got, [1, 2, 3])

    async def test_abandoning_a_stream_does_not_poison_the_next_request(self):
        self.engine.start()
        await self.engine.ready
        out = str(Path(TMP) / "s2")
        stream = self.engine.request_stream({"op": "tts_stream", "out": out, "chunks": 4, "delay": 0.2})
        agen = stream.__aiter__()
        await agen.__anext__()          # one chunk, then the call ends
        await agen.aclose()
        reply = await self.engine.request({"op": "stt", "say": "mi respuesta"})
        self.assertEqual(reply["text"], "mi respuesta")

    async def test_app_listing_contacts_warms_engine(self):
        srv = server.Server({"phone_token": "secret", "local_port": 0, "phone_port": 0,
                             "phone_host": "127.0.0.1", "contacts": []})
        srv.engine = self.engine
        _, phone = server.build_apps(srv)
        runner = web.AppRunner(phone)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 18779)
        await site.start()
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get("http://127.0.0.1:18779/contacts",
                                    headers={"Authorization": "Bearer secret"}) as response:
                    self.assertEqual(response.status, 200)
            await self.engine.ready
            self.assertIsNotNone(self.engine.process)
            self.assertIsNotNone(self.engine.release)
        finally:
            await runner.cleanup()

    async def test_warm_loads_and_schedules_release(self):
        self.engine.warm(0.3)
        await self.engine.ready
        self.assertIsNotNone(self.engine.process)
        await asyncio.sleep(0.5)
        self.assertIsNone(self.engine.process)


class FakeLocalEngine:
    """Stands in for the voice engine in the dictation/voice endpoint tests."""

    def __init__(self):
        self.released = None
        self.requests = []

    def start(self): pass
    def warm(self, seconds): self.released = seconds

    async def request(self, payload):
        self.requests.append(payload)
        data = np.fromfile(payload["pcm"], dtype="<i2")
        return {"ok": True, "text": f"escuché {len(data)} muestras"}

    async def request_stream(self, payload):
        self.requests.append(payload)
        for i in range(2):
            path = f"{payload['out']}.{i}.pcm"
            Path(path).write_bytes(np.full(480, i + 1, dtype="<i2").tobytes())
            yield path


class LocalVoiceEndpointsTests(unittest.IsolatedAsyncioTestCase):
    """A local helper on this Mac (e.g. a menu-bar app, not included) dictates and speaks through the local port only."""

    async def asyncSetUp(self):
        self.server = server.Server({"phone_token": "x", "local_port": 0, "phone_port": 0, "phone_host": "127.0.0.1"})
        self.engine = FakeLocalEngine()
        self.server.engine = self.engine
        local, phone = server.build_apps(self.server)
        self.runners = []
        for app, port in ((local, 18790), (phone, 18791)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            self.runners.append(runner)
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        for runner in self.runners:
            await runner.cleanup()

    async def test_dictar_turns_audio_into_text_and_keeps_the_engine_warm(self):
        pcm = np.zeros(16000, dtype="<i2").tobytes()           # one second at 16 kHz
        async with self.http.post("http://127.0.0.1:18790/dictar", data=pcm) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["text"], "escuché 16000 muestras")
        self.assertEqual(self.engine.released, server.WARM_SECONDS)

    async def test_dictar_refuses_audio_that_is_too_short(self):
        async with self.http.post("http://127.0.0.1:18790/dictar", data=b"\x00" * 100) as response:
            self.assertEqual(response.status, 400)

    async def test_voz_streams_the_voice_piece_by_piece(self):
        body = {"text": "Primera frase corta. Segunda frase corta."}
        async with self.http.post("http://127.0.0.1:18790/voz", json=body) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("rate=48000", response.headers["Content-Type"])
            audio = await response.read()
        pieces = [r for r in self.engine.requests if r["op"] == "tts_stream"]
        self.assertGreaterEqual(len(pieces), 1)
        self.assertEqual(len(audio), 1920 * len(pieces))         # two 480-sample (960-byte) chunks per piece
        self.assertEqual(self.engine.released, server.WARM_SECONDS)

    async def test_voz_without_text_is_a_bad_request(self):
        async with self.http.post("http://127.0.0.1:18790/voz", json={"text": "  "}) as response:
            self.assertEqual(response.status, 400)

    async def test_the_phone_port_does_not_offer_them(self):
        async with self.http.post("http://127.0.0.1:18791/dictar", data=b"\x00" * 40000) as response:
            self.assertEqual(response.status, 404)
        async with self.http.post("http://127.0.0.1:18791/voz", json={"text": "hola"}) as response:
            self.assertEqual(response.status, 404)


class RemoteBarEndpointsTests(unittest.IsolatedAsyncioTestCase):
    """A helper on another Mac dictates, speaks and asks Jev through the tailnet port, bearer token required."""

    async def asyncSetUp(self):
        self.server = server.Server({"phone_token": "x"})
        self.engine = FakeLocalEngine()
        self.server.engine = self.engine
        self.runner = web.AppRunner(server.build_agent_app(self.server, "secreto"))
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 18795).start()
        self.http = aiohttp.ClientSession()
        self.auth = {"Authorization": "Bearer secreto"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()

    async def test_dictar_and_voz_work_with_the_token(self):
        pcm = np.zeros(16000, dtype="<i2").tobytes()
        async with self.http.post("http://127.0.0.1:18795/dictar", data=pcm, headers=self.auth) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["text"], "escuché 16000 muestras")
        async with self.http.post("http://127.0.0.1:18795/voz", json={"text": "Hola."}, headers=self.auth) as response:
            self.assertEqual(response.status, 200)
            self.assertGreater(len(await response.read()), 0)

    async def test_jev_routes_are_offered_with_the_token(self):
        with mock.patch.object(server.jev, "enabled", return_value=False):
            async with self.http.post("http://127.0.0.1:18795/jev", headers=self.auth,
                                      json={"pregunta": "muletilla", "texto": "hola"}) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {"decision": None})
            async with self.http.post("http://127.0.0.1:18795/jev/calentar", headers=self.auth) as response:
                self.assertEqual(response.status, 200)

    async def test_the_text_mailbox_is_offered_with_the_token(self):
        sent = []

        async def fake_prompt(target, text):
            sent.append(target)

        async def fake_status(target):
            return "working"

        self.server.config["contacts"] = [{"id": "firstmate", "name": "Firstmate", "agent": "w1:p1"}]
        with mock.patch.object(server.herdr_agent, "prompt", fake_prompt), \
                mock.patch.object(server.herdr_agent, "status", fake_status):
            for headers in ({}, {"Authorization": "Bearer otro"}):
                async with self.http.post("http://127.0.0.1:18795/texto/enviar", json={"texto": "hola"},
                                          headers=headers) as response:
                    self.assertEqual(response.status, 401)
                async with self.http.get("http://127.0.0.1:18795/texto/respuesta?timeout=0", headers=headers) as response:
                    self.assertEqual(response.status, 401)
            self.assertEqual(sent, [])
            async with self.http.post("http://127.0.0.1:18795/texto/enviar", json={"texto": "hola"},
                                      headers=self.auth) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(sent, ["w1:p1"])
            async with self.http.get("http://127.0.0.1:18795/texto/respuesta?timeout=0&tramos=1",
                                     headers=self.auth) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["respuestas"], [])

    async def test_without_the_token_nothing_is_dictated_or_spoken(self):
        pcm = np.zeros(16000, dtype="<i2").tobytes()
        for headers in ({}, {"Authorization": "Bearer otro"}):
            for path, kwargs in (("/dictar", {"data": pcm}), ("/voz", {"json": {"text": "Hola."}}),
                                 ("/jev", {"json": {"pregunta": "muletilla", "texto": "hola"}}), ("/jev/calentar", {})):
                async with self.http.post(f"http://127.0.0.1:18795{path}", headers=headers, **kwargs) as response:
                    self.assertEqual(response.status, 401, path)
        self.assertEqual(self.engine.requests, [])


class FcmTokenEndpointTests(unittest.IsolatedAsyncioTestCase):
    """The VPS reads the phone's FCM token through the agent API, bearer token required."""

    async def asyncSetUp(self):
        self.server = server.Server({"phone_token": "x"})
        self.server.engine = FakeLocalEngine()
        self.server.device_file = Path(tempfile.mkdtemp(dir=TMP)) / "device.json"
        local, phone = server.build_apps(self.server)
        self.runners = []
        for app, port in ((server.build_agent_app(self.server, "secreto"), 18792), (local, 18793), (phone, 18794)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            self.runners.append(runner)
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        for runner in self.runners:
            await runner.cleanup()

    async def test_without_the_bearer_token_it_is_unauthorized(self):
        self.server.device_file.write_text(json.dumps({"fcm_token": "tok", "registered": 1.0}))
        for headers in ({}, {"Authorization": "Bearer otro"}):
            async with self.http.get("http://127.0.0.1:18792/fcm-token", headers=headers) as response:
                self.assertEqual(response.status, 401)
                self.assertNotIn("tok", await response.text())

    async def test_without_a_registered_phone_it_is_not_found(self):
        async with self.http.get("http://127.0.0.1:18792/fcm-token",
                                 headers={"Authorization": "Bearer secreto"}) as response:
            self.assertEqual(response.status, 404)
            self.assertEqual(await response.json(), {"error": "sin teléfono registrado"})

    async def test_it_returns_the_registered_token(self):
        self.server.device_file.write_text(json.dumps({"fcm_token": "tok-123", "registered": 1792659095.5}))
        async with self.http.get("http://127.0.0.1:18792/fcm-token",
                                 headers={"Authorization": "Bearer secreto"}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"fcm_token": "tok-123", "registered": 1792659095.5})

    async def test_the_local_and_phone_ports_do_not_offer_it(self):
        self.server.device_file.write_text(json.dumps({"fcm_token": "tok", "registered": 1.0}))
        for port in (18793, 18794):
            async with self.http.get(f"http://127.0.0.1:{port}/fcm-token") as response:
                self.assertEqual(response.status, 404)


class TextChannelTests(unittest.IsolatedAsyncioTestCase):
    """Hermes talks to the firstmate in text: its `llamada decir` replies, with no call, wait in a mailbox."""

    async def asyncSetUp(self):
        self.sent = []
        self.agent_status = "working"

        async def fake_prompt(target, text):
            self.sent.append((target, text))

        async def fake_status(target):
            return self.agent_status

        self.real = server.herdr_agent.prompt, server.herdr_agent.status
        server.herdr_agent.prompt, server.herdr_agent.status = fake_prompt, fake_status
        self.srv = server.Server({"phone_token": "x", "local_port": 0, "phone_port": 0, "phone_host": "127.0.0.1",
                                  "contacts": [{"id": "firstmate", "name": "Firstmate", "agent": "w1:p1"}]})
        self.srv.engine = FakeLocalEngine()
        self.late = []
        self.srv.texto.avisar_tarde = lambda texto: self.late.append(texto)
        local, _ = server.build_apps(self.srv)
        self.runner = web.AppRunner(local)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 18792).start()
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        server.herdr_agent.prompt, server.herdr_agent.status = self.real
        await self.http.close()
        await self.runner.cleanup()

    async def post(self, path, body):
        async with self.http.post(f"http://127.0.0.1:18792{path}", json=body) as response:
            return response.status, await response.json()

    async def get(self, path):
        async with self.http.get(f"http://127.0.0.1:18792{path}") as response:
            return response.status, await response.json()

    async def test_enviar_hands_the_message_to_the_firstmate_with_the_text_intro_once(self):
        status, body = await self.post("/texto/enviar", {"texto": "¿Estás ahí?"})
        self.assertEqual(status, 200)
        await self.post("/texto/enviar", {"texto": "Segunda pregunta"})
        (target1, first), (target2, second) = self.sent
        self.assertEqual((target1, target2), ("w1:p1", "w1:p1"))
        self.assertIn("desde Hermes", first)                 # says it is text, not a call
        self.assertIn("llamada decir", first)
        self.assertTrue(first.endswith("USUARIO dijo: ¿Estás ahí?"))
        self.assertEqual(second, "[llamada] USUARIO dijo: Segunda pregunta")

    async def test_the_reply_to_llamada_decir_waits_in_the_mailbox(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        status, body = await self.post("/say", {"text": "Aquí estoy, dime."})
        self.assertEqual(body["status"], "en_curso")        # what the firstmate expects from a call
        status, body = await self.get("/texto/respuesta?timeout=1")
        self.assertEqual(body["respuestas"], ["Aquí estoy, dime."])
        status, body = await self.get("/texto/respuesta?timeout=0.2")
        self.assertEqual(body["respuestas"], [])             # delivered once, not repeated

    async def test_waiting_returns_as_soon_as_the_reply_arrives(self):
        await self.post("/texto/enviar", {"texto": "hola"})

        async def answer_later():
            await asyncio.sleep(0.4)
            await self.post("/say", {"text": "Ya te respondo."})

        task = asyncio.ensure_future(answer_later())
        started = time.monotonic()
        status, body = await self.get("/texto/respuesta?timeout=20")
        await task
        self.assertEqual(body["respuestas"], ["Ya te respondo."])
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.late, [])                       # someone was waiting: no notification

    async def test_timeout_says_the_firstmate_is_still_working(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        status, body = await self.get("/texto/respuesta?timeout=0.3")
        self.assertEqual(body["respuestas"], [])
        self.assertEqual(body["estado"], "working")

    async def test_a_late_reply_nobody_waits_for_raises_a_notification_and_stays_in_the_mailbox(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        await self.post("/say", {"text": "Perdón por la demora: ya está listo lo que pediste."})
        self.assertEqual(len(self.late), 1)
        self.assertIn("ya está listo", self.late[0])
        status, body = await self.get("/texto/respuesta?timeout=0.2")
        self.assertEqual(len(body["respuestas"]), 1)

    async def test_colgar_also_lands_in_the_mailbox(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        status, body = await self.post("/end", {"text": "Hasta luego, Ana."})
        self.assertEqual(body["status"], "terminada")
        status, body = await self.get("/texto/respuesta?timeout=0.2")
        self.assertEqual(body["respuestas"], ["Hasta luego, Ana."])

    async def test_pieces_said_with_sigue_reach_hermes_together_with_the_last_one(self):
        await self.post("/texto/enviar", {"texto": "hola"})

        async def answer_in_pieces():
            await self.post("/say", {"text": "Sí, ya lo miro.", "sigue": True})
            await asyncio.sleep(0.5)
            await self.post("/say", {"text": "La doce está lista."})

        task = asyncio.ensure_future(answer_in_pieces())
        status, body = await self.get("/texto/respuesta?timeout=10")
        await task
        self.assertEqual(body["respuestas"], ["Sí, ya lo miro.", "La doce está lista."])

    async def test_if_the_rest_does_not_come_in_time_hermes_gets_the_pieces_so_far(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        await self.post("/say", {"text": "Sí, ya lo miro.", "sigue": True})
        status, body = await self.get("/texto/respuesta?timeout=0.3")
        self.assertEqual(body["respuestas"], ["Sí, ya lo miro."])

    async def test_by_pieces_each_piece_comes_as_soon_as_it_is_said_and_completo_marks_the_last(self):
        await self.post("/texto/enviar", {"texto": "hola"})

        async def answer_in_pieces():
            await asyncio.sleep(0.2)
            await self.post("/say", {"text": "Sí, ya lo miro.", "sigue": True})
            await asyncio.sleep(1.5)
            await self.post("/say", {"text": "La doce está lista."})

        task = asyncio.ensure_future(answer_in_pieces())
        started = time.monotonic()
        status, body = await self.get("/texto/respuesta?timeout=10&tramos=1")
        self.assertEqual((body["respuestas"], body["completo"]), (["Sí, ya lo miro."], False))
        self.assertLess(time.monotonic() - started, 1.2)          # did not wait for the rest
        status, body = await self.get("/texto/respuesta?timeout=10&tramos=1")
        await task
        self.assertEqual((body["respuestas"], body["completo"]), (["La doce está lista."], True))
        self.assertEqual(self.late, [])                            # someone was waiting: no notification

    async def test_by_pieces_with_nothing_said_it_waits_out_the_timeout(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        status, body = await self.get("/texto/respuesta?timeout=0.3&tramos=1")
        self.assertEqual(body["respuestas"], [])
        self.assertEqual(body["estado"], "working")

    async def test_without_tramos_the_reply_is_still_whole_and_says_completo(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        await self.post("/say", {"text": "Sí, ya lo miro.", "sigue": True})
        await self.post("/say", {"text": "La doce está lista."})
        status, body = await self.get("/texto/respuesta?timeout=1")
        self.assertEqual((body["respuestas"], body["completo"]), (["Sí, ya lo miro.", "La doce está lista."], True))

    async def test_a_late_piece_raises_no_notification_until_the_answer_is_complete(self):
        await self.post("/texto/enviar", {"texto": "hola"})
        await self.post("/say", {"text": "Sí, ya lo miro.", "sigue": True})
        self.assertEqual(self.late, [])
        await self.post("/say", {"text": "La doce está lista."})
        self.assertEqual(len(self.late), 1)

    async def test_without_a_text_conversation_llamada_decir_behaves_as_before(self):
        status, body = await self.post("/say", {"text": "hola"})
        self.assertEqual(body["status"], "sin_llamada")

    async def test_enviar_rejects_empty_text_and_reports_a_failed_delivery(self):
        status, _ = await self.post("/texto/enviar", {"texto": "   "})
        self.assertEqual(status, 400)

        async def broken(target, text):
            raise server.herdr_agent.HerdrError("sin acceso")

        server.herdr_agent.prompt = broken
        status, body = await self.post("/texto/enviar", {"texto": "hola"})
        self.assertEqual(status, 502)
        self.assertIn("sin acceso", body["error"])


class LlamadaCommandTests(unittest.TestCase):
    """`llamada decir --sigue "…"`: a piece of the answer said at once, without waiting for his reply."""

    def run_command(self, *argv, reply=None):
        import contextlib
        import io
        import llamada
        sent = []

        def fake_call(path, body=None):
            sent.append((path, body))
            return reply or {"status": "en_curso"}

        saved, llamada.call = llamada.call, fake_call
        herdr = {k: os.environ.pop(k) for k in ("HERDR_ENV", "HERDR_PANE_ID") if k in os.environ}
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = llamada.main(["llamada", *argv])
        finally:
            llamada.call = saved
            os.environ.update(herdr)
        return code, sent, out.getvalue()

    def test_sigue_sends_the_piece_marked_as_not_the_last(self):
        code, sent, out = self.run_command("decir", "--sigue", "Sí,", "la", "doce", "ya", "está.")
        self.assertEqual(code, 0)
        self.assertEqual(sent, [("/say", {"text": "Sí, la doce ya está.", "sigue": True})])
        self.assertEqual(out, "")

    def test_plain_decir_is_unchanged(self):
        code, sent, out = self.run_command("decir", "Hola.", reply={"status": "ok", "text": "Hola, dime."})
        self.assertEqual(sent, [("/say", {"text": "Hola."})])
        self.assertEqual((code, out), (0, "Hola, dime.\n"))


class StartSiteTests(unittest.IsolatedAsyncioTestCase):
    """After a reboot the server starts before Tailscale has an address: it must not die."""

    async def test_keeps_retrying_until_the_address_is_available(self):
        import socket
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        app = web.Application()
        app.add_routes([web.get("/ping", server.ping)])
        task = asyncio.ensure_future(server.start_site(app, "127.0.0.1", port, retry_seconds=0.1))
        await asyncio.sleep(0.5)
        self.assertFalse(task.done())  # still trying, not crashed
        blocker.close()
        runner = await asyncio.wait_for(task, 3)
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/ping") as response:
                self.assertEqual(response.status, 200)
        await runner.cleanup()


class FakeTypesafe:
    """Stands in for api.typesafe.ai: answers each question with `choice` at `confidence`."""

    def __init__(self, choice="pregunta", confidence=0.9, delay=0.0, status=200):
        self.choice, self.confidence, self.delay, self.status = choice, confidence, delay, status
        self.requests = []
        self.gets = 0

    async def systemone(self, request):
        self.requests.append((dict(request.headers), await request.json()))
        await asyncio.sleep(self.delay)
        if self.status != 200:
            return web.json_response({"error": "boom"}, status=self.status)
        name = next(iter(self.requests[-1][1]["questions"]))
        return web.json_response({"answers": {name: {
            "choice": self.choice, "confidence": self.confidence,
            "probabilities": {self.choice: self.confidence}}}, "usage": {"input_tokens": 1, "output_tokens": 1}})

    async def models(self, request):
        self.gets += 1
        return web.json_response({"data": []})

    async def start(self, port=18790):
        app = web.Application()
        app.add_routes([web.post("/v1/systemone", self.systemone), web.get("/v1/models", self.models)])
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", port).start()
        return f"http://127.0.0.1:{port}"


class JevClientTests(unittest.IsolatedAsyncioTestCase):
    """Jev answers quick choices about what he said; any doubt, delay or failure is «no decision»."""

    async def asyncSetUp(self):
        import jev
        self.folder = Path(tempfile.mkdtemp(prefix="jev-"))
        (self.folder / "jev-token").write_text("clave-de-prueba\n")
        self.fake = FakeTypesafe()
        base = await self.fake.start()
        self.jev_module, self.jev = jev, jev.Jev(self.folder, base=base)

    async def asyncTearDown(self):
        await self.jev.close()
        await self.fake.runner.cleanup()

    async def test_a_confident_answer_is_the_choice(self):
        self.assertEqual(await self.jev.filler_category("¿Qué tareas tenemos para mañana?"), "pregunta")
        headers, body = self.fake.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer clave-de-prueba")
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"usuario_dijo": "¿Qué tareas tenemos para mañana?"})
        question = body["questions"]["muletilla"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), set(self.jev_module.FILLER_CATEGORIES))

    async def test_a_doubtful_answer_is_no_decision(self):
        self.fake.confidence = 0.4
        self.assertIsNone(await self.jev.filler_category("Mira."))

    async def test_a_slow_answer_is_no_decision_in_time(self):
        self.fake.delay = 1.5
        started = time.monotonic()
        self.assertIsNone(await self.jev.filler_category("Hola."))
        self.assertLess(time.monotonic() - started, 0.6)

    async def test_an_http_error_is_no_decision(self):
        self.fake.status = 500
        self.assertIsNone(await self.jev.interruption("espera"))

    async def test_a_choice_that_is_not_an_option_is_no_decision(self):
        self.fake.choice = "inventada"
        self.assertIsNone(await self.jev.interruption("espera"))

    async def test_without_a_key_nothing_is_sent(self):
        (self.folder / "jev-token").unlink()
        self.assertIsNone(await self.jev.filler_category("Hola."))
        self.assertEqual(self.fake.requests, [])

    async def test_interruption_asks_between_interrupting_and_assenting(self):
        self.fake.choice = "asentimiento"
        self.assertEqual(await self.jev.interruption("Ajá."), "asentimiento")
        question = self.fake.requests[0][1]["questions"]["interrupcion"]
        self.assertEqual(set(question["criteria"]), {"interrupcion", "asentimiento"})

    async def test_warm_opens_the_connection_once_in_a_while(self):
        self.jev.warm()
        self.jev.warm()
        await asyncio.sleep(0.3)
        self.assertEqual(self.fake.gets, 1)

    def test_the_flag_file_turns_it_on(self):
        import jev
        self.assertFalse(jev.enabled(self.folder))
        (self.folder / "jev-on").touch()
        self.assertTrue(jev.enabled(self.folder))


class FakeJev:
    """Stands in for the Jev client inside a call: fixed answers, every question recorded."""

    def __init__(self, category=None, verdict=None, delay=0.0):
        self.category, self.verdict, self.delay = category, verdict, delay
        self.asked = []
        self.warmed = 0

    async def filler_category(self, phrase):
        self.asked.append(("muletilla", phrase))
        await asyncio.sleep(self.delay)
        return self.category

    async def interruption(self, phrase):
        self.asked.append(("interrupcion", phrase))
        return self.verdict

    def warm(self):
        self.warmed += 1


class FakeSttEngine:
    def __init__(self, text):
        self.text = text

    async def request(self, payload):
        return {"ok": True, "text": self.text}


class JevInCallTests(unittest.IsolatedAsyncioTestCase):
    """With the jev-on flag the filler fits his phrase and an «ajá» over the agent does not cut it."""

    async def asyncSetUp(self):
        import soundfile
        self.folder = Path(tempfile.mkdtemp(prefix="fillers-jev-"))
        tone = (9000 * np.sin(2 * np.pi * 440 * np.arange(4800) / 48000)).astype(np.int16)
        for name in ("mmm-ya", "dime"):
            soundfile.write(self.folder / f"{name}.wav", tone, 48000, subtype="PCM_16")
        for category, names in (("despedida", ("listo", "hasta-luego")), ("pregunta", ("a-ver",))):
            (self.folder / category).mkdir()
            for name in names:
                soundfile.write(self.folder / category / f"{name}.wav", tone, 48000, subtype="PCM_16")
        self.saved = server.FILLERS
        server.FILLERS = self.folder
        self.flag = Path(TMP) / "jev-on"
        self.flag.touch()
        self.delivered = []

    async def asyncTearDown(self):
        server.FILLERS = self.saved
        self.flag.unlink(missing_ok=True)

    def call(self, heard, jev):
        call = server.Call("jev-test", FakeSttEngine(heard), agent="coordinador", inbound=True, jev=jev)

        async def deliver(text):
            self.delivered.append(text)
        call.deliver = deliver
        return call

    async def he_says(self, call, seconds=1.0):
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * int(seconds * vad.RATE))
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.4)

    async def test_filler_sets_load_by_category(self):
        sets = server.load_filler_sets(self.folder)
        self.assertEqual(sorted(sets), ["despedida", "pregunta"])
        self.assertEqual(sorted(name for name, _ in sets["despedida"]), ["despedida/hasta-luego", "despedida/listo"])

    async def test_the_filler_fits_the_kind_of_phrase(self):
        jev = FakeJev(category="despedida")
        call = self.call("Eso es todo, ya puedes cortar.", jev)
        await self.he_says(call)
        self.assertEqual(jev.asked, [("muletilla", "Eso es todo, ya puedes cortar.")])
        self.assertEqual(call.fillers_played, 1)
        self.assertTrue(call.filler_name.startswith("despedida/"), call.filler_name)

    async def test_without_a_decision_the_filler_is_a_random_generic_one(self):
        call = self.call("Te estoy hablando.", FakeJev(category=None))
        await self.he_says(call)
        self.assertEqual(call.fillers_played, 1)
        self.assertIn(call.filler_name, ("mmm-ya", "dime"))

    async def test_a_category_with_no_recordings_falls_back_to_the_generic_ones(self):
        call = self.call("Gracias.", FakeJev(category="agradecimiento"))
        await self.he_says(call)
        self.assertIn(call.filler_name, ("mmm-ya", "dime"))

    async def test_with_the_flag_off_jev_is_not_asked(self):
        self.flag.unlink()
        jev = FakeJev(category="despedida")
        call = self.call("Adiós.", jev)
        await self.he_says(call)
        self.assertEqual(jev.asked, [])
        self.assertIn(call.filler_name, ("mmm-ya", "dime"))

    async def test_no_filler_when_nothing_intelligible_was_heard(self):
        jev = FakeJev(category="despedida")
        call = self.call("", jev)
        await self.he_says(call)
        self.assertEqual((jev.asked, call.fillers_played), ([], 0))

    async def test_talking_starts_warming_the_connection(self):
        jev = FakeJev()
        call = self.call("Hola.", jev)
        call._speech_started()
        self.assertEqual(jev.warmed, 1)

    def agent_talking(self, call):
        call.out.enqueue(server.Segment("Primero voy a correr todas las pruebas y luego reviso la base de datos.",
                                        np.zeros(48000 * 5, dtype=np.int16)))
        call.transcript.append(("claude", "Primero voy a correr todas las pruebas y luego reviso la base de datos."))

    async def test_an_assent_over_the_agent_lets_it_carry_on(self):
        jev = FakeJev(verdict="asentimiento")
        call = self.call("Ajá, sí.", jev)
        self.agent_talking(call)
        await self.he_says(call, seconds=0.5)
        self.assertEqual(jev.asked, [("interrupcion", "Ajá, sí.")])
        self.assertFalse(call.out.paused)
        self.assertFalse(call.barge_in.is_set())
        self.assertEqual(len(call.out.queue), 1)
        self.assertEqual(call.agent_pending, [])
        self.assertEqual(self.delivered, [])

    async def test_a_yes_over_a_question_does_not_cut_but_still_reaches_the_agent(self):
        call = self.call("Sí, sí.", FakeJev(verdict="asentimiento"))
        call.out.enqueue(server.Segment("Ya terminé las pruebas. ¿Lo despliego?", np.zeros(48000 * 5, dtype=np.int16)))
        call.transcript.append(("claude", "Ya terminé las pruebas. ¿Lo despliego?"))
        await self.he_says(call, seconds=0.5)
        self.assertFalse(call.barge_in.is_set())
        self.assertFalse(call.out.paused)
        self.assertEqual(call.agent_pending, ["Sí, sí."])

    async def test_a_real_interruption_still_cuts_the_agent(self):
        call = self.call("Espera, no toques la base de datos.", FakeJev(verdict="interrupcion"))
        self.agent_talking(call)
        await self.he_says(call)
        self.assertTrue(call.barge_in.is_set())
        self.assertEqual([seg.text for seg in call.out.queue], [call.filler_name])  # only "ok": its voice is gone
        self.assertEqual(call.agent_pending, ["Espera, no toques la base de datos."])

    async def test_without_a_verdict_his_words_cut_the_agent_as_before(self):
        call = self.call("Sí.", FakeJev(verdict=None))
        self.agent_talking(call)
        await self.he_says(call, seconds=0.5)
        self.assertTrue(call.barge_in.is_set())

    def seed_mm(self):
        import soundfile
        (self.folder / "mm").mkdir()
        tone = (9000 * np.sin(2 * np.pi * 330 * np.arange(4800) / 48000)).astype(np.int16)
        for name in ("mm", "mmm"):
            soundfile.write(self.folder / "mm" / f"{name}.wav", tone, 48000, subtype="PCM_16")

    async def test_the_mm_is_not_a_category_of_fillers(self):
        self.seed_mm()
        sets = server.load_filler_sets(self.folder)
        self.assertIn("mm", sets)  # on disk, but a call keeps it apart from what Jev can pick
        call = self.call("Hola.", FakeJev())
        self.assertNotIn("mm", call.filler_sets)
        self.assertEqual(sorted(name for name, _ in call.mm_clips), ["mm/mm", "mm/mmm"])

    async def test_a_neutral_mm_comes_first_then_the_fitted_filler_behind_it(self):
        self.seed_mm()
        call = self.call("Eso es todo, ya puedes cortar.", FakeJev(category="despedida"))
        await self.he_says(call)
        names = [seg.text for seg in call.out.queue]
        self.assertEqual(len(names), 2, names)  # in a queue: never two clips at once
        self.assertTrue(names[0].startswith("mm/"), names)
        self.assertTrue(names[1].startswith("despedida/"), names)
        self.assertEqual(call.fillers_played, 2)

    async def test_the_mm_does_not_wait_for_jev(self):
        self.seed_mm()
        call = self.call("¿Qué tareas hay para mañana?", FakeJev(category="pregunta", delay=1.0))
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * vad.RATE)
        await asyncio.sleep(server.FILLER_DELAY + 0.15)
        self.assertEqual([seg.text.split("/")[0] for seg in call.out.queue], ["mm"])
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(1.2)
        self.assertEqual([seg.text.split("/")[0] for seg in call.out.queue], ["mm", "pregunta"])

    async def test_without_a_decision_the_mm_is_enough(self):
        self.seed_mm()
        call = self.call("Te estoy hablando.", FakeJev(category=None))
        await self.he_says(call)
        self.assertEqual([seg.text.split("/")[0] for seg in call.out.queue], ["mm"])
        self.assertEqual(call.fillers_played, 1)

    async def test_a_category_with_no_recordings_leaves_only_the_mm(self):
        self.seed_mm()
        call = self.call("Gracias.", FakeJev(category="agradecimiento"))
        await self.he_says(call)
        self.assertEqual([seg.text.split("/")[0] for seg in call.out.queue], ["mm"])

    async def test_the_fitted_filler_is_skipped_when_the_agent_already_speaks(self):
        self.seed_mm()
        call = self.call("Adiós.", FakeJev(category="despedida", delay=0.6))
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * vad.RATE)
        await asyncio.sleep(server.FILLER_DELAY + 0.15)
        call.out.queue.clear()  # the "mm" has played
        call.speech_chain = asyncio.ensure_future(asyncio.sleep(5))  # the answer is being spoken
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.8)
        call.speech_chain.cancel()
        self.assertEqual(list(call.out.queue), [])
        self.assertEqual(call.fillers_played, 1)

    async def test_the_fitted_filler_is_skipped_when_the_answer_is_already_playing(self):
        self.seed_mm()
        call = self.call("Adiós.", FakeJev(category="despedida", delay=0.6))
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * vad.RATE)
        await asyncio.sleep(server.FILLER_DELAY + 0.15)
        call.out.queue.clear()
        self.agent_talking(call)
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.8)
        self.assertEqual(len(call.out.queue), 1)
        self.assertFalse(call.out.queue[0].text.startswith("despedida/"))

    async def test_talking_over_the_mm_drops_it_and_no_fitted_one_follows(self):
        self.seed_mm()
        call = self.call("Adiós.", FakeJev(category="despedida", delay=0.6))
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * vad.RATE)
        await asyncio.sleep(server.FILLER_DELAY + 0.15)
        call._speech_started()  # he goes on over the "mm"
        self.assertEqual(list(call.out.queue), [])
        self.assertFalse(call.barge_in.is_set())
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.8)
        self.assertEqual(list(call.out.queue), [])

    async def test_the_real_answer_drops_the_mm_and_the_fitted_filler(self):
        self.seed_mm()
        call = self.call("Adiós.", FakeJev(category="despedida"))
        await self.he_says(call)
        self.assertEqual(len(call.out.queue), 2)
        call._drop_fillers()
        self.assertEqual(list(call.out.queue), [])

    async def test_with_the_flag_off_there_is_no_mm(self):
        self.seed_mm()
        self.flag.unlink()
        call = self.call("Adiós.", FakeJev(category="despedida"))
        await self.he_says(call)
        self.assertEqual(call.fillers_played, 1)
        self.assertIn(call.filler_name, ("mmm-ya", "dime"))

    async def test_without_mm_recordings_jev_on_keeps_the_fitted_filler_alone(self):
        call = self.call("Eso es todo, ya puedes cortar.", FakeJev(category="despedida"))
        await self.he_says(call)
        self.assertEqual(call.fillers_played, 1)
        self.assertTrue(call.filler_name.startswith("despedida/"))

    async def test_the_agent_hearing_itself_is_not_sent_to_jev(self):
        jev = FakeJev(verdict="interrupcion")
        call = self.call("luego reviso la base de datos", jev)
        self.agent_talking(call)
        await self.he_says(call)
        self.assertEqual(jev.asked, [])
        self.assertFalse(call.barge_in.is_set())


class JevLocalEndpointTests(unittest.IsolatedAsyncioTestCase):
    """A local helper asks the server's Jev (never the key itself), on the local port only."""

    async def asyncSetUp(self):
        self.server = server.Server({"phone_token": "x", "local_port": 0, "phone_port": 0, "phone_host": "127.0.0.1"})
        self.flag = Path(TMP) / "jev-on"
        self.flag.touch()
        local, phone = server.build_apps(self.server)
        self.runners = []
        for app, port in ((local, 18796), (phone, 18797)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            self.runners.append(runner)
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        self.flag.unlink(missing_ok=True)
        await self.http.close()
        for runner in self.runners:
            await runner.cleanup()

    async def ask(self, body, port=18796):
        async with self.http.post(f"http://127.0.0.1:{port}/jev", json=body) as response:
            return response.status, await response.json()

    async def test_filler_category_for_his_phrase(self):
        self.server.jev = jev = FakeJev(category="pregunta")
        self.assertEqual(await self.ask({"pregunta": "muletilla", "texto": "¿Qué tareas hay?"}),
                         (200, {"decision": "pregunta"}))
        self.assertEqual(jev.asked, [("muletilla", "¿Qué tareas hay?")])

    async def test_assent_or_interruption_over_the_voice(self):
        self.server.jev = jev = FakeJev(verdict="asentimiento")
        self.assertEqual(await self.ask({"pregunta": "interrupcion", "texto": "Ajá."}),
                         (200, {"decision": "asentimiento"}))
        self.assertEqual(jev.asked, [("interrupcion", "Ajá.")])

    async def test_doubt_or_no_answer_in_time_is_no_decision(self):
        self.server.jev = FakeJev(category=None)
        self.assertEqual(await self.ask({"pregunta": "muletilla", "texto": "Mira."}), (200, {"decision": None}))

    async def test_with_the_flag_off_jev_is_not_asked(self):
        self.flag.unlink()
        self.server.jev = jev = FakeJev(category="pregunta")
        self.assertEqual(await self.ask({"pregunta": "muletilla", "texto": "¿Qué hora es?"}),
                         (200, {"decision": None}))
        self.assertEqual(jev.asked, [])

    async def test_empty_phrase_or_unknown_question_is_a_bad_request(self):
        self.server.jev = jev = FakeJev(category="pregunta")
        self.assertEqual((await self.ask({"pregunta": "muletilla", "texto": "  "}))[0], 400)
        self.assertEqual((await self.ask({"pregunta": "otra", "texto": "Hola."}))[0], 400)
        async with self.http.post("http://127.0.0.1:18796/jev", data=b"no es json") as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(jev.asked, [])

    async def test_warm_opens_the_connection_only_with_the_flag(self):
        self.server.jev = jev = FakeJev()
        async with self.http.post("http://127.0.0.1:18796/jev/calentar") as response:
            self.assertEqual(response.status, 200)
        self.flag.unlink()
        async with self.http.post("http://127.0.0.1:18796/jev/calentar") as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(jev.warmed, 1)

    async def test_the_phone_port_does_not_offer_it(self):
        self.server.jev = FakeJev(category="pregunta")
        async with self.http.post("http://127.0.0.1:18797/jev", json={"pregunta": "muletilla", "texto": "Hola."}) as r:
            self.assertEqual(r.status, 404)


class FillerRecordingTests(unittest.TestCase):
    def test_the_mm_clips_are_short_neutral_ones_apart_from_the_jev_categories(self):
        import jev
        import make_fillers
        self.assertGreaterEqual(len(make_fillers.MM), 2)
        self.assertNotIn(server.MM_FOLDER, jev.FILLER_CATEGORIES)
        for text in make_fillers.MM.values():
            self.assertLessEqual(len(text), 6, text)  # "Mm.", "Ajá.": around half a second


    def test_every_recorded_category_is_one_jev_can_choose(self):
        import jev
        import make_fillers
        self.assertTrue(make_fillers.FILLER_CATEGORIES)
        self.assertLessEqual(set(make_fillers.FILLER_CATEGORIES), set(jev.FILLER_CATEGORIES))
        for texts in make_fillers.FILLER_CATEGORIES.values():
            self.assertGreaterEqual(len(texts), 2)


class FillerTests(unittest.TestCase):
    def test_pick_avoids_repeating_the_last_one(self):
        fillers = [("a", np.zeros(10, dtype=np.int16)), ("b", np.zeros(10, dtype=np.int16))]
        for _ in range(20):
            self.assertEqual(server.pick_filler(fillers, "a")[0], "b")
        self.assertEqual(server.pick_filler(fillers[:1], "a")[0], "a")  # a single one may repeat
        self.assertIsNone(server.pick_filler([], None))

    def test_load_reads_wavs_from_the_fillers_folder(self):
        import soundfile
        folder = Path(TMP) / "fillers-unit"
        folder.mkdir(exist_ok=True)
        soundfile.write(folder / "dime.wav", np.zeros(4800, dtype=np.int16), 48000, subtype="PCM_16")
        (folder / "notas.txt").write_text("no es audio")
        loaded = server.load_fillers(folder)
        self.assertEqual([name for name, _ in loaded], ["dime"])
        self.assertEqual(loaded[0][1].dtype, np.int16)

    def test_missing_folder_means_no_fillers(self):
        self.assertEqual(server.load_fillers(Path(TMP) / "no-existe"), [])


class JoinWaitTests(unittest.TestCase):
    """How long to wait for him to go on, judging by how his last phrase ends."""

    def test_a_phrase_that_trails_off_waits_longer(self):
        for text in ("Quiero que revises el servidor y,", "Entonces lo que pasa es que", "Necesito que me digas el",
                     "Mira, o sea...", "Dime si puedes mirar lo de"):
            self.assertEqual(server.join_seconds(text), server.JOIN_LONG_SECONDS, text)

    def test_a_clear_question_goes_a_little_sooner(self):
        self.assertEqual(server.join_seconds("¿Me puedes decir cuántos proyectos estamos viendo?"),
                         server.JOIN_QUESTION_SECONDS)

    def test_everything_else_keeps_the_default(self):
        for text in ("Ok, perfecto.", "Sí, apruébalo. Puedes desplegar.", "¿Qué?", "Hola"):
            self.assertEqual(server.join_seconds(text), server.AGENT_JOIN_SECONDS, text)

    def test_empty_text_keeps_the_default(self):
        self.assertEqual(server.join_seconds(""), server.AGENT_JOIN_SECONDS)


class StreamAudioTests(unittest.TestCase):
    """Chunked synthesis must sound like the whole-file pipeline: same level, smooth joins, soft ending."""

    def tone(self, seconds=3.0, amp=0.03):
        n = int(seconds * 24000)
        return (amp * np.sin(2 * np.pi * 220 * np.arange(n) / 24000)).astype(np.float32)

    def test_upsampler_doubles_the_rate_and_is_chunk_invariant(self):
        import engine
        x = self.tone()
        whole = engine.Upsampler2x().process(x)
        self.assertEqual(len(whole), 2 * len(x))
        up = engine.Upsampler2x()
        parts = np.concatenate([up.process(x[:10000]), up.process(x[10000:33333]), up.process(x[33333:])])
        np.testing.assert_allclose(parts, whole, atol=1e-6)

    def test_upsampler_keeps_the_level(self):
        import engine
        y = engine.Upsampler2x().process(self.tone(2.0, amp=0.1))
        self.assertAlmostEqual(float(np.sqrt(np.mean(y[2000:-2000] ** 2))), 0.1 / np.sqrt(2), delta=0.005)

    def test_finisher_is_chunk_invariant_and_brings_the_level_up(self):
        import engine
        x = self.tone(3.0, amp=0.03)
        one = engine.StreamFinisher()
        whole = np.concatenate([one.push(x), one.finish()])
        two = engine.StreamFinisher()
        parts = np.concatenate([two.push(x[:24000]), two.push(x[24000:50000]), two.push(x[50000:]), two.finish()])
        np.testing.assert_array_equal(parts, whole)
        body = whole[5000:-20000].astype(np.float32) / 32768
        self.assertAlmostEqual(float(np.sqrt(np.mean(body ** 2))), 0.03 * engine.STREAM_GAIN / np.sqrt(2), delta=0.01)

    def test_finisher_length_has_the_silent_tail_and_the_end_fades(self):
        import engine
        x = self.tone(2.0)
        f = engine.StreamFinisher()
        out = np.concatenate([f.push(x), f.finish()])
        self.assertEqual(len(out), 2 * len(x) + int(0.25 * 48000))
        self.assertEqual(int(np.abs(out[-int(0.25 * 48000):]).max()), 0)            # the pause after the phrase
        speech_end = out[:-int(0.25 * 48000)]
        self.assertLess(int(np.abs(speech_end[-40:]).max()), 600)                  # faded, no abrupt cut
        self.assertGreater(int(np.abs(speech_end[-int(0.2 * 48000):-int(0.1 * 48000)]).max()), 2000)

    def test_loud_peaks_are_softly_limited(self):
        import engine
        f = engine.StreamFinisher()
        out = np.concatenate([f.push(self.tone(1.0, amp=0.9)), f.finish()])
        self.assertLess(int(np.abs(out).max()), 32768 * 0.96)


class EchoTests(unittest.TestCase):
    def test_a_fragment_of_what_the_agent_is_saying_is_an_echo(self):
        spoken = "Listo, terminé de revisar el servidor y todo quedó funcionando bien."
        self.assertTrue(server.is_echo("terminé de revisar el servidor", spoken))
        self.assertTrue(server.is_echo("Todo quedó funcionando bien.", spoken))

    def test_his_own_words_are_not_an_echo(self):
        spoken = "Listo, terminé de revisar el servidor y todo quedó funcionando bien."
        self.assertFalse(server.is_echo("espera, no toques la base de datos", spoken))
        self.assertFalse(server.is_echo("sí", spoken))          # too short to call an echo
        self.assertFalse(server.is_echo("sí, claro", spoken))
        self.assertFalse(server.is_echo("", spoken))
        self.assertFalse(server.is_echo("hola de nuevo amigo", ""))


class GrowingSegmentTests(unittest.IsolatedAsyncioTestCase):
    """A phrase that is still being generated plays as far as it has got, then waits for more."""

    async def test_playback_waits_for_the_next_chunk_instead_of_ending(self):
        out = server.OutboundAudio()
        segment = server.Segment("hola", np.full(server.OUT_FRAME * 2, 4000, dtype=np.int16), complete=False)
        out.enqueue(segment)
        for _ in range(2):
            self.assertGreater(int(np.abs((await out.recv()).to_ndarray()).max()), 0)
        for _ in range(3):  # caught up with the generator: silence, but the segment stays
            self.assertEqual(int(np.abs((await out.recv()).to_ndarray()).max()), 0)
        self.assertEqual(len(out.queue), 1)
        self.assertFalse(segment.done.is_set())
        segment.extend(np.full(server.OUT_FRAME, 4000, dtype=np.int16))
        self.assertGreater(int(np.abs((await out.recv()).to_ndarray()).max()), 0)
        segment.finish()
        await out.recv()
        self.assertEqual(len(out.queue), 0)
        self.assertTrue(segment.done.is_set())

    async def test_a_cut_mid_generation_estimates_how_much_of_the_phrase_was_heard(self):
        out = server.OutboundAudio()
        text = "una frase que todavía se está generando y que tiene bastantes palabras"
        segment = server.Segment(text, np.full(server.OUT_FRAME * 5, 4000, dtype=np.int16), complete=False)
        out.enqueue(segment)
        for _ in range(5):
            await out.recv()
        cut = out.flush()
        fraction = cut[0][1]
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 0.5)  # only ~0.1 s of a phrase that lasts seconds


class FakeTtsEngine:
    """Voice D stand-in for a call: after `delay` seconds of «synthesis», a tone as long as the text
    (`per_char` seconds a character); records when each text was asked for."""

    def __init__(self, delay=0.3, per_char=0.02):
        self.delay, self.per_char = delay, per_char
        self.asked: list[tuple[float, str]] = []

    def tone(self, text):
        t = np.arange(int(len(text) * self.per_char * 48000)) / 48000
        return (6000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)

    async def request(self, payload):
        import soundfile
        self.asked.append((time.monotonic(), payload["text"]))
        await asyncio.sleep(self.delay)
        soundfile.write(payload["out"], self.tone(payload["text"]), 48000, subtype="PCM_16")
        return {"ok": True}

    async def request_stream(self, payload):
        self.asked.append((time.monotonic(), payload["text"]))
        pcm = self.tone(payload["text"])
        for i, part in enumerate(np.array_split(pcm, 2)):
            await asyncio.sleep(self.delay / 2)
            path = f"{payload['out']}.{i}.pcm"
            part.astype("<i2").tofile(path)
            yield path


class ChainedSpeechTests(unittest.IsolatedAsyncioTestCase):
    """The agent says first what it already knows and the rest in another `llamada decir`: the second one is
    synthesized while the first still sounds and follows it with no gap, in order, nothing said twice."""

    async def asyncSetUp(self):
        self.flag = Path(TMP) / "stream-tts"
        self.flag.unlink(missing_ok=True)
        self.engine = FakeTtsEngine()
        self.frames: list[tuple[float, bool]] = []
        self.states: list[tuple[float, str]] = []
        self.players = []

    async def asyncTearDown(self):
        self.flag.unlink(missing_ok=True)
        for call in getattr(self, "calls", []):
            call.end("terminada")
        for player in self.players:
            player.cancel()

    def call(self, agent="coordinador"):
        call = server.Call("chain-test", self.engine, agent=agent)
        call.wait_sets = {}

        async def send(message):
            if message.get("type") == "state":
                self.states.append((time.monotonic(), message["value"]))
        call.send = send
        self.calls = getattr(self, "calls", []) + [call]
        self.players.append(asyncio.ensure_future(self.play(call)))
        return call

    async def play(self, call):
        """The phone: takes the line's audio in real time and notes which 20 ms frames had voice."""
        while not call.ended:
            frame = await call.out.recv()
            self.frames.append((time.monotonic(), int(np.abs(frame.to_ndarray()).max()) > 1000))

    def longest_gap(self) -> float:
        """The longest silence between the first and the last voiced frame."""
        voiced = [i for i, (_, loud) in enumerate(self.frames) if loud]
        gap = run = 0
        for _, loud in self.frames[voiced[0]:voiced[-1] + 1]:
            run = 0 if loud else run + 1
            gap = max(gap, run)
        return gap * server.OUT_FRAME / server.OUT_RATE

    def voiced_seconds(self) -> float:
        return sum(loud for _, loud in self.frames) * server.OUT_FRAME / server.OUT_RATE

    def assert_said_once_each(self, *texts):
        """All of each text sounded, one after the other: never two at once (that would be shorter)."""
        expected = sum(len(t) for t in texts) * self.engine.per_char
        self.assertAlmostEqual(self.voiced_seconds(), expected, delta=0.1)

    async def wait_voice(self):
        while not any(loud for _, loud in self.frames):
            await asyncio.sleep(0.02)

    async def test_two_decir_in_a_row_sound_back_to_back(self):
        call = self.call()
        call.queue_speech("Sí, la doce ya está lista.")
        await asyncio.wait({call.queue_speech("La diecisiete la sigo revisando.")})
        self.assertLess(self.longest_gap(), 0.1)
        self.assert_said_once_each("Sí, la doce ya está lista.", "La diecisiete la sigo revisando.")
        self.assertEqual([text for _, text in self.engine.asked],
                         ["Sí, la doce ya está lista.", "La diecisiete la sigo revisando."])

    async def test_the_second_is_synthesized_while_the_first_still_sounds(self):
        call = self.call()
        first = call.queue_speech("Sí, la doce ya está lista y la once también.")
        second = call.queue_speech("La diecisiete la sigo revisando.")
        await asyncio.wait({first})
        asked_second = self.engine.asked[1][0]
        first_heard_until = max(t for t, loud in self.frames if loud)
        self.assertLess(asked_second, first_heard_until)
        await asyncio.wait({second})

    async def test_with_streamed_voice_they_also_follow_without_a_gap(self):
        self.flag.write_text("1")
        call = self.call()
        call.queue_speech("Sí, la doce ya está lista.")
        await asyncio.wait({call.queue_speech("La diecisiete la sigo revisando.")})
        self.assertLess(self.longest_gap(), 0.1)

    async def test_the_line_says_listening_only_after_the_last_one(self):
        call = self.call()
        call.queue_speech("Sí, la doce ya está lista.")
        last = call.queue_speech("La diecisiete la sigo revisando.")
        await asyncio.wait({last})
        values = [v for _, v in self.states]
        self.assertEqual(values.count("listening"), 1, values)
        self.assertEqual(values[-1], "listening")

    async def test_a_cut_reports_what_was_still_queued_as_not_said(self):
        call = self.call()
        first = call.queue_speech("Primero voy a correr todas las pruebas del servidor y de la aplicación.")
        call.queue_speech("Después reviso los registros.")
        last = call.queue_speech("Al final te mando un resumen.")
        await self.wait_voice()
        await asyncio.sleep(0.3)
        call._speech_started()
        call._resolve_hold(call.hold_seq, "espera, para")  # his words: a real interruption
        await asyncio.wait({first, last})
        cut = call.pending_interruption
        self.assertTrue(cut["heard"].startswith("Primero"), cut)
        self.assertNotIn("registros", cut["heard"])
        self.assertTrue(cut["not_heard"].endswith("Después reviso los registros. Al final te mando un resumen."),
                        cut)

    async def test_turn_mode_a_sigue_returns_at_once_and_the_answer_follows_it(self):
        call = self.call(agent=None)
        call.queue_speech("Sí, la doce ya está lista.")

        async def he_answers():
            await asyncio.sleep(1.5)
            call.utterances.append("Perfecto, gracias.")
            call.speech_change.set()

        answering = asyncio.ensure_future(he_answers())
        result = await call.turn("La diecisiete la sigo revisando.")
        await answering
        self.assertEqual(result, {"status": "ok", "text": "Perfecto, gracias."})
        self.assertLess(self.longest_gap(), 0.1)
        self.assert_said_once_each("Sí, la doce ya está lista.", "La diecisiete la sigo revisando.")

    async def test_turn_mode_a_cut_tramo_reports_the_interruption_and_the_unsaid_answer(self):
        call = self.call(agent=None)
        call.queue_speech("Primero voy a correr todas las pruebas del servidor y de la aplicación.")
        await self.wait_voice()
        await asyncio.sleep(0.3)
        call._speech_started()
        call.user_speaking = False
        call._resolve_hold(call.hold_seq, "espera, para")
        call.utterances.append("Espera, para.")
        result = await call.turn("Después reviso los registros.")
        self.assertEqual(result["status"], "interrumpido", result)
        self.assertEqual(result["text"], "Espera, para.")
        self.assertTrue(result["heard"].startswith("Primero"), result)
        self.assertTrue(result["not_heard"].endswith("Después reviso los registros."), result)

    async def test_say_with_sigue_in_turn_mode_returns_at_once(self):
        srv = server.Server({"phone_token": "x"})
        srv.call = self.call(agent=None)

        class Request:
            async def json(self):
                return {"text": "Sí, la doce ya está lista.", "sigue": True}

        started = time.monotonic()
        response = await srv.say(Request())
        self.assertEqual(json.loads(response.body), {"status": "en_curso"})
        self.assertLess(time.monotonic() - started, 0.2)
        await asyncio.wait({srv.call.speech_chain})
        self.assertTrue(any(loud for _, loud in self.frames))


class CallTextTests(unittest.IsolatedAsyncioTestCase):
    """The call in text on the phone's screen: each line goes over the call's channel as soon as it exists — the
    agent's when it starts to sound, his once transcribed. Fillers, waiting phrases and nods are never shown."""

    async def asyncSetUp(self):
        self.engine = FakeTtsEngine()
        self.frames: list[tuple[float, bool]] = []
        self.lines: list[tuple[float, str, str]] = []
        self.calls, self.players = [], []

    async def asyncTearDown(self):
        for call in self.calls:
            call.end("terminada")
        for player in self.players:
            player.cancel()

    def call(self, engine=None, agent="coordinador"):
        call = server.Call("text-test", engine or self.engine, agent=agent)
        call.wait_sets = {}

        async def send(message):
            if message.get("type") == "line":
                self.lines.append((time.monotonic(), message["who"], message["text"]))
        call.send = send
        self.calls.append(call)
        self.players.append(asyncio.ensure_future(self.play(call)))
        return call

    async def play(self, call):
        while not call.ended:
            frame = await call.out.recv()
            self.frames.append((time.monotonic(), int(np.abs(frame.to_ndarray()).max()) > 1000))

    def said(self):
        return [(who, text) for _, who, text in self.lines]

    async def test_the_agents_line_is_shown_when_it_starts_to_sound_not_before(self):
        call = self.call()
        await asyncio.wait({call.queue_speech("Sí, la doce ya está lista. Y la once también, la revisé esta mañana.")})
        self.assertEqual(self.said(), [("agent", "Sí, la doce ya está lista. Y la once también, la revisé esta mañana.")])
        first_voice = min(t for t, loud in self.frames if loud)
        self.assertGreaterEqual(self.lines[0][0], first_voice - 0.05)
        self.assertLess(self.lines[0][0], first_voice + 0.1)

    async def test_chained_decir_show_each_line_in_order_as_it_starts(self):
        call = self.call()
        call.queue_speech("Sí, la doce ya está lista.")
        second = call.queue_speech("La diecisiete la sigo revisando.")
        await asyncio.sleep(0.5)  # the first sounds; the second is synthesized but not heard yet
        self.assertEqual(self.said(), [("agent", "Sí, la doce ya está lista.")])
        await asyncio.wait({second})
        self.assertEqual(self.said(), [("agent", "Sí, la doce ya está lista."),
                                       ("agent", "La diecisiete la sigo revisando.")])

    async def test_with_streamed_voice_the_line_is_shown_once(self):
        flag = Path(TMP) / "stream-tts"
        flag.write_text("1")
        try:
            call = self.call()
            await asyncio.wait({call.queue_speech("Primero una frase corta. Luego otra frase bastante más larga que "
                                                  "la primera para que haya varios tramos de voz seguidos.")})
        finally:
            flag.unlink(missing_ok=True)
        self.assertEqual(len(self.lines), 1, self.lines)
        self.assertEqual(self.lines[0][1], "agent")

    async def test_a_speech_dropped_before_it_sounded_is_not_shown(self):
        call = self.call()
        first = call.queue_speech("Primero voy a correr todas las pruebas del servidor y de la aplicación.")
        last = call.queue_speech("Después reviso los registros.")
        while not any(loud for _, loud in self.frames):
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        call._speech_started()
        call._resolve_hold(call.hold_seq, "espera, para")
        await asyncio.wait({first, last})
        self.assertEqual([who for who, _ in self.said()], ["agent"])
        self.assertTrue(self.said()[0][1].startswith("Primero"))

    async def test_a_recorded_greeting_is_shown(self):
        import soundfile
        path = Path(tempfile.mkdtemp(prefix="greeting-")) / "hola.wav"
        soundfile.write(path, self.engine.tone("¿Sí? Te escucho."), 48000, subtype="PCM_16")
        call = self.call()
        await asyncio.wait({call.play_recorded(path, "¿Sí? Te escucho.")})
        self.assertEqual(self.said(), [("agent", "¿Sí? Te escucho.")])

    async def test_his_words_are_shown_once_transcribed_and_fillers_are_not(self):
        import soundfile
        folder = Path(tempfile.mkdtemp(prefix="fillers-text-"))
        soundfile.write(folder / "mmm-ya.wav", self.engine.tone("mmm, ya"), 48000, subtype="PCM_16")
        saved, server.FILLERS = server.FILLERS, folder
        try:
            call = self.call(engine=FakeSttEngine("Revisa la tarea doce, por favor."))
        finally:
            server.FILLERS = saved
        delivered = []

        async def deliver(text):
            delivered.append(text)
        call.deliver = deliver
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * int(1.2 * vad.RATE))
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.8)
        self.assertEqual(call.fillers_played, 1)  # the «mmm, ya» sounded...
        self.assertEqual(self.said(), [("user", "Revisa la tarea doce, por favor.")])  # ...but only his words show

    async def test_an_echo_or_empty_transcription_is_not_shown(self):
        call = self.call(engine=FakeSttEngine(""))
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * int(0.5 * vad.RATE))
        await asyncio.wait({call.stt_chain})
        self.assertEqual(self.said(), [])

    async def test_nods_and_waiting_phrases_are_not_shown(self):
        call = self.call()
        tone = self.engine.tone("ajá")
        call.nod = server.Segment("asentir/aja", tone)
        call.out.enqueue(call.nod)
        call.filler = server.Segment("espera/otro/dejame-ver", tone)
        call.out.enqueue(call.filler)
        await call.filler.done.wait()
        self.assertEqual(self.said(), [])

    async def test_nothing_is_written_to_disk(self):
        before = set(Path(TMP).rglob("*"))
        call = self.call(engine=FakeSttEngine("Apunta esto solo en pantalla."))
        call.deliver = lambda text: asyncio.sleep(0)
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * int(0.5 * vad.RATE))
        await asyncio.wait({call.stt_chain})
        call.engine = self.engine
        await asyncio.wait({call.queue_speech("Vale, lo dejo solo en la pantalla.")})
        after = set(Path(TMP).rglob("*"))
        for path in after - before:
            if path.is_file():
                self.assertNotIn(b"pantalla", path.read_bytes(), path)
        self.assertEqual(len(self.said()), 2)


class PausedOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_output_is_silent_and_keeps_its_place(self):
        out = server.OutboundAudio()
        segment = server.Segment("x", np.full(server.OUT_FRAME * 10, 5000, dtype=np.int16))
        out.enqueue(segment)
        await out.recv()
        before = segment.position
        out.paused = True
        for _ in range(3):
            frame = await out.recv()
            self.assertEqual(int(np.abs(frame.to_ndarray()).max()), 0)
        self.assertEqual(segment.position, before)
        out.paused = False
        frame = await out.recv()
        self.assertGreater(int(np.abs(frame.to_ndarray()).max()), 0)
        self.assertEqual(segment.position, before + server.OUT_FRAME)


class WaitPhraseTests(unittest.TestCase):
    def clips(self):
        z = np.zeros(10, dtype=np.int16)
        return [("primero-revisando", z), ("despues-ya-casi", z), ("despues-sigo", z)]

    def test_the_first_phrase_comes_first(self):
        for _ in range(10):
            self.assertEqual(server.pick_wait_phrase(self.clips(), 0, None)[0], "primero-revisando")

    def test_later_phrases_vary_and_never_repeat_the_last(self):
        seen = set()
        for _ in range(30):
            name = server.pick_wait_phrase(self.clips(), 1, "despues-ya-casi")[0]
            self.assertTrue(name.startswith("despues"))
            self.assertNotEqual(name, "despues-ya-casi")
            seen.add(name)
        self.assertEqual(seen, {"despues-sigo"})

    def test_without_clips_there_is_nothing_to_say(self):
        self.assertIsNone(server.pick_wait_phrase([], 0, None))

    def test_only_firsts_available_still_works_later(self):
        only_first = [("primero-revisando", np.zeros(10, dtype=np.int16))]
        self.assertEqual(server.pick_wait_phrase(only_first, 3, None)[0], "primero-revisando")


class WaitPlanTests(unittest.TestCase):
    """The schedule of «still working» cues, driven by a fake clock (seconds since his message)."""

    def play_through(self, plan, until, step=0.5):
        """What the loop would do polling every `step` seconds, everything it asks for played at once."""
        events, t = [], 0.0
        while t <= until:
            kind = plan.due(t)
            if kind:
                plan.played(kind, t)
                events.append((kind, t))
            t += step
        return events

    def test_nothing_before_the_first_wait(self):
        plan = server.WaitPlan()
        self.assertIsNone(plan.due(0))
        self.assertIsNone(plan.due(server.WAIT_FIRST_SECONDS - 0.1))
        self.assertEqual(plan.due(server.WAIT_FIRST_SECONDS), "voice")

    def test_four_phrases_then_only_the_soft_signal(self):
        events = self.play_through(server.WaitPlan(), 100)
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[:4], ["voice"] * 4)
        self.assertTrue(kinds[4:] and all(kind == "chime" for kind in kinds[4:]))
        self.assertEqual(kinds.count("voice"), server.WAIT_MAX_PHRASES)

    def test_spacing_between_cues(self):
        events = self.play_through(server.WaitPlan(), 120)
        times = [t for _, t in events]
        self.assertEqual(times[0], server.WAIT_FIRST_SECONDS)
        for (kind, before), (_, after) in zip(events[:3], events[1:4]):
            self.assertGreaterEqual(after - before, server.WAIT_REPEAT_SECONDS)
        for (_, before), (_, after) in zip(events[3:], events[4:]):
            self.assertGreaterEqual(after - before, server.WAIT_CHIME_SECONDS)

    def test_a_cue_that_could_not_play_is_not_counted_and_comes_as_soon_as_free(self):
        plan = server.WaitPlan()
        self.assertEqual(plan.due(4.0), "voice")  # busy: the loop does not call played()
        self.assertEqual(plan.due(4.5), "voice")
        plan.played("voice", 4.5)
        self.assertIsNone(plan.due(5.0))
        self.assertEqual(plan.due(4.5 + server.WAIT_REPEAT_SECONDS), "voice")

    def test_it_gives_up_after_the_limit(self):
        plan = server.WaitPlan()
        self.assertTrue(plan.over(server.WAIT_MAX_SECONDS))
        self.assertFalse(plan.over(server.WAIT_MAX_SECONDS - 1))
        self.assertIsNone(plan.due(server.WAIT_MAX_SECONDS + 5))


class RealSpeechPauseTests(unittest.TestCase):
    """With the real Silero and real voice: breathing pauses of a long talk are reported, the turn stays open."""

    def test_a_long_talk_with_short_pauses_gives_pauses_after_fifteen_seconds_and_no_stop(self):
        phrases = ["Estoy revisando el informe del trimestre y quiero que me digas qué opinas de las cifras.",
                   "Fíjate que las ventas del segundo mes bajaron bastante frente a lo que habíamos previsto.",
                   "Creo que hay que mirar con calma el costo de los envíos antes de decidir nada más.",
                   "Además me preocupa que el equipo no tenga tiempo para revisar todo esto esta semana.",
                   "Por eso quería contarte cómo lo veo y que luego me ayudes a ordenar las prioridades."]
        gap = noise(0.45, -60)
        def trimmed(text):  # the synthesizer leaves quiet at both ends: a real talk has only the pause between phrases
            pcm = speech(text, 16000)
            loud = np.flatnonzero(np.abs(pcm.astype(np.int32)) > 0.02 * np.abs(pcm).max())
            return pcm[loud[0]:loud[-1]]

        audio = np.concatenate([np.concatenate([trimmed(text), gap]) for text in phrases])
        self.assertGreater(len(audio) / 16000, 17)
        detector = vad.TurnDetector(pause_ms=server.NOD_PAUSE_MS)
        pauses, kinds = [], []
        for i in range(0, len(audio), 1600):  # 0.1 s at a time, as the phone delivers it
            for kind, _ in detector.feed(audio[i:i + 1600].tobytes()):
                kinds.append(kind)
                if kind == "pause":
                    pauses.append(detector.spoken_ms / 1000)
        self.assertEqual(kinds[0], "start")
        self.assertNotIn("stop", kinds)
        self.assertGreaterEqual(len(pauses), 4, pauses)
        self.assertTrue(any(p >= server.NOD_AFTER_SECONDS for p in pauses), pauses)


class NodPlanTests(unittest.TestCase):
    """When a soft «ajá» is due while he talks on: after 15 s of talk, then one every 10-15 s (fake clock)."""

    def test_nothing_before_he_has_talked_long_enough(self):
        plan = server.NodPlan()
        self.assertFalse(plan.due(spoken=server.NOD_AFTER_SECONDS - 0.1, now=100))
        self.assertTrue(plan.due(spoken=server.NOD_AFTER_SECONDS, now=100))

    def test_spaced_apart_by_ten_to_fifteen_seconds(self):
        for seed in range(30):
            plan = server.NodPlan(random.Random(seed))
            plan.nodded(now=100)
            self.assertFalse(plan.due(spoken=40, now=100 + server.NOD_GAP_SECONDS[0] - 0.1))
            self.assertTrue(plan.due(spoken=40, now=100 + server.NOD_GAP_SECONDS[1]))

    def test_a_nod_that_did_not_play_is_due_again_at_once(self):
        plan = server.NodPlan()
        self.assertTrue(plan.due(spoken=20, now=100))
        self.assertTrue(plan.due(spoken=20.5, now=100.5))  # nodded() was never called: the line was busy

    def test_the_gap_range_is_the_one_asked_for(self):
        self.assertEqual(server.NOD_GAP_SECONDS, (10.0, 15.0))
        self.assertLessEqual(server.NOD_MAX_SECONDS, 0.6)
        self.assertLess(server.NOD_GAIN, 1.0)


class NodClipTests(unittest.TestCase):
    def test_only_short_clips_are_kept_and_quieter(self):
        import soundfile
        folder = Path(TMP) / "nod-clips"
        folder.mkdir(exist_ok=True)
        tone = lambda seconds: (10000 * np.sin(np.arange(int(seconds * 48000)) / 20)).astype(np.int16)  # noqa: E731
        soundfile.write(folder / "aja.wav", tone(0.4), 48000, subtype="PCM_16")
        soundfile.write(folder / "largo.wav", tone(0.9), 48000, subtype="PCM_16")
        loaded = server.load_nod_clips(folder)
        self.assertEqual([name for name, _ in loaded], ["aja"])
        self.assertLess(int(np.abs(loaded[0][1]).max()), 10000 * server.NOD_GAIN + 2)

    def test_the_recorded_nods_are_the_four_asked_for_and_short(self):
        import make_fillers
        self.assertEqual(sorted(make_fillers.NODS.values()), ["Ajá.", "Mm.", "Sí.", "Ya."])


class NodInCallTests(unittest.IsolatedAsyncioTestCase):
    """The call nods in a natural pause of a long talk, never over a voice, and the nod is not a turn."""

    NAMES = ("aja", "mm", "ya", "si")

    async def asyncSetUp(self):
        import soundfile
        patch = FakeProbability()
        patch.__enter__()
        self.addCleanup(patch.__exit__)
        self.folder = Path(tempfile.mkdtemp(prefix="fillers-nod-"))
        tone = (9000 * np.sin(2 * np.pi * 440 * np.arange(int(0.4 * 48000)) / 48000)).astype(np.int16)
        (self.folder / "asentir").mkdir()
        for name in self.NAMES:
            soundfile.write(self.folder / "asentir" / f"{name}.wav", tone, 48000, subtype="PCM_16")
        (self.folder / "mm").mkdir()
        soundfile.write(self.folder / "mm" / "mm.wav", tone, 48000, subtype="PCM_16")
        (self.folder / "pregunta").mkdir()
        soundfile.write(self.folder / "pregunta" / "a-ver.wav", tone, 48000, subtype="PCM_16")
        soundfile.write(self.folder / "dime.wav", tone, 48000, subtype="PCM_16")
        self.saved = server.FILLERS
        server.FILLERS = self.folder
        self.flag = Path(TMP) / "asentir-on"
        self.flag.touch()
        self.altavoz = Path(TMP) / "asentir-altavoz"
        self.jev_flag = Path(TMP) / "jev-on"
        self.now = [1000.0]

    async def asyncTearDown(self):
        server.FILLERS = self.saved
        for flag in (self.flag, self.altavoz, self.jev_flag):
            flag.unlink(missing_ok=True)

    def call(self, jev=None, speaker=False):
        call = server.Call("nod-test", FakeSttEngine("hola"), agent="coordinador", inbound=True, jev=jev)
        call.speaker = speaker
        call.clock = lambda: self.now[0]
        return call

    def talk(self, call, seconds):
        call._feed(level(seconds, VOICE).tobytes())

    def pause(self, call, seconds=0.4):
        call._feed(level(seconds, QUIET).tobytes())

    def queued(self, call):
        return [seg.text for seg in call.out.queue]

    async def test_a_nod_comes_in_the_first_pause_after_fifteen_seconds(self):
        call = self.call()
        self.talk(call, 16)
        self.assertEqual(self.queued(call), [])        # nothing in the middle of his words
        self.pause(call)
        self.assertEqual(len(self.queued(call)), 1)
        self.assertIn(self.queued(call)[0], self.NAMES)
        self.assertEqual(call.nods_played, 1)

    async def test_no_nod_in_a_short_talk(self):
        call = self.call()
        self.talk(call, 10)
        self.pause(call)
        self.assertEqual(self.queued(call), [])

    async def test_the_nod_is_not_a_turn_and_not_a_filler(self):
        call = self.call()
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual((call.utterance_seq, call.turn_end_at, call.fillers_played, call.filler_this_turn),
                         (0, 0.0, 0, False))
        self.assertEqual((call.transcript, call.agent_pending, call.utterances), ([], [], []))
        self.assertTrue(call.user_speaking)
        self.assertTrue(call._only_fillers_queued())   # so his voice over it is not taken for an interruption
        self.assertFalse(call.detector.barge_mode)

    async def test_while_the_nod_may_echo_the_detector_distrusts_weak_voice(self):
        call = self.call()
        self.talk(call, 16)
        self.pause(call)
        self.assertGreater(call.detector.echo_ms, 400)

    async def test_one_nod_every_ten_to_fifteen_seconds_never_the_same_clip_twice(self):
        call = self.call()
        names = []
        self.talk(call, 16)
        for _ in range(6):
            self.pause(call)
            names.append(self.queued(call)[0])
            call.out.queue.clear()
            self.talk(call, 1)
            self.pause(call)                           # too soon: no second nod
            self.assertEqual(self.queued(call), [])
            self.now[0] += 15.5
            self.talk(call, 1)
        self.assertEqual(call.nods_played, 6)
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])), names)

    async def test_his_voice_returning_cuts_the_nod(self):
        call = self.call()
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual(len(call.out.queue), 1)
        self.talk(call, 0.3)
        self.assertEqual(self.queued(call), [])

    async def test_never_over_the_agents_voice(self):
        call = self.call()
        call.out.enqueue(server.Segment("Respuesta larga del agente", np.zeros(48000 * 3, dtype=np.int16)))
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual(call.nods_played, 0)
        self.assertEqual(self.queued(call), ["Respuesta larga del agente"])

    async def test_nothing_while_an_interruption_is_being_judged(self):
        call = self.call()
        call.hold = True
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual(call.nods_played, 0)

    async def test_off_without_the_flag(self):
        self.flag.unlink()
        call = self.call()
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual(call.nods_played, 0)

    async def test_by_speaker_only_when_explicitly_allowed(self):
        for speaker in (True, None):
            call = self.call(speaker=speaker)
            self.talk(call, 16)
            self.pause(call)
            self.assertEqual(call.nods_played, 0, speaker)
        self.altavoz.touch()
        call = self.call(speaker=True)
        self.talk(call, 16)
        self.pause(call)
        self.assertEqual(call.nods_played, 1)

    async def test_the_app_tells_which_output_is_in_use(self):
        call = self.call(speaker=None)
        call.phone_route({"type": "route", "speaker": False})
        self.assertIs(call.speaker, False)
        call.phone_route({"type": "route", "speaker": True})
        self.assertIs(call.speaker, True)

    async def test_the_acknowledgement_that_follows_a_nod_is_the_fitted_one_not_another_mm(self):
        self.jev_flag.touch()
        jev = FakeJev(category="pregunta")
        call = self.call(jev=jev)
        self.talk(call, 16)
        self.pause(call)
        call.out.queue.clear()                         # the nod has played
        self.now[0] += 0.9
        call._speech_stopped(b"\x00\x00" * int(1.0 * vad.RATE))
        self.assertIsNone(call.mm_task)
        await asyncio.wait({call.stt_chain})
        await asyncio.sleep(0.3)
        self.assertEqual(call.filler_name, "pregunta/a-ver")
        self.assertEqual(call.fillers_played, 1)

    async def test_without_a_recent_nod_the_mm_still_comes(self):
        self.jev_flag.touch()
        call = self.call(jev=FakeJev(category="pregunta"))
        call._speech_stopped(b"\x00\x00" * int(1.0 * vad.RATE))
        self.assertIsNotNone(call.mm_task)
        await asyncio.wait({call.stt_chain})


class WaitClipTests(unittest.TestCase):
    def sets(self):
        z = np.zeros(10, dtype=np.int16)
        return {"pregunta": [("pregunta/dejame", z), ("pregunta/mirando", z)],
                "orden": [("orden/haciendo", z)], "otro": [("otro/momento", z)],
                "seguir": [("seguir/sigo", z), ("seguir/casi", z)]}

    def test_the_first_phrase_fits_the_category(self):
        for _ in range(20):
            self.assertTrue(server.pick_wait_clip(self.sets(), 0, "pregunta", None)[0].startswith("pregunta/"))
            self.assertEqual(server.pick_wait_clip(self.sets(), 0, "orden", None)[0], "orden/haciendo")

    def test_without_a_decision_or_a_recording_for_it_the_generic_one_is_used(self):
        self.assertEqual(server.pick_wait_clip(self.sets(), 0, None, None)[0], "otro/momento")
        self.assertEqual(server.pick_wait_clip(self.sets(), 0, "despedida", None)[0], "otro/momento")

    def test_later_ones_are_the_short_generic_ones_and_never_repeat(self):
        for _ in range(20):
            name = server.pick_wait_clip(self.sets(), 1, "pregunta", "seguir/sigo")[0]
            self.assertEqual(name, "seguir/casi")

    def test_nothing_to_say_without_clips(self):
        self.assertIsNone(server.pick_wait_clip({}, 0, "pregunta", None))

    def test_the_old_waiting_folder_still_serves_when_there_is_no_espera(self):
        z = np.zeros(10, dtype=np.int16)
        legacy = [("primero-revisando", z), ("despues-sigo", z)]
        sets = server.legacy_wait_sets(legacy)
        self.assertEqual(server.pick_wait_clip(sets, 0, "pregunta", None)[0], "primero-revisando")
        self.assertEqual(server.pick_wait_clip(sets, 2, "pregunta", None)[0], "despues-sigo")


class AgentMessageTests(unittest.TestCase):
    """What the agent reads must keep its own words apart from the user's."""

    def test_plain_answer_is_labelled_as_user(self):
        message = server.agent_message("Hola, ¿me escuchas?", None, introduced=True)
        self.assertEqual(message, "[llamada] USUARIO dijo: Hola, ¿me escuchas?")

    def test_first_message_carries_intro_then_user_label(self):
        message = server.agent_message("Hola", None, introduced=False)
        self.assertTrue(message.startswith(server.INTRO))
        self.assertTrue(message.endswith("USUARIO dijo: Hola"))

    def test_the_intro_asks_to_say_first_what_it_already_knows(self):
        self.assertIn("primero lo que ya tienes claro", server.INTRO)
        self.assertIn("suenan seguidos", server.INTRO)

    def test_the_text_intro_offers_pieces_that_reach_hermes_together(self):
        self.assertIn('llamada decir --sigue "…"', server.INTRO_TEXTO)

    def test_interruption_separates_what_agent_said_from_what_user_said(self):
        cut = {"heard": "Primero voy a correr", "not_heard": "las pruebas y luego reviso"}
        message = server.agent_message("espera, para", cut, introduced=True)
        self.assertIn("USUARIO dijo: espera, para", message)
        self.assertIn("YO dije (él alcanzó a oír): «Primero voy a correr»", message)
        self.assertIn("YO no alcancé a decir: «las pruebas y luego reviso»", message)
        self.assertLess(message.index("USUARIO dijo"), message.index("YO dije"))

    def test_interruption_with_nothing_heard_says_so(self):
        message = server.agent_message("espera", {"heard": "", "not_heard": "Hola Ana"}, introduced=True)
        self.assertIn("YO dije (él alcanzó a oír): «(nada)»", message)


class WhisperFilterTests(unittest.TestCase):
    """Short real phrases on phone audio score low; only silence and loops should be dropped."""

    @staticmethod
    def segment(text, no_speech=0.1, logprob=-0.5, compression=1.5):
        return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob,
                "compression_ratio": compression}

    def test_short_real_phrases_with_modest_confidence_are_kept(self):
        import engine
        for text, logprob in (("¿Qué pasa?", -1.2), ("igual.", -1.3), ("desde", -1.4)):
            self.assertTrue(engine.keep_segment(self.segment(text, logprob=logprob)), text)

    def test_silence_is_dropped(self):
        import engine
        self.assertFalse(engine.keep_segment(self.segment("Gracias.", no_speech=0.7, logprob=-1.2)))
        self.assertFalse(engine.keep_segment(self.segment("Gracias.", no_speech=0.95)))

    def test_loops_and_garbage_are_dropped(self):
        import engine
        self.assertFalse(engine.keep_segment(self.segment("no, no, no, no, no, no, no, no, no")))
        self.assertFalse(engine.keep_segment(self.segment("blah", compression=3.0)))
        self.assertFalse(engine.keep_segment(self.segment("xx", logprob=-2.5)))

    def test_known_hallucinations_are_dropped_even_when_whisper_is_sure(self):
        """large-v3-turbo reports no_speech 0.0 on silence and invents these stock phrases."""
        import engine
        for text in ("Gracias por ver el video.", "Gracias por ver el vídeo.", "¡Suscríbete al canal!",
                     "Sincronización por la comunidad.", "Subtítulos realizados por la comunidad de Amara.org",
                     "Subtítulos por Amara.org"):
            self.assertFalse(engine.keep_segment(self.segment(text, no_speech=0.0, logprob=-0.4)), text)

    def test_a_plain_thanks_is_real_speech(self):
        import engine
        self.assertTrue(engine.keep_segment(self.segment("Gracias.", no_speech=0.0, logprob=-0.4)))
        self.assertTrue(engine.keep_segment(self.segment("Ok, gracias por todo.", no_speech=0.0, logprob=-0.4)))

    def test_prompt_names_the_project_vocabulary(self):
        import engine
        for word in ("firstmate", "secondmate", "llamada", "Tailscale"):
            self.assertIn(word, engine.INITIAL_PROMPT)


class HangUpReleasesTests(unittest.IsolatedAsyncioTestCase):
    """However the call ends, the server lets go of its side: the phone's socket and its WebRTC connection are
    closed and /status no longer says the phone is connected (an ended call with connected=true looked like a
    phone still holding the call)."""

    class Engine:
        def start(self): pass
        def release_after(self, seconds): self.released = seconds
        def warm(self, seconds): pass

    async def asyncSetUp(self):
        self.server = server.Server({"phone_token": "secret", "local_port": 0, "phone_port": 0,
                                     "phone_host": "127.0.0.1"})
        self.server.engine = self.Engine()
        self.runners = []
        for app, port in zip(server.build_apps(self.server), (18788, 18789)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            self.runners.append(runner)
        self.http = aiohttp.ClientSession()
        self.phone_pc = RTCPeerConnection()

    async def asyncTearDown(self):
        await self.phone_pc.close()
        await self.http.close()
        for runner in self.runners:
            await runner.cleanup()

    async def answer(self, agent=None):
        call = server.Call("hangup-test", self.server.engine, agent=agent)
        self.server.call = call
        ws = await self.http.ws_connect("http://127.0.0.1:18789/ws?call_id=hangup-test&token=secret")
        self.phone_pc.addTrack(FakeMic())
        await self.phone_pc.setLocalDescription(await self.phone_pc.createOffer())
        await ws.send_str(json.dumps({"type": "offer", "sdp": self.phone_pc.localDescription.sdp}))
        answer = json.loads((await ws.receive()).data)
        self.assertEqual(answer["type"], "answer")
        return call, ws

    async def status(self):
        async with self.http.get("http://127.0.0.1:18788/status") as response:
            return (await response.json())["call"]

    async def until_closed(self, call):
        deadline = time.monotonic() + 5
        while call.pc.connectionState != "closed" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def test_while_the_phone_is_on_the_call_it_is_connected(self):
        call, ws = await self.answer()
        self.assertEqual(await self.status(), {"id": "hangup-test", "connected": True, "ended": None})
        await ws.close()

    async def test_the_phone_hanging_up_leaves_it_disconnected_and_closes_the_webrtc_side(self):
        call, ws = await self.answer()
        await ws.send_str(json.dumps({"type": "hangup"}))
        await ws.receive()  # the server closes the socket
        await self.until_closed(call)
        self.assertEqual(await self.status(), {"id": "hangup-test", "connected": False, "ended": "colgó"})
        self.assertEqual(call.pc.connectionState, "closed")

    async def test_the_phone_vanishing_leaves_it_disconnected_and_closes_the_webrtc_side(self):
        call, ws = await self.answer()
        await ws.close()
        await call.ended_event.wait()
        await self.until_closed(call)
        self.assertEqual(await self.status(), {"id": "hangup-test", "connected": False, "ended": "colgó"})
        self.assertEqual(call.pc.connectionState, "closed")

    async def test_colgar_from_the_mac_tells_the_phone_and_closes_its_side(self):
        call, ws = await self.answer()
        async with self.http.post("http://127.0.0.1:18788/end", json={}) as response:
            self.assertEqual((await response.json())["status"], "terminada")
        self.assertEqual(json.loads((await ws.receive()).data), {"type": "end"})
        self.assertIn((await ws.receive()).type, {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED})
        self.assertEqual(await self.status(), {"id": "hangup-test", "connected": False, "ended": "terminada"})
        self.assertEqual(call.pc.connectionState, "closed")


class BargeStatsTests(unittest.TestCase):
    def test_barge_start_reports_how_it_triggered(self):
        detector = vad.TurnDetector()
        detector.barge_mode = True
        voice = speech("Sí, apruébalo ahora mismo.", 16000)
        events = detector.feed(np.concatenate([noise(0.3, -60), voice]).tobytes())
        self.assertIn("start", [e[0] for e in events])
        self.assertTrue(detector.last_start["barged"])
        self.assertGreaterEqual(detector.last_start["voiced_ms"], vad.BARGE_VOICED_MS)
        self.assertGreater(detector.last_start["max_p"], vad.BARGE_THRESHOLD)


class FakeMic(MediaStreamTrack):
    """The simulated phone's microphone: silence unless told to speak."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.buffer = np.zeros(0, dtype=np.int16)
        self.start = None
        self.pts = 0

    def say(self, pcm48: np.ndarray):
        self.buffer = np.concatenate([self.buffer, pcm48])

    async def recv(self):
        if self.start is None:
            self.start = time.monotonic()
        wait = self.start + self.pts / 48000 - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        frame = np.zeros(960, dtype=np.int16)
        take = min(960, self.buffer.size)
        frame[:take] = self.buffer[:take]
        self.buffer = self.buffer[take:]
        audio = av.AudioFrame.from_ndarray(frame.reshape(1, -1), format="s16", layout="mono")
        audio.sample_rate, audio.pts, audio.time_base = 48000, self.pts, fractions.Fraction(1, 48000)
        self.pts += 960
        return audio


class FakePhone:
    """Answers over the real WebSocket + WebRTC path and records what Claude sends."""

    def __init__(self, http: aiohttp.ClientSession):
        self.http = http
        self.mic = FakeMic()
        self.states: list[tuple[float, str]] = []
        self.loud: list[float] = []   # times Claude's audio was audibly speech
        self.ws = None
        self.pc = None
        self.closed = asyncio.Event()

    async def answer(self, call_id: str):
        self.ws = await self.http.ws_connect(f"http://127.0.0.1:18771/ws?call_id={call_id}&token=secret")
        self.pc = RTCPeerConnection()
        self.pc.addTrack(self.mic)

        @self.pc.on("track")
        def on_track(track):
            asyncio.ensure_future(self._listen(track))

        await self.pc.setLocalDescription(await self.pc.createOffer())
        await self.ws.send_str(json.dumps({"type": "offer", "sdp": self.pc.localDescription.sdp}))
        asyncio.ensure_future(self._control())

    async def _control(self):
        async for message in self.ws:
            data = json.loads(message.data)
            if data["type"] == "answer":
                await self.pc.setRemoteDescription(RTCSessionDescription(data["sdp"], "answer"))
            elif data["type"] == "state":
                self.states.append((time.monotonic(), data["value"]))
            elif data["type"] == "end":
                break
        self.closed.set()

    async def _listen(self, track):
        try:
            while True:
                frame = await track.recv()
                pcm = frame.to_ndarray().astype(np.float32)
                if np.sqrt(np.mean(pcm * pcm)) > 1500:  # well above the thinking chime
                    self.loud.append(time.monotonic())
        except Exception:
            pass

    async def wait_state(self, value: str, after: float = 0, timeout: float = 60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(v == value and t >= after for t, v in self.states):
                return next(t for t, v in self.states if v == value and t >= after)
            await asyncio.sleep(0.05)
        raise AssertionError(f"phone never saw state {value}: {self.states}")

    async def hang_up(self):
        await self.ws.send_str(json.dumps({"type": "hangup"}))
        await self.pc.close()


PHRASES = ["Sí, apruébalo. Puedes desplegar.", "Espera, espera. No toques la base de datos.",
           "Y otra cosa: avísame cuando termines."]


class RealtimeCallTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        for phrase in PHRASES:  # generate the phone's speech up front, not mid-call
            speech(phrase, 48000)

    async def asyncSetUp(self):
        (Path(TMP) / "device.json").write_text(json.dumps({"fcm_token": "fake"}))
        self.server = server.Server({"phone_token": "secret", "local_port": 18770,
                                     "phone_port": 18771, "phone_host": "127.0.0.1"})
        self.http = aiohttp.ClientSession()
        self.phone = FakePhone(self.http)
        phone = self.phone

        class Push:
            sent = []

            async def send(self, token, data):
                self.sent.append(data)
                if data["type"] == "incoming_call":
                    asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(
                        phone.answer(data["call_id"])))

        self.server.push = Push()
        self.runners = []
        for app, port in zip(server.build_apps(self.server), (18770, 18771)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            self.runners.append(runner)

    async def asyncTearDown(self):
        if self.server.call and not self.server.call.ended:
            await self.post("/end", {"text": ""})
        if self.phone.pc:
            await self.phone.pc.close()
        await self.http.close()
        await self.server.engine.stop()
        for runner in self.runners:
            await runner.cleanup()

    async def post(self, path, body):
        async with self.http.post(f"http://127.0.0.1:18770{path}", json=body) as response:
            return await response.json()

    async def answer_when_listening(self, text: str, after: float = 0):
        await self.phone.wait_state("listening", after)
        await asyncio.sleep(0.4)
        self.phone.mic.say(speech(text, 48000))

    async def test_turn_barge_in_and_talking_while_thinking(self):
        # 1. Normal turn: Claude speaks, then the user answers.
        answering = asyncio.ensure_future(self.answer_when_listening("Sí, apruébalo. Puedes desplegar."))
        result = await self.post("/start", {"text": "Hola, Ana. ¿Apruebo el despliegue?"})
        await answering
        self.assertEqual(result["status"], "ok", result)
        self.assertIn("despleg", result["text"].lower())
        self.assertTrue(self.phone.loud, "phone never heard Claude")

        # 2. Barge-in: the user talks over a long answer; Claude must stop at once.
        long_text = ("Perfecto. Primero voy a correr todas las pruebas. Después reviso los registros "
                     "de la base de datos. Luego actualizo la documentación del proyecto completo. "
                     "Finalmente te mando un resumen con todo lo que cambió.")
        started = time.monotonic()

        async def interrupt():
            speaking_at = await self.phone.wait_state("speaking", started)
            await asyncio.sleep(1.5)
            cut_in = time.monotonic()
            self.phone.mic.say(speech("Espera, espera. No toques la base de datos.", 48000))
            return cut_in

        cutting = asyncio.ensure_future(interrupt())
        result = await self.post("/say", {"text": long_text})
        cut_in = await cutting
        self.assertEqual(result["status"], "interrumpido", result)
        self.assertIn("base de datos", result["text"].lower())
        self.assertTrue(result["heard"].startswith("Perfecto"), result)
        self.assertIn("resumen", result["not_heard"])
        still_talking = [t for t in self.phone.loud if t > cut_in + 0.25 + vad.START_MS / 1000 + 0.3]
        # The phone's own speech is not in `loud` (that is Claude's track only).
        self.assertLess(len(still_talking), 5, "Claude kept talking after the barge-in")

        # 3. The user speaks while Claude is thinking: next turn hands it over, unsaid.
        self.phone.mic.say(speech("Y otra cosa: avísame cuando termines.", 48000))
        await asyncio.sleep(5)
        result = await self.post("/say", {"text": "Entendido, no toco la base de datos."})
        self.assertEqual(result["status"], "nuevo", result)
        self.assertIn("avísame", result["text"].lower())

        # 4. The user hangs up.
        hanging = asyncio.ensure_future(self._hang_up_when_listening())
        result = await self.post("/say", {"text": "Claro, te aviso."})
        await hanging
        self.assertEqual(result["status"], "colgó", result)
        await asyncio.sleep(0.5)
        self.assertIsNotNone(self.server.engine.release)  # models freed later, not now

    async def _hang_up_when_listening(self):
        await self.phone.wait_state("listening", time.monotonic())
        await self.phone.hang_up()

    async def test_no_answer(self):
        self.server.push = type("P", (), {"sent": [], "send": lambda s, t, d: _record(s, d)})()
        server.RING_SECONDS = 1
        try:
            result = await self.post("/start", {"text": "Hola"})
        finally:
            server.RING_SECONDS = 45
        self.assertEqual(result["status"], "sin_respuesta")
        self.assertEqual([m["type"] for m in self.server.push.sent], ["incoming_call", "cancel_call"])


async def _record(sender, data):
    sender.sent.append(data)


if __name__ == "__main__":
    unittest.main()


FAKE_HERDR = """#!/bin/sh
# Fake herdr: records prompts, reports a fixed status.
if [ "$1 $2" = "agent prompt" ]; then printf '%s\\n' "$4" >> "$FAKE_HERDR_LOG"; echo '{}'; exit 0; fi
if [ "$1 $2" = "agent get" ]; then
  [ "$3" = "coordinador" ] || { echo '{"error":{"message":"no agent"}}' >&2; exit 1; }
  echo '{"result":{"agent":{"agent_status":"'"$(cat "$FAKE_HERDR_STATUS" 2>/dev/null || echo idle)"'"}}}'; exit 0
fi
exit 2
"""


class HerdrAgentCallTests(RealtimeCallTests):
    """The user calls the Herdr coordinator; it answers with `llamada decir`."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        folder = Path(tempfile.mkdtemp(prefix="fake-herdr-"))
        script = folder / "herdr"
        script.write_text(FAKE_HERDR)
        script.chmod(0o755)
        self.prompts = folder / "prompts.log"
        os.environ["FAKE_HERDR_LOG"] = str(self.prompts)
        os.environ["FAKE_HERDR_STATUS"] = str(folder / "status")
        import herdr_agent
        herdr_agent.HERDR = str(script)
        self.server.config["contacts"] = [{"id": "coordinador", "name": "Coordinador",
                                           "agent": "coordinador", "greeting": "¿Sí, Ana?"}]

    def prompts_sent(self):
        return self.prompts.read_text().splitlines() if self.prompts.exists() else []

    async def wait_prompts(self, count, timeout=40):
        deadline = time.monotonic() + timeout
        while len(self.prompts_sent()) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        return self.prompts_sent()

    async def phone_post(self, path, body):
        async with self.http.post(f"http://127.0.0.1:18771{path}?token=secret", json=body) as response:
            return response.status, await response.json()

    async def test_turn_barge_in_and_talking_while_thinking(self):
        pass  # covered by RealtimeCallTests; this class is about agent mode

    async def test_no_answer(self):
        pass

    async def test_inbound_call_to_coordinator(self):
        async with self.http.get("http://127.0.0.1:18771/contacts?token=secret") as response:
            contacts = (await response.json())["contacts"]
        self.assertEqual(contacts, [{"id": "coordinador", "name": "Coordinador", "subtitle": "",
                                     "status": "idle"}])
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.phone.answer(dialed["call_id"])

        # Greeting, then the user's words reach the agent with the call intro.
        await self.answer_when_listening("Sí, apruébalo. Puedes desplegar.")
        prompts = await self.wait_prompts(1)
        self.assertTrue(prompts and prompts[0].startswith(f"[llamada] {server.NOMBRE} te llamó"), prompts)
        self.assertIn("despleg", prompts[0].lower())

        # The agent answers with `llamada decir`: returns at once, the phone hears it.
        heard_before = len(self.phone.loud)
        started = time.monotonic()
        result = await self.post("/say", {"text": "Perfecto, lo despliego ahora."})
        self.assertEqual(result["status"], "en_curso")
        self.assertLess(time.monotonic() - started, 1.0)
        await self.phone.wait_state("listening", started + 0.5)
        self.assertGreater(len(self.phone.loud), heard_before)

        # Barge-in on a long answer: the next prompt says what he heard.
        long_text = ("Primero voy a correr todas las pruebas. Después reviso los registros de la "
                     "base de datos. Luego actualizo la documentación. Finalmente te mando un resumen.")
        started = time.monotonic()
        await self.post("/say", {"text": long_text})
        await self.phone.wait_state("speaking", started)
        await asyncio.sleep(1.5)
        self.phone.mic.say(speech(PHRASES[1], 48000))
        prompts = await self.wait_prompts(2)
        self.assertIn("base de datos", prompts[1].lower())
        self.assertIn("USUARIO dijo:", prompts[1])
        self.assertIn("YO dije (él alcanzó a oír):", prompts[1])

        # He hangs up: the agent is told.
        await self.phone.hang_up()
        prompts = await self.wait_prompts(3)
        self.assertEqual(prompts[2], f"[llamada] {server.NOMBRE} colgó la llamada.")
        await asyncio.sleep(1)
        self.assertIsNotNone(self.server.engine.release)

    async def test_speech_with_short_pauses_reaches_the_agent_as_one_message(self):
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.phone.answer(dialed["call_id"])
        await self.phone.wait_state("listening")
        await asyncio.sleep(0.5)
        # Two phrases with a pause between them that is longer than a turn end but shorter than a hang-on.
        self.phone.mic.say(speech(PHRASES[0], 48000))
        self.phone.mic.say(np.zeros(int(1.1 * 48000), dtype="<i2"))
        self.phone.mic.say(speech(PHRASES[1], 48000))
        prompts = await self.wait_prompts(1)
        await asyncio.sleep(server.AGENT_JOIN_SECONDS + 8)  # a second, separate message would show up here
        prompts = self.prompts_sent()
        self.assertEqual(len(prompts), 1, prompts)
        self.assertIn("despleg", prompts[0].lower())
        self.assertIn("base de datos", prompts[0].lower())
        await self.phone.hang_up()

    def seed_filler(self):
        import soundfile
        folder = Path(TMP) / "fillers"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*.wav"):
            old.unlink()
        tone = (9000 * np.sin(2 * np.pi * 440 * np.arange(int(1.5 * 48000)) / 48000)).astype(np.int16)
        soundfile.write(folder / "ya.wav", tone, 48000, subtype="PCM_16")

    async def start_agent_call(self):
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.phone.answer(dialed["call_id"])
        spoke = await self.phone.wait_state("speaking")  # the greeting
        await self.phone.wait_state("listening", spoke)
        await asyncio.sleep(0.5)

    async def test_filler_plays_after_he_stops_and_is_cut_when_the_agent_speaks(self):
        self.seed_filler()
        await self.start_agent_call()
        heard_before = len(self.phone.loud)
        self.phone.mic.say(speech(PHRASES[0], 48000))
        # He stops talking; before the agent has said anything the line is not empty.
        deadline = time.monotonic() + 15
        while len(self.phone.loud) == heard_before and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        self.assertGreater(len(self.phone.loud), heard_before, "no filler was heard")
        self.assertEqual(self.prompts_sent(), [])  # still nothing sent to the agent: the filler came first
        # The agent answers: no leftover filler under the answer, and no interruption is reported.
        said_at = time.monotonic()
        await self.post("/say", {"text": "Perfecto, lo despliego ahora."})
        await self.phone.wait_state("speaking", said_at)
        self.assertFalse(any(seg.text == "ya" for seg in self.server.call.out.queue))
        await self.phone.hang_up()

    async def test_only_one_filler_per_turn(self):
        self.seed_filler()
        await self.start_agent_call()
        self.phone.mic.say(speech(PHRASES[0], 48000))
        self.phone.mic.say(np.zeros(int(1.4 * 48000), dtype="<i2"))
        self.phone.mic.say(speech(PHRASES[1], 48000))
        await self.wait_prompts(1)
        self.assertEqual(self.server.call.fillers_played, 1)
        await self.phone.hang_up()

    async def test_speaking_over_a_filler_is_not_a_barge_in(self):
        self.seed_filler()
        await self.start_agent_call()
        self.phone.mic.say(speech(PHRASES[0], 48000))
        deadline = time.monotonic() + 15
        while self.server.call.fillers_played == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        self.assertEqual(self.server.call.fillers_played, 1)
        self.phone.mic.say(speech(PHRASES[2], 48000))  # keeps talking over the filler
        prompts = await self.wait_prompts(1)
        await asyncio.sleep(1)
        self.assertNotIn("interrumpió", " ".join(self.prompts_sent()))
        self.assertIn("despleg", self.prompts_sent()[0].lower())
        await self.phone.hang_up()

    def seed_waiting(self):
        import soundfile
        folder = Path(TMP) / "waiting"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*.wav"):
            old.unlink()
        tone = (9000 * np.sin(2 * np.pi * 300 * np.arange(int(0.6 * 48000)) / 48000)).astype(np.int16)
        for name in ("primero-revisando", "despues-ya-casi", "despues-sigo"):
            soundfile.write(folder / f"{name}.wav", tone, 48000, subtype="PCM_16")

    async def test_while_the_agent_is_silent_the_line_gets_reassurance_until_it_answers(self):
        self.seed_waiting()
        saved = server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS
        server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS = 1.0, 1.5
        try:
            await self.start_agent_call()
            self.phone.mic.say(speech(PHRASES[0], 48000))
            await self.wait_prompts(1)  # delivered; the agent says nothing
            deadline = time.monotonic() + 20
            while self.server.call.waits_played < 3 and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            self.assertGreaterEqual(self.server.call.waits_played, 3)
            # The agent finally answers: no more reassurance, and none left queued under the answer.
            said_at = time.monotonic()
            await self.post("/say", {"text": "Listo, ya lo revisé."})
            await self.phone.wait_state("speaking", said_at)
            played = self.server.call.waits_played
            await asyncio.sleep(4)
            self.assertEqual(self.server.call.waits_played, played)
        finally:
            server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS = saved
        await self.phone.hang_up()

    def seed_espera(self):
        import shutil
        import soundfile
        folder = Path(TMP) / "fillers" / "espera"
        shutil.rmtree(folder, ignore_errors=True)
        tone = (9000 * np.sin(2 * np.pi * 300 * np.arange(int(0.3 * 48000)) / 48000)).astype(np.int16)
        for sub, name in (("pregunta", "dejame"), ("otro", "momento"), ("seguir", "sigo"), ("seguir", "casi"),
                          ("senal", "suave")):
            (folder / sub).mkdir(parents=True, exist_ok=True)
            soundfile.write(folder / sub / f"{name}.wav", tone, 48000, subtype="PCM_16")

    async def test_four_phrases_then_only_the_soft_signal_and_none_after_the_answer(self):
        self.seed_espera()
        saved = (server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS, server.WAIT_CHIME_SECONDS)
        server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS, server.WAIT_CHIME_SECONDS = 0.5, 0.7, 0.7
        try:
            await self.start_agent_call()
            self.phone.mic.say(speech(PHRASES[0], 48000))
            await self.wait_prompts(1)
            call = self.server.call
            call.wait_category = "pregunta"
            deadline = time.monotonic() + 20
            while call.chimes_played < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            self.assertEqual(call.waits_played, server.WAIT_MAX_PHRASES)
            self.assertGreaterEqual(call.chimes_played, 2)
            said_at = time.monotonic()
            await self.post("/say", {"text": "Listo, ya lo revisé."})
            await self.phone.wait_state("speaking", said_at)
            played = (call.waits_played, call.chimes_played)
            await asyncio.sleep(2)
            self.assertEqual((call.waits_played, call.chimes_played), played)
        finally:
            server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS, server.WAIT_CHIME_SECONDS = saved
        await self.phone.hang_up()

    async def test_nothing_is_said_over_him(self):
        self.seed_espera()
        saved = server.WAIT_FIRST_SECONDS
        server.WAIT_FIRST_SECONDS = 0.5
        try:
            await self.start_agent_call()
            self.phone.mic.say(speech(PHRASES[0], 48000))
            await self.wait_prompts(1)
            call = self.server.call
            call.user_speaking = True  # he is talking again
            await asyncio.sleep(2)
            self.assertEqual(call.waits_played, 0)
            call.user_speaking = False
            deadline = time.monotonic() + 5
            while call.waits_played == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            self.assertEqual(call.waits_played, 1)
        finally:
            server.WAIT_FIRST_SECONDS = saved
        await self.phone.hang_up()

    async def test_no_reassurance_when_the_agent_answers_quickly(self):
        self.seed_waiting()
        saved = server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS
        server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS = 3.0, 3.0
        try:
            await self.start_agent_call()
            self.phone.mic.say(speech(PHRASES[0], 48000))
            await self.wait_prompts(1)
            await self.post("/say", {"text": "Sí, adelante."})
            await asyncio.sleep(6)
            self.assertEqual(self.server.call.waits_played, 0)
        finally:
            server.WAIT_FIRST_SECONDS, server.WAIT_REPEAT_SECONDS = saved
        await self.phone.hang_up()

    async def long_agent_speech(self):
        await self.start_agent_call()
        said_at = time.monotonic()
        await self.post("/say", {"text": "Primero voy a correr todas las pruebas. Después reviso los registros "
                                         "de la base de datos. Luego actualizo la documentación. Finalmente te mando un resumen."})
        await self.phone.wait_state("speaking", said_at)
        await asyncio.sleep(1.0)
        return self.server.call

    async def test_a_false_interruption_resumes_the_speech_where_it_stopped(self):
        call = await self.long_agent_speech()
        call._speech_started()  # voice detected over the agent...
        self.assertTrue(call.out.paused)
        place = call.out.queue[0].position
        await asyncio.sleep(0.6)
        self.assertEqual(call.out.queue[0].position, place)  # ...it stops, but nothing is thrown away
        self.assertFalse(call.barge_in.is_set())
        call._speech_stopped(b"\x00\x00" * 8000)
        text = call._resolve_hold(call.hold_seq, "")  # ...and nothing intelligible was said
        self.assertEqual(text, "")
        self.assertFalse(call.out.paused)
        resumed_at = time.monotonic()
        await self.phone.wait_state("listening", resumed_at)  # it finished the whole speech
        self.assertIsNone(call.pending_interruption)
        self.assertEqual(self.prompts_sent(), [])  # the agent was never told he interrupted
        await self.phone.hang_up()

    async def test_real_words_over_the_agent_cut_it_for_good(self):
        call = await self.long_agent_speech()
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * 8000)
        text = call._resolve_hold(call.hold_seq, "espera, no toques la base de datos")
        self.assertEqual(text, "espera, no toques la base de datos")
        self.assertTrue(call.barge_in.is_set())
        self.assertEqual(len(call.out.queue), 0)
        self.assertFalse(call.out.paused)
        deadline = time.monotonic() + 20  # the phrase being synthesized finishes first
        while not call.pending_interruption and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        self.assertTrue(call.pending_interruption)
        self.assertTrue(call.pending_interruption["not_heard"])
        await self.phone.hang_up()

    async def test_the_agent_hearing_itself_is_not_an_interruption(self):
        call = await self.long_agent_speech()
        call._speech_started()
        call._speech_stopped(b"\x00\x00" * 8000)
        self.assertEqual(call._resolve_hold(call.hold_seq, "reviso los registros de la base de datos"), "")
        self.assertFalse(call.barge_in.is_set())
        self.assertFalse(call.out.paused)
        await self.phone.hang_up()

    async def test_streamed_speech_starts_sooner_and_plays_in_full(self):
        flag = Path(TMP) / "stream-tts"
        long_text = ("Primero voy a correr todas las pruebas y a revisar los registros. Después actualizo la "
                     "documentación y te mando un resumen con todo lo que encontré en el servidor.")
        await self.start_agent_call()

        async def first_sound(text):
            said_at = time.monotonic()
            await self.post("/say", {"text": text})
            first = await self.phone.wait_state("speaking", said_at)
            await self.phone.wait_state("listening", first)
            return first - said_at

        flag.unlink(missing_ok=True)
        whole = await first_sound(long_text)
        flag.write_text("1")
        try:
            heard_before = len(self.phone.loud)
            streamed = await first_sound(long_text)
            self.assertGreater(len(self.phone.loud), heard_before)  # it was actually heard
        finally:
            flag.unlink(missing_ok=True)
        self.assertLess(streamed, whole - 1.0, (streamed, whole))
        await self.phone.hang_up()

    async def test_greeting_is_recorded_then_played_before_engine_is_ready(self):
        saved = server.greeting_path("¿Sí, Ana?")  # the test contact's greeting
        saved.unlink(missing_ok=True)
        # First call: the engine says the greeting and the server keeps it.
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.phone.answer(dialed["call_id"])
        spoke = await self.phone.wait_state("speaking")
        await self.phone.wait_state("listening", spoke)
        self.assertTrue(saved.exists())
        await self.phone.hang_up()
        await asyncio.sleep(1.5)
        await self.server.engine.stop()
        self.phone.states.clear()
        self.phone.mic = FakeMic()  # the first call's microphone track has ended
        # Second call, engine never ready: the saved greeting still plays, no models needed.
        engine = self.server.engine
        engine.start = lambda: setattr(engine, "ready", asyncio.get_running_loop().create_future())
        started = time.monotonic()
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.phone.answer(dialed["call_id"])
        await self.phone.wait_state("speaking", started)
        await self.phone.wait_state("listening", started)
        self.assertFalse(engine.ready.done())
        await self.phone.hang_up()

    async def test_inbox_contact_with_unreachable_pane_is_unknown_not_offline(self):
        self.server.config["contacts"][0].update({"agent": "nadie", "inbox": "mac"})
        async with self.http.get("http://127.0.0.1:18771/contacts?token=secret") as response:
            listed = (await response.json())["contacts"]
        self.assertEqual(listed[0]["status"], "unknown")
        status, dialed = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual(status, 200, dialed)
        await self.post("/end", {"text": ""})

    async def test_offline_agent_cannot_be_dialed(self):
        self.server.config["contacts"][0]["agent"] = "nadie"
        status, body = await self.phone_post("/dial", {"contact": "coordinador"})
        self.assertEqual((status, body["error"]), (503, "offline"))
