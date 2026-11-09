"""Call server: lets Claude Code phone the user's Android and talk in real time.

Two listeners:
  * local API (127.0.0.1 only) used by the `llamada` CLI: /start, /say, /end, /status
  * phone API (Tailscale) used by the app: /register, /decline, /ws (signaling + control)

Audio flows both ways continuously over WebRTC (Opus, with the phone's echo cancellation).
The user can talk at any time: while Claude speaks it cuts Claude off (barge-in, reporting
what was actually heard); while Claude thinks it is kept and handed over on the next turn.
One call at a time; the voice engine (TTS voice + Whisper) lives only while a call is active.
"""
import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
import fractions
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import secrets
import subprocess
import sys
import time

from aiohttp import WSMsgType, web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
import av
import numpy as np
import soundfile

import herdr_agent
import jev
import vad

CONFIG_DIR = Path(os.environ.get("LLAMADA_CONFIG", Path.home() / ".config" / "llamada"))
# How the agent prompts name the person on the phone: "[llamada] <NOMBRE> te llamó por teléfono …".
NOMBRE = os.environ.get("LLAMADA_NOMBRE", "").strip() or "Tu usuario"
VOICE_TTS = Path(__file__).resolve().parents[2]
# The voice engine (speech-to-text + text-to-speech) is a separate process speaking the JSON-lines protocol
# described in engine.py. Point these two at another script to use a different model (see README).
ENGINE_PYTHON = Path(os.environ.get("LLAMADA_ENGINE_PYTHON") or VOICE_TTS / ".venv" / "bin" / "python")
ENGINE = Path(os.environ.get("LLAMADA_ENGINE") or Path(__file__).resolve().parent / "engine.py")
RUNTIME = CONFIG_DIR / "runtime"
WAITING = CONFIG_DIR / "waiting"  # old layout of the waiting phrases: primero-*.wav first, the rest after (espera/ replaces it)
FILLERS = CONFIG_DIR / "fillers"  # short recorded acknowledgements in the same voice; fillers/<category>/ fit a kind of phrase (jev-on)
GREETINGS = CONFIG_DIR / "greetings"  # a contact's greeting, recorded once, played before the models load

RING_SECONDS = 45          # how long the phone rings before "no answer"
CONNECT_SECONDS = 20       # answered -> audio flowing
ANSWER_WAIT_SECONDS = 60   # how long to wait for the user to start speaking
CONTINUE_SECONDS = 1.0     # a pause shorter than this after an utterance joins the next one
IDLE_HANGUP_SECONDS = 600  # Claude went quiet mid-call: hang up eventually
WARM_SECONDS = float(os.environ.get("LLAMADA_WARM_SECONDS", "600"))  # models stay loaded after a call / app open
AGENT_JOIN_SECONDS = 1.0   # agent mode: silence after the last phrase before it all goes to the agent as one message
JOIN_LONG_SECONDS = 2.0    # ... when his last phrase trails off ("y", "que", a comma): he is surely going on
JOIN_QUESTION_SECONDS = 0.7  # ... a little sooner after a clear question
FILLER_DELAY = 0.3         # agent mode: a recorded "mmm, ya" this long after he stops, if the agent is silent
MM_FOLDER = "mm"           # fillers/mm/: the neutral clips (<=0.5 s) played first when Jev is on
FILLER_MIN_UTTERANCE = 0.8 # seconds of speech below which (a cough, a click) no filler is played
HOLD_MAX_SECONDS = 20.0    # voice paused over a detected interruption: resume if no verdict arrives in this time
ESPERA_FOLDER = "espera"   # fillers/espera/<category>/: the phrase that fits what he asked, "seguir/": the short ones after, "senal/": the soft signal
WAIT_FIRST_SECONDS = 4.0   # agent mode: silence after his message before the first waiting phrase ("Déjame revisarlo.")
WAIT_REPEAT_SECONDS = 11.0 # ... and between the shorter phrases that follow while the agent still works
WAIT_MAX_PHRASES = 4       # voice phrases in all (the first one included); past them, only the soft signal
WAIT_CHIME_SECONDS = 15.0  # ... one soft sound this often, so he knows the line is alive without more talk
WAIT_POLL_SECONDS = 0.5    # how often the wait loop looks whether a cue is due and the line is free
WAIT_MAX_SECONDS = 180.0   # stop reassuring after this long: something is wrong, not slow
NOD_FOLDER = "asentir"     # fillers/asentir/: the soft «ajá» / «mm» / «ya» / «sí» played while he talks on and on
NOD_AFTER_SECONDS = 15.0   # he has talked this long without ending his turn before the first nod
NOD_GAP_SECONDS = (10.0, 15.0)  # a nod this long (random in the range) after the previous one, not before
NOD_PAUSE_MS = 300         # a natural pause (quiet this long, the turn still open) is where a nod goes
NOD_MAX_SECONDS = 0.6      # longer clips are not nods
NOD_GAIN = 0.6             # softer than the voice itself
NOD_ECHO_TAIL_MS = 400     # after a nod ends its echo may still reach the microphone this long (phone delay + room)
NOD_RECENT_SECONDS = 1.5   # a nod this recently before he stops already acknowledged him: no "mm" on top
FIRST_CHUNK_CHARS = 80     # the first piece is short: its synthesis is what delays the first sound
CHUNK_CHARS = 220          # the voice stays natural on short pieces
OUT_RATE = 48000
OUT_FRAME = 960            # 20 ms
SAMPLES_PER_CHAR = 2600    # 48 kHz audio per character of Spanish speech: estimates a phrase still being generated
PLAYOUT_DELAY = 0.15       # jitter buffer on the phone: audio "sent" is heard this much later
RECORD_UPLINK = os.environ.get("LLAMADA_RECORD") == "1"

log = logging.getLogger("llamada")


def load_config() -> dict:
    config = json.loads((CONFIG_DIR / "config.json").read_text(encoding="utf-8"))
    config.setdefault("local_port", 8770)
    config.setdefault("phone_port", 8771)
    config.setdefault("phone_host", "0.0.0.0")
    return config


INTRO_TEXTO = (f"[llamada] {NOMBRE} te escribe desde Hermes (por texto, no hay llamada de voz). Lo que escriba te llegará así. "
               "Contéstale con: llamada decir \"…\" (claro y breve; llega a Hermes como texto). "
               "Si respondes en varias partes, manda todas menos la última con llamada decir --sigue \"…\": "
               "Hermes las recibe juntas. "
               "Para cerrar la conversación: llamada colgar \"…\".")


class TextChannel:
    """Conversation in text with the firstmate (from Hermes): with no call going, what it says through
    `llamada decir` is kept here until someone collects it, however long it takes."""

    ACTIVE_SECONDS = 1800

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.changed = asyncio.Condition()
        self.active_until = 0.0
        self.introduced = False
        self.waiting = 0
        self.complete = True

    def active(self) -> bool:
        return time.monotonic() < self.active_until

    def touch(self) -> None:
        self.active_until = time.monotonic() + self.ACTIVE_SECONDS

    async def push(self, text: str, more: bool = False) -> None:
        """A reply; `more` (said with --sigue) means the rest of the answer is still coming."""
        async with self.changed:
            self.replies.append(text)
            self.complete = not more
            self.changed.notify_all()
        if self.waiting == 0 and not more:
            self.avisar_tarde(text)

    async def take(self, timeout: float, pieces: bool = False) -> tuple[list[str], bool]:
        """The replies once the answer is complete (its last piece arrived), or whatever came by the timeout; with
        `pieces` (the bar reads them aloud as they come) as soon as there is any. Also whether the last piece is in."""
        deadline = time.monotonic() + timeout
        async with self.changed:
            self.waiting += 1
            try:
                while not (self.replies and (self.complete or pieces)) and time.monotonic() < deadline:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self.changed.wait(), deadline - time.monotonic())
                taken, self.replies = self.replies, []
                return taken, self.complete
            finally:
                self.waiting -= 1

    def avisar_tarde(self, text: str) -> None:
        """A reply nobody was waiting for: tell him on the Mac."""
        resumen = text.replace('"', "'").replace("\\", " ")[:140]
        with contextlib.suppress(Exception):
            subprocess.Popen(["/usr/bin/osascript", "-e",
                              f'display notification "{resumen}" with title "Firstmate respondió" sound name "Glass"'])


def agent_message(text: str, cut: dict | None, introduced: bool, intro_text: str | None = None) -> str:
    """What the agent reads. The user's words (USUARIO) and the agent's own (YO) are always labelled apart."""
    intro = "" if introduced else (intro_text or INTRO) + " "
    message = f"{intro or '[llamada] '}USUARIO dijo: {text}"
    if cut:
        message += (f" [USUARIO te interrumpió mientras TÚ hablabas. "
                    f"YO dije (él alcanzó a oír): «{cut['heard'] or '(nada)'}» · "
                    f"YO no alcancé a decir: «{cut['not_heard'] or '(nada)'}»]")
    return message


def stream_enabled() -> bool:
    """Synthesis by chunks (first sound in ~0.7 s instead of waiting for the whole phrase)."""
    return (CONFIG_DIR / "stream-tts").exists()


def greeting_path(text: str) -> Path:
    return GREETINGS / (hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] + ".wav")


def split_for_speech(text: str) -> list[str]:
    """Sentences grouped into pieces of at most CHUNK_CHARS; the first piece is shorter
    (FIRST_CHUNK_CHARS) so speech starts sooner. Long sentences split at commas."""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.findall(r"[^.!?…]+[.!?…]*", text)
    pieces: list[str] = []

    def limit() -> int:
        return FIRST_CHUNK_CHARS if not pieces else CHUNK_CHARS

    for sentence in (s.strip() for s in sentences):
        while len(sentence) > limit():
            cap = limit()
            cut = sentence.rfind(",", 0, cap)
            cut = cut if cut > min(40, cap // 3) else sentence.rfind(" ", 0, cap)
            cut = cut if cut > 0 else cap - 1  # no break point at all: cut hard
            pieces.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if not sentence:
            continue
        previous_limit = FIRST_CHUNK_CHARS if len(pieces) == 1 else CHUNK_CHARS
        if pieces and len(pieces[-1]) + 1 + len(sentence) <= previous_limit:
            pieces[-1] += " " + sentence
        else:
            pieces.append(sentence)
    return [p for p in pieces if re.search(r"\w", p)]


def load_fillers(folder: Path = FILLERS) -> list[tuple[str, np.ndarray]]:
    """The recorded acknowledgements ("mmm, ya", "dime", ...) as (name, 48 kHz int16 pcm)."""
    loaded = []
    for path in sorted(folder.glob("*.wav")) if folder.is_dir() else []:
        try:
            pcm, rate = soundfile.read(path, dtype="int16")
        except Exception as exc:
            log.warning("bad filler %s: %s", path.name, exc)
            continue
        if rate == OUT_RATE:
            loaded.append((path.stem, pcm.reshape(-1)))
    return loaded


def load_filler_sets(folder: Path = FILLERS) -> dict[str, list[tuple[str, np.ndarray]]]:
    """The acknowledgements that fit a kind of phrase (fillers/pregunta/, fillers/despedida/, ...), by category;
    each named "category/name"."""
    sets = {}
    for sub in sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []:
        clips = [(f"{sub.name}/{name}", pcm) for name, pcm in load_fillers(sub)]
        if clips:
            sets[sub.name] = clips
    return sets


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


# Words a Spanish sentence does not end on: he has more to say.
TRAILING = {"y", "o", "e", "u", "ni", "pero", "sino", "que", "de", "del", "el", "la", "los", "las", "un", "una",
            "en", "con", "para", "por", "porque", "si", "como", "entonces", "eh", "mmm", "este", "a", "al",
            "aunque", "cuando", "donde", "mi", "tu", "su", "lo", "me", "te", "se", "sea", "es", "son", "está", "estoy"}


def join_seconds(text: str) -> float:
    """How long to wait for him to go on after this phrase, judging by how it ends."""
    stripped = text.strip()
    words = _words(stripped)
    if not words:
        return AGENT_JOIN_SECONDS
    if stripped.endswith((",", "…", "...")) or words[-1] in TRAILING:
        return JOIN_LONG_SECONDS
    if stripped.endswith("?") and len(words) >= 4:
        return JOIN_QUESTION_SECONDS
    return AGENT_JOIN_SECONDS


def is_echo(heard: str, spoken: str) -> bool:
    """True when what the microphone transcribed is a stretch of what the agent itself is saying
    (its voice coming back through the phone). Needs 3+ words: a lone \"sí\" is him."""
    words, said = _words(heard), _words(spoken)
    if len(words) < 3:
        return False
    return any(said[i:i + len(words)] == words for i in range(len(said) - len(words) + 1))


class WaitPlan:
    """When the «still working» cues are due, by seconds since his message: the first phrase at WAIT_FIRST_SECONDS,
    up to WAIT_MAX_PHRASES in all WAIT_REPEAT_SECONDS apart, then a soft signal every WAIT_CHIME_SECONDS.
    The loop reports only the cues it really played, so one held back (he talks, the agent speaks) is due again at once."""

    def __init__(self) -> None:
        self.voices = 0
        self.last_cue: float | None = None

    def over(self, elapsed: float) -> bool:
        return elapsed >= WAIT_MAX_SECONDS

    def due(self, elapsed: float) -> str | None:
        if self.over(elapsed):
            return None
        if self.voices == 0:
            return "voice" if elapsed >= WAIT_FIRST_SECONDS else None
        since = elapsed - (self.last_cue or 0.0)
        if self.voices < WAIT_MAX_PHRASES:
            return "voice" if since >= WAIT_REPEAT_SECONDS else None
        return "chime" if since >= WAIT_CHIME_SECONDS else None

    def played(self, kind: str, elapsed: float) -> None:
        self.voices += kind == "voice"
        self.last_cue = elapsed


def legacy_wait_sets(clips: list) -> dict[str, list]:
    """The old waiting/ folder as wait sets: primero-* as the first phrase, the rest as the ones after."""
    return {"otro": [c for c in clips if c[0].startswith("primero")],
            "seguir": [c for c in clips if not c[0].startswith("primero")]}


def pick_wait_clip(sets: dict, count: int, category: str | None, last: str | None):
    """The phrase for the count-th wait: the first fits his kind of message (the generic "otro" without a Jev
    decision or recordings for it), the ones after are the short generic "seguir" ones; never the last again."""
    if count == 0:
        pool = sets.get(category) or sets.get("otro") or []
    else:
        pool = sets.get("seguir") or sets.get("otro") or []
    options = [c for c in pool if c[0] != last] or pool
    return random.choice(options) if options else None


def pick_wait_phrase(clips: list, count: int, last: str | None):
    """The reassurance for the count-th wait: a "primero-*" one first, then varied ones, never the last again."""
    firsts = [c for c in clips if c[0].startswith("primero")]
    later = [c for c in clips if not c[0].startswith("primero")]
    pool = firsts if count == 0 else (later or firsts)
    options = [c for c in pool if c[0] != last] or pool
    return random.choice(options) if options else None


def load_nod_clips(folder: Path | None = None) -> list[tuple[str, np.ndarray]]:
    """The recorded nods (<= NOD_MAX_SECONDS), already softer by NOD_GAIN."""
    clips = []
    for name, pcm in load_fillers(FILLERS / NOD_FOLDER if folder is None else folder):
        if pcm.size > NOD_MAX_SECONDS * OUT_RATE:
            log.warning("nod %s is longer than %.1f s: ignored", name, NOD_MAX_SECONDS)
            continue
        clips.append((name, (pcm.astype(np.float32) * NOD_GAIN).astype(np.int16)))
    return clips


class NodPlan:
    """When a nod is due while he talks on: once he has talked NOD_AFTER_SECONDS, then no sooner than 10-15 s
    after the previous one. The caller reports only the nods it really played, so one held back is due again."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.next_at = 0.0

    def due(self, spoken: float, now: float) -> bool:
        return spoken >= NOD_AFTER_SECONDS and now >= self.next_at

    def nodded(self, now: float) -> None:
        self.next_at = now + self.rng.uniform(*NOD_GAP_SECONDS)


def pick_filler(fillers: list, last: str | None):
    """A random filler, not the one used last (when there is a choice)."""
    options = [f for f in fillers if f[0] != last] or fillers
    return random.choice(options) if options else None


def join_cuts(first: dict | None, second: dict) -> dict:
    """Two reports of one interruption (speeches said one after the other) as one, in the order they were said."""
    if not first:
        return second
    return {key: " ".join(part for part in (first[key], second[key]) if part) for key in ("heard", "not_heard")}


def heard_prefix(text: str, fraction: float) -> str:
    """The words of `text` the user heard if playback stopped at `fraction` of its audio."""
    if fraction <= 0:
        return ""
    words = text.split()
    return " ".join(words[: int(len(words) * min(fraction, 1.0))])


def thinking_tone() -> np.ndarray:
    """A soft two-note chime every 2.5 s: lets the user know the line is alive."""
    t = np.arange(int(0.12 * OUT_RATE)) / OUT_RATE
    envelope = np.sin(np.pi * t / t[-1])
    note = lambda hz: 0.035 * envelope * np.sin(2 * np.pi * hz * t)  # noqa: E731
    gap = np.zeros(int(0.05 * OUT_RATE))
    chime = np.concatenate([note(660), gap, note(880)])
    period = np.zeros(int(2.5 * OUT_RATE))
    period[: chime.size] = chime
    return (period * 32767).astype(np.int16)


class CallEnded(Exception):
    def __init__(self, status: str, detail: str = ""):
        super().__init__(status)
        self.status = status
        self.detail = detail


@dataclass
class Segment:
    text: str
    pcm: np.ndarray
    position: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)
    complete: bool = True   # False while the phrase is still being generated chunk by chunk
    on_start: Callable[[], None] | None = None  # called once, as its first audio goes out (the line on screen)

    def extend(self, pcm: np.ndarray) -> None:
        self.pcm = np.concatenate([self.pcm, pcm])

    def finish(self) -> None:
        self.complete = True


class OutboundAudio(MediaStreamTrack):
    """Claude's side of the line: queued speech, the thinking chime, or silence; real-time paced."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self.queue: deque[Segment] = deque()
        self.thinking = False
        self.paused = False   # an interruption is being judged: silence now, nothing thrown away
        self.tone = thinking_tone()
        self.tone_position = 0
        self.start: float | None = None
        self.pts = 0

    @property
    def playing(self) -> bool:
        return bool(self.queue)

    def enqueue(self, segment: Segment) -> None:
        self.queue.append(segment)

    def only_queued(self, segment: Segment | None) -> bool:
        return segment is not None and len(self.queue) == 1 and self.queue[0] is segment

    def drop(self, segment: Segment | None) -> None:
        """Remove one queued segment (identity, not equality: the pcm is an array)."""
        kept = [q for q in self.queue if q is not segment]
        if len(kept) != len(self.queue):
            self.queue = deque(kept)
            segment.done.set()

    def flush(self) -> list[tuple[Segment, float]]:
        """Stop talking now. Returns each cut segment with the fraction the user heard."""
        cut = []
        # Audio already sent is still in the phone's jitter buffer and will be heard.
        late = int(PLAYOUT_DELAY * OUT_RATE)
        for segment in self.queue:
            heard = max(0, min(segment.position + late, segment.pcm.size)) if segment.position else 0
            total = segment.pcm.size if segment.complete else max(
                segment.pcm.size, int(len(segment.text) * SAMPLES_PER_CHAR))
            cut.append((segment, heard / total))
            segment.done.set()
        self.queue.clear()
        return cut

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        if self.start is None:
            self.start = time.monotonic()
        wait = self.start + self.pts / OUT_RATE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        frame = np.zeros(OUT_FRAME, dtype=np.int16)
        filled = 0
        while filled < OUT_FRAME and self.queue and not self.paused:
            segment = self.queue[0]
            if segment.on_start:
                started, segment.on_start = segment.on_start, None
                started()
            take = min(OUT_FRAME - filled, segment.pcm.size - segment.position)
            frame[filled:filled + take] = segment.pcm[segment.position:segment.position + take]
            segment.position += take
            filled += take
            if segment.position >= segment.pcm.size:
                if not segment.complete:
                    break  # caught up with the generator: silence until the next chunk
                self.queue.popleft()
                segment.done.set()
        if filled:
            self.tone_position = 0
        elif self.thinking:
            end = self.tone_position + OUT_FRAME
            frame[:] = np.resize(self.tone, end)[self.tone_position:end]
            self.tone_position = end % self.tone.size
        audio = av.AudioFrame.from_ndarray(frame.reshape(1, -1), format="s16", layout="mono")
        audio.sample_rate = OUT_RATE
        audio.pts = self.pts
        audio.time_base = fractions.Fraction(1, OUT_RATE)
        self.pts += OUT_FRAME
        return audio


class VoiceEngine:
    """The engine subprocess; requests are serialized."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.ready: asyncio.Task | None = None
        self.release: asyncio.TimerHandle | None = None
        self.next_id = 0

    def start(self) -> None:
        if self.release:
            self.release.cancel()
            self.release = None
        if self.ready is None:
            self.ready = asyncio.ensure_future(self._spawn())

    def release_after(self, seconds: float) -> None:
        """Keep the models loaded for a while: a call right after this one starts instantly."""
        if self.release:
            self.release.cancel()
        self.release = asyncio.get_running_loop().call_later(
            seconds, lambda: asyncio.ensure_future(self.stop()))

    def warm(self, seconds: float) -> None:
        """Load the models ahead of a call (the app opened) and free them if no call comes."""
        self.start()
        self.release_after(seconds)

    async def _spawn(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            str(ENGINE_PYTHON), str(ENGINE), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=sys.stderr, limit=1 << 20,
        )
        reply = json.loads(await self.process.stdout.readline() or b'{"ok":false,"error":"engine exited"}')
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "engine failed"))
        log.info("voice engine ready")

    async def request(self, payload: dict) -> dict:
        self.start()
        await self.ready
        async with self.lock:
            self.next_id += 1
            payload = {**payload, "id": self.next_id}
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("voice engine exited")
                reply = json.loads(line)
                if reply.get("id") == payload["id"]:
                    break
                # A request abandoned when its call ended (the engine stays warm): its late reply is not ours.
                log.info("discarding a late engine reply (id %s)", reply.get("id"))
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "engine error"))
        return reply

    async def request_stream(self, payload: dict):
        """Like request, but the engine answers with audio chunks as it generates them; yields each
        chunk's file path. Abandoning it early is safe: the engine's late lines are dropped by id."""
        self.start()
        await self.ready
        async with self.lock:
            self.next_id += 1
            payload = {**payload, "id": self.next_id}
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("voice engine exited")
                reply = json.loads(line)
                if reply.get("id") != payload["id"]:
                    log.info("discarding a late engine reply (id %s)", reply.get("id"))
                    continue
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error", "engine error"))
                if reply.get("final"):
                    return
                yield reply["chunk"]

    async def stop(self) -> None:
        if self.release:
            self.release.cancel()
            self.release = None
        if self.ready and not self.ready.done():
            self.ready.cancel()
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except asyncio.TimeoutError:
                self.process.kill()
        self.process = None
        self.ready = None


INTRO = (f"[llamada] {NOMBRE} te llamó por teléfono y está en la línea. Lo que diga te llegará así. "
         "Contéstale con: llamada decir \"…\" (breve y hablado, sin código). "
         "Si vas a tardar, di primero lo que ya tienes claro con un llamada decir corto y luego el resto con otro: "
         "suenan seguidos, sin hueco. "
         "Para despedirte y colgar: llamada colgar \"…\".")


class Call:
    def __init__(self, call_id: str, engine: VoiceEngine, agent: str | None = None,
                 inbound: bool = False, jev: "jev.Jev | None" = None) -> None:
        self.id = call_id
        self.engine = engine
        # Agent mode: the user's words go to this Herdr agent as prompts and the agent
        # answers with `llamada decir` (non-blocking). Without it, the CLI drives turns.
        self.agent = agent
        self.inbound = inbound
        self.introduced = not inbound
        self.speech_chain: asyncio.Future | None = None
        self.speech_synthesized: asyncio.Event | None = None  # the last queued speech has all its audio queued
        self.generation = 0                # bumps on barge-in: drops queued speech
        self.pending_interruption: dict | None = None
        self.agent_status = "idle"
        self.ws: web.WebSocketResponse | None = None
        self.pc: RTCPeerConnection | None = None
        self.out = OutboundAudio()
        self.detector = vad.TurnDetector(pause_ms=NOD_PAUSE_MS)
        self.answered = asyncio.Event()     # phone picked up (control channel open)
        self.audio_up = asyncio.Event()     # first audio from the phone arrived
        self.declined = False
        self.ended: CallEnded | None = None
        self.ended_event = asyncio.Event()
        self.user_speaking = False
        self.barge_in = asyncio.Event()
        self.fillers = load_fillers(FILLERS) if agent else []
        self.filler_sets = load_filler_sets(FILLERS) if agent else {}
        self.filler_sets.pop(ESPERA_FOLDER, None)
        self.mm_clips = self.filler_sets.pop(MM_FOLDER, [])  # jev-on: the neutral "mm" that comes first, at FILLER_DELAY
        self.nod_clips = load_nod_clips()   # played only while ~/.config/llamada/asentir-on exists (see _nod_allowed)
        self.nod_plan = NodPlan()
        self.nod: Segment | None = None
        self.nod_name: str | None = None
        self.nod_at = -1e9
        self.nods_played = 0
        self.speaker: bool | None = None    # the phone's output (the app says): None = unknown, taken for the speaker
        self.clock = time.monotonic
        self.jev = jev                      # asked only while ~/.config/llamada/jev-on exists
        self.wait_sets = self._load_wait_sets() if agent else {}
        self.wait_chime = self.wait_sets.pop("senal", [])  # the soft sound: not a phrase
        self.wait_category: str | None = None  # Jev's reading of his last message, for the first waiting phrase
        self.waits_played = 0
        self.chimes_played = 0
        self.hold = False                  # voice paused while an interruption is judged
        self.hold_seq = 0
        self.utterance_seq = 0
        self.wait_task: asyncio.Future | None = None
        self.filler: Segment | None = None     # the recorded acknowledgement currently queued
        self.filler_name: str | None = None
        self.filler_prev: Segment | None = None  # the "mm" while the fitted filler is queued behind it
        self.mm_task: asyncio.Future | None = None
        self.fitted_this_turn = False
        self.fillers_played = 0
        self.filler_this_turn = False
        self.turn_end_at = 0.0                 # latency log: he stopped talking
        self.turn_heard_at = 0.0
        self.turn_sent_at = 0.0
        self.agent_pending: list[str] = []   # phrases heard, not yet sent to the agent
        self.agent_heard_at = 0.0
        self.agent_flush: asyncio.Future | None = None
        self.interruption: list[tuple[Segment, float]] = []
        self.speech_change = asyncio.Event()
        self.utterances: list[str] = []     # transcribed, not yet handed to Claude
        self.transcribing = 0
        self.stt_chain: asyncio.Future | None = None
        self.last_activity = time.monotonic()
        self.transcript: list[tuple[str, str]] = []
        self.counter = 0

    def check(self) -> None:
        if self.ended:
            raise self.ended

    def phone_connected(self) -> bool:
        """The phone is on the call now: its socket is open and the call has not ended. `ws` stays set after the
        call (it also marks the call as answered), so it alone does not say the phone is still there."""
        return bool(self.ws is not None and not self.ws.closed and not self.ended)

    def end(self, status: str, detail: str = "") -> None:
        if not self.ended:
            self.ended = CallEnded(status, detail)
            self.ended_event.set()
            self.answered.set()
            self.audio_up.set()
            self.speech_change.set()
            self.out.flush()

    async def send(self, message: dict) -> None:
        if self.ended or not self.ws or self.ws.closed:
            return
        try:
            await self.ws.send_str(json.dumps(message, ensure_ascii=False))
        except (ConnectionError, RuntimeError) as exc:
            self.end("colgó", f"connection lost: {exc}")

    def show_line(self, who: str, text: str) -> Callable[[], None]:
        """The call in text on the phone's screen: a line goes over the call's channel, nowhere else (no disk,
        no log). `who` is "agent" or "user"; the returned callable sends it (for Segment.on_start)."""
        return lambda: asyncio.ensure_future(self.send({"type": "line", "who": who, "text": text}))

    async def state(self, value: str) -> None:
        self.out.thinking = value == "thinking"
        await self.send({"type": "state", "value": value})

    async def until(self, awaitable, timeout: float | None = None):
        """Await something, but stop if the call ends; returns None on timeout."""
        task = asyncio.ensure_future(awaitable)
        ended = asyncio.ensure_future(self.ended_event.wait())
        done, _ = await asyncio.wait({task, ended}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        ended.cancel()
        if task not in done:
            task.cancel()
            self.check()
            return None
        return task.result()

    # ----- audio from the phone ----------------------------------------------------------
    async def consume(self, track: MediaStreamTrack) -> None:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=vad.RATE)
        try:
            while not self.ended:
                frame = await track.recv()
                self.audio_up.set()
                for chunk in resampler.resample(frame):
                    pcm = chunk.to_ndarray().astype("<i2").tobytes()
                    if RECORD_UPLINK:
                        self._record(pcm)
                    self._feed(pcm)
        except MediaStreamError:
            self.end("colgó", "audio stopped")

    def _feed(self, pcm: bytes) -> None:
        self.detector.barge_mode = (self.out.playing and not self.out.paused
                                    and not self._only_fillers_queued())
        for kind, data in self.detector.feed(pcm):
            if kind == "start":
                self._speech_started()
            elif kind == "pause":
                self._speech_paused()
            elif kind == "resume":
                self._speech_resumed()
            else:
                self._speech_stopped(data)

    def phone_route(self, data: dict) -> None:
        """The app says where the call's sound goes (speaker, or earpiece/headphones): the nods depend on it."""
        self.speaker = bool(data.get("speaker"))
        log.info("phone output: %s", "speaker" if self.speaker else "earpiece/headphones")

    def _nod_allowed(self) -> bool:
        """Nods are on with asentir-on, and by the loudspeaker only with asentir-altavoz too: the phone's echo
        cancellation is not guaranteed to hide the nod there, so it is opt-in (earpiece and headphones need nothing)."""
        if not (CONFIG_DIR / "asentir-on").exists():
            return False
        return self.speaker is False or (CONFIG_DIR / "asentir-altavoz").exists()

    def _speech_paused(self) -> None:
        """He is quiet for a moment in the middle of a long talk: a soft nod, if one is due and the line is free."""
        if not (self.nod_clips and self.user_speaking and self._nod_allowed()):
            return
        now = self.clock()
        if not self.nod_plan.due(self.detector.spoken_ms / 1000, now):
            return
        if (self.out.playing or self.hold or self.ended
                or (self.speech_chain and not self.speech_chain.done())):
            return  # never over the agent's voice, nor while an interruption is judged
        choice = pick_filler(self.nod_clips, self.nod_name)
        self.nod_name, pcm = choice
        self.nod = Segment(self.nod_name, pcm)
        self.out.enqueue(self.nod)
        self.detector.mask_echo(int(pcm.size * 1000 / OUT_RATE) + int(PLAYOUT_DELAY * 1000) + NOD_ECHO_TAIL_MS)
        self.nod_plan.nodded(now)
        self.nod_at = now
        self.nods_played += 1
        log.info("nod %r after %.0fs of his talk", self.nod_name, self.detector.spoken_ms / 1000)

    def _speech_resumed(self) -> None:
        """His voice is back: a nod still playing stops (it never goes over him)."""
        if self.nod is not None:
            self.out.drop(self.nod)
            self.nod = None

    def _log_latency(self) -> None:
        """One line per turn: where the time went between he stopped talking and the first voice."""
        if not self.turn_end_at:
            return
        now = time.monotonic()
        marks = [("stt", self.turn_heard_at), ("join", self.turn_sent_at)]
        parts, last = [], self.turn_end_at
        for name, mark in marks:
            if mark and mark >= last:
                parts.append(f"{name} {mark - last:.1f}s")
                last = mark
        parts.append(f"agent+voice {now - last:.1f}s")
        log.info("latency: stopped talking -> first voice %.1fs (%s)", now - self.turn_end_at, ", ".join(parts))
        self.turn_end_at = self.turn_heard_at = self.turn_sent_at = 0.0

    def _record(self, pcm: bytes) -> None:
        """Diagnostics (LLAMADA_RECORD=1): the phone's audio, plus when Claude was speaking."""
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with (RUNTIME / f"{self.id}-uplink.pcm").open("ab") as f:
            f.write(pcm)
        # One byte per 10 ms of uplink: 1 while Claude's audio is going out.
        with (RUNTIME / f"{self.id}-speaking.bin").open("ab") as f:
            f.write(bytes([1 if self.out.playing else 0]) * (len(pcm) // 320))

    def _only_fillers_queued(self) -> bool:
        """Nothing but fillers (or waiting phrases) is queued: no real speech to interrupt."""
        mine = [s for s in (self.filler, self.filler_prev, self.nod) if s is not None]
        return bool(self.out.queue) and all(any(q is s for s in mine) for q in self.out.queue)

    def _drop_fillers(self) -> None:
        self.out.drop(self.filler)
        self.out.drop(self.filler_prev)
        self.filler_prev = None

    def _jev_on(self) -> bool:
        return self.jev is not None and jev.enabled(CONFIG_DIR)

    def _speech_started(self) -> None:
        self.user_speaking = True
        if self._jev_on():
            self.jev.warm()  # by the time his words are transcribed the connection is open
        self._stop_waiting()  # he is adding something: the next message restarts the wait
        self.wait_category = None
        self.last_activity = time.monotonic()
        if self._only_fillers_queued():
            self._drop_fillers()  # he kept talking over "mmm, ya": not an interruption
        elif self.hold:
            pass  # already judging this one
        elif self.out.playing:
            # Voice over the agent: stop at once but keep the rest; the words he says decide
            # whether it was a real interruption (cut) or a false one (noise, echo: resume).
            self.hold, self.hold_seq = True, self.utterance_seq + 1
            self.out.paused = True
            asyncio.get_running_loop().call_later(HOLD_MAX_SECONDS, self._hold_timeout, self.hold_seq)
            log.info("barge-in held (%s)", self.detector.last_start)
        elif self.speech_chain and not self.speech_chain.done():
            self.interruption = self.out.flush()
            self.barge_in.set()
            self.generation += 1
            log.info("barge-in (%s)", self.detector.last_start)
        asyncio.ensure_future(self.state("listening"))
        self.speech_change.set()

    def _speech_stopped(self, pcm: bytes) -> None:
        self.user_speaking = False
        self.utterance_seq += 1
        seq = self.utterance_seq
        self.turn_end_at = time.monotonic()
        wants_filler = (self.agent and self.fillers and not self.filler_this_turn
                        and len(pcm) >= FILLER_MIN_UTTERANCE * vad.RATE * 2)
        base = (self.agent and not self.filler_this_turn and len(pcm) >= FILLER_MIN_UTTERANCE * vad.RATE * 2)
        self.mm_task = None
        fitted = False
        nodded = self.clock() - self.nod_at < NOD_RECENT_SECONDS  # the nod was his acknowledgement: no "mm" on top
        if base and self.mm_clips and self._jev_on() and not nodded:
            # With Jev: a neutral "mm" at once, then the filler that fits his words once Jev has decided.
            self.mm_task = asyncio.ensure_future(self._filler_after(FILLER_DELAY, self.mm_clips))
            fitted = bool(self.filler_sets)
        elif wants_filler:
            # Jev on without an "mm": the filler waits for his words (STT + Jev, ~1 s) so it can fit them.
            fitted = bool(self.filler_sets and self._jev_on())
            if not fitted and not nodded:
                asyncio.ensure_future(self._filler_after(FILLER_DELAY))
        self.transcribing += 1
        self.stt_chain = asyncio.ensure_future(self._transcribe(pcm, self.stt_chain, seq, fitted))
        self.speech_change.set()

    def _start_waiting(self) -> None:
        self._stop_waiting()
        if self.wait_sets:
            self.wait_task = asyncio.ensure_future(self._wait_loop())

    def _stop_waiting(self) -> None:
        if self.wait_task:
            self.wait_task.cancel()
            self.wait_task = None

    def _load_wait_sets(self) -> dict[str, list]:
        sets = load_filler_sets(FILLERS / ESPERA_FOLDER)
        if not sets.get("otro"):
            for name, clips in legacy_wait_sets(load_fillers(WAITING)).items():
                sets.setdefault(name, clips)
        return sets

    async def _wait_loop(self) -> None:
        """The agent is working on his message: say so, and keep the line from going empty. A phrase that fits
        his kind of message, a few short ones, then only a soft sound; never over a voice, his or the agent's."""
        start = time.monotonic()
        plan, last = WaitPlan(), None
        while not self.ended and not plan.over(time.monotonic() - start):
            await asyncio.sleep(WAIT_POLL_SECONDS)
            elapsed = time.monotonic() - start
            kind = plan.due(elapsed)
            if not kind:
                continue
            if self.agent_status == "idle" and elapsed > 6:
                return  # it finished without saying anything: nothing is pending
            if (self.user_has_more() or self.user_speaking or self.out.playing
                    or (self.speech_chain and not self.speech_chain.done())):
                continue
            choice = (pick_wait_clip(self.wait_sets, plan.voices, self.wait_category, last) if kind == "voice"
                      else (self.wait_chime or [None])[0])
            if not choice:
                return
            name, pcm = choice
            if kind == "voice":
                last = name
                self.waits_played += 1
            else:
                self.chimes_played += 1
            plan.played(kind, elapsed)
            self.filler = Segment(name, pcm)  # like a filler: not an interruption if he talks over it
            self.out.enqueue(self.filler)
            log.info("waiting %s %r after %.0fs", kind, name, elapsed)

    async def _filler_after(self, delay: float, clips: list | None = None) -> None:
        """A recorded "mmm, ya" so the line is not empty while the agent thinks. Skipped when he is
        talking again, or the agent is already speaking or has speech queued (it answered first)."""
        await asyncio.sleep(delay)
        if (self.ended or self.user_speaking or self.filler_this_turn or self.out.playing
                or (self.speech_chain and not self.speech_chain.done())):
            return
        choice = pick_filler(clips or self.fillers, self.filler_name)
        if not choice:
            return
        self.filler_name, pcm = choice
        self.filler_this_turn = True
        self.fillers_played += 1
        self.filler = Segment(self.filler_name, pcm)
        self.out.enqueue(self.filler)
        log.info("filler %r %.1fs after he stopped", self.filler_name, time.monotonic() - self.turn_end_at)

    async def _filler_for(self, text: str) -> None:
        """Jev on: a filler that fits what he said (a question, an order, a goodbye...). After the "mm" it is
        queued behind it and only if Jev decided and the real answer is not already coming; without an "mm"
        a generic one covers for Jev having no answer in time or no recordings fitting."""
        category = await self.jev.filler_category(text)
        self.wait_category = category
        clips = self.filler_sets.get(category)
        if not self.mm_task:
            await self._filler_after(0, clips)
            return
        await asyncio.wait({self.mm_task})
        if (not clips or self.ended or self.user_speaking or self.fitted_this_turn or not self.filler_this_turn
                or (self.out.playing and not self._only_fillers_queued())
                or (self.speech_chain and not self.speech_chain.done())):
            return
        choice = pick_filler(clips, self.filler_name)
        self.filler_name, pcm = choice
        self.fitted_this_turn = True
        self.fillers_played += 1
        self.filler_prev = self.filler if self.out.playing else None  # still queued: kept to be dropped with it
        self.filler = Segment(self.filler_name, pcm)
        self.out.enqueue(self.filler)  # behind the "mm": never two clips at once
        log.info("fitted filler %r %.1fs after he stopped", self.filler_name, time.monotonic() - self.turn_end_at)

    def _hold_timeout(self, seq: int) -> None:
        if self.hold and self.hold_seq == seq:
            log.info("held interruption got no verdict in %.0fs: resuming", HOLD_MAX_SECONDS)
            self._resolve_hold(seq, "")

    def _spoken(self) -> str:
        return next((t for who, t in reversed(self.transcript) if who == "claude"), "")

    def _resolve_hold(self, seq: int, text: str, assent: bool = False) -> str:
        """The verdict on a held interruption, once his words are known. Returns the text to keep."""
        if not self.hold or seq != self.hold_seq:
            return text
        self.hold = False
        if text and not assent and not is_echo(text, self._spoken()):
            self.interruption = self.out.flush()   # real: cut for good, remember how much he heard
            self.out.paused = False
            self.barge_in.set()
            self.generation += 1
            log.info("interruption confirmed by his words")
            return text
        self.out.paused = False                    # false (noise, echo, silence, an "ajá"): carry on talking
        log.info("%s (%r): resumed", "assent over the agent" if assent else "false interruption", text[:40])
        asyncio.ensure_future(self.state("speaking"))
        return ""

    async def _transcribe(self, pcm: bytes, previous: asyncio.Future | None, seq: int = 0,
                          fitted_filler: bool = False) -> None:
        """Transcriptions finish in the order the user spoke."""
        try:
            if previous:
                await asyncio.wait({previous})
            self.counter += 1
            path = RUNTIME / f"{self.id}-in{self.counter}.pcm"
            RUNTIME.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pcm)
            try:
                text = (await self.engine.request({"op": "stt", "pcm": str(path)}))["text"]
            finally:
                path.unlink(missing_ok=True)
            assent = False
            if (text and self.hold and seq == self.hold_seq and self._jev_on()
                    and not is_echo(text, self._spoken())):
                # Said over the agent: an "ajá" lets it go on; doubt or no answer cuts it, as without Jev.
                assent = await self.jev.interruption(text) == "asentimiento"
            heard, text = text, self._resolve_hold(seq, text, assent)
            if assent and "?" in self._spoken():
                text = heard  # an early "sí" to its question: no cut, but the answer must reach the agent
            if text and fitted_filler:
                asyncio.ensure_future(self._filler_for(text))
            if text:
                self.transcript.append(("usuario", text))
                log.info("heard: %s", text)
                self.show_line("user", text)()
                self.turn_heard_at = time.monotonic()
                if self.agent:
                    self.agent_pending.append(text)
                    self.agent_heard_at = time.monotonic()
                    if not self.agent_flush or self.agent_flush.done():
                        self.agent_flush = asyncio.ensure_future(self._flush_to_agent())
                else:
                    self.utterances.append(text)
        except Exception as exc:  # a failed transcription must not kill the call
            log.warning("transcription failed: %s", exc)
            self._resolve_hold(seq, "")
        finally:
            self.transcribing -= 1
            self.speech_change.set()

    async def _flush_to_agent(self) -> None:
        """He may talk in bursts with short pauses: send them to the agent once, when he really stops."""
        while not self.ended and (self.user_has_more() or time.monotonic() - self.agent_heard_at
                                  < join_seconds(self.agent_pending[-1] if self.agent_pending else "")):
            await asyncio.sleep(0.2)
        text, self.agent_pending = " ".join(self.agent_pending).strip(), []
        if text:
            await self.deliver(text)

    # ----- turns -------------------------------------------------------------------------
    def take_utterances(self) -> str:
        text = " ".join(self.utterances).strip()
        self.utterances.clear()
        return text

    def user_has_more(self) -> bool:
        return self.user_speaking or self.transcribing > 0

    async def collect_answer(self, wait_for_start: float) -> str:
        """Wait for the user to say something and finish; short pauses join utterances."""
        deadline = time.monotonic() + wait_for_start
        while True:
            self.check()
            if not self.user_has_more() and self.utterances:
                # Finished a phrase: give them a moment to continue the thought.
                self.speech_change.clear()
                await self.until(self.speech_change.wait(), CONTINUE_SECONDS)
                if not self.user_has_more():
                    return self.take_utterances()
                continue
            if not self.user_has_more() and time.monotonic() > deadline:
                return ""
            self.speech_change.clear()
            wait = 1.0 if self.user_has_more() else max(0.05, deadline - time.monotonic())
            await self.until(self.speech_change.wait(), wait)

    async def speak(self, text: str, save_as: Path | None = None,
                    synthesized: asyncio.Event | None = None) -> dict | None:
        """Say text; returns what was heard/not heard if the user cut in, else None. With `synthesized` (a chain
        of speeches) it is set once all the audio is queued, and the chain, not this, says "listening" after."""
        pieces = split_for_speech(text)
        self.barge_in.clear()
        self.interruption = []
        if not pieces:
            return None
        self.transcript.append(("claude", text))
        queued: list[Segment] = []
        unsaid: list[str] = []
        try:
            await self._synthesize(pieces, queued, unsaid, save_as, self.show_line("agent", text))
        finally:
            if synthesized:
                synthesized.set()
        if queued and not self.barge_in.is_set():
            await self.until(queued[-1].done.wait())
            # Let the jitter buffer drain so the last word is heard before we listen.
            await self.until(asyncio.sleep(PLAYOUT_DELAY))
        if not self.barge_in.is_set():
            if not synthesized:
                await self.state("listening")
            return None
        cut = {id(segment): fraction for segment, fraction in self.interruption}
        heard, not_heard = [], []
        for segment in queued:
            fraction = cut.get(id(segment), 1.0)
            prefix = heard_prefix(segment.text, fraction)
            if prefix:
                heard.append(prefix)
            rest = " ".join(segment.text.split()[len(prefix.split()):])
            if rest:
                not_heard.append(rest)
        not_heard += unsaid
        return {"heard": " ".join(heard), "not_heard": " ".join(not_heard)}

    async def _synthesize(self, pieces: list[str], queued: list[Segment], unsaid: list[str],
                          save_as: Path | None, on_start: Callable[[], None] | None = None) -> None:
        """Synthesize the pieces and queue their audio in order; stops (leaving the rest in `unsaid`) if he cuts in.
        `on_start` goes with the first piece: called when the speech starts to sound."""
        for index, piece in enumerate(pieces):
            if self.barge_in.is_set() or (self.user_speaking and not self.hold):
                self.barge_in.set()
                unsaid += pieces[index:]
                break
            self.counter += 1
            RUNTIME.mkdir(parents=True, exist_ok=True)
            if stream_enabled():
                segment, aborted = await self._speak_streamed(piece, index, len(pieces), save_as,
                                                              on_start if index == 0 else None)
                if segment:
                    queued.append(segment)
                if aborted:
                    unsaid += pieces[index + (1 if segment else 0):]
                    self.barge_in.set()
                    break
                continue
            out = RUNTIME / f"{self.id}-{self.counter}.wav"
            await self.until(self.engine.request({"op": "tts", "text": piece, "out": str(out)}))
            pcm, _ = soundfile.read(out, dtype="int16")
            out.unlink(missing_ok=True)
            if self.barge_in.is_set() or (self.user_speaking and not self.hold):
                self.barge_in.set()
                unsaid += pieces[index:]
                break
            if index == 0:
                self._stop_waiting()
                self._drop_fillers()  # the answer replaces "mmm, ya" and the waiting phrases
                self.filler_this_turn = self.fitted_this_turn = False
                self._log_latency()
                await self.state("speaking")
            if save_as and len(pieces) == 1:
                save_as.parent.mkdir(parents=True, exist_ok=True)
                soundfile.write(save_as, pcm.reshape(-1), OUT_RATE, subtype="PCM_16")
            segment = Segment(piece, pcm.reshape(-1), on_start=on_start if index == 0 else None)
            queued.append(segment)
            self.out.enqueue(segment)

    async def _speak_streamed(self, piece: str, index: int, total: int, save_as: Path | None,
                              on_start: Callable[[], None] | None = None):
        """Synthesize one piece in chunks and start playing at the first one. Returns the piece's
        segment (None if nothing played) and whether he cut in before it finished."""
        out = RUNTIME / f"{self.id}-{self.counter}"
        stream = self.engine.request_stream({"op": "tts_stream", "text": piece, "out": str(out)})
        iterator = stream.__aiter__()
        segment, saved, aborted = None, [], False
        try:
            while True:
                try:
                    path = await self.until(iterator.__anext__())
                except StopAsyncIteration:
                    break
                pcm = np.fromfile(path, dtype="<i2")
                Path(path).unlink(missing_ok=True)
                if self.barge_in.is_set() or (self.user_speaking and not self.hold):
                    aborted = True
                    break
                saved.append(pcm)
                if segment is None:
                    if index == 0:
                        self._stop_waiting()
                        self._drop_fillers()
                        self.filler_this_turn = self.fitted_this_turn = False
                        self._log_latency()
                        await self.state("speaking")
                    segment = Segment(piece, pcm, complete=False, on_start=on_start)
                    self.out.enqueue(segment)
                else:
                    segment.extend(pcm)
        finally:
            if segment:
                segment.finish()
            try:
                await iterator.aclose()
            except Exception:  # the call may already be over
                pass
        if save_as and total == 1 and saved and not aborted:
            save_as.parent.mkdir(parents=True, exist_ok=True)
            soundfile.write(save_as, np.concatenate(saved), OUT_RATE, subtype="PCM_16")
        return segment, aborted

    async def turn(self, text: str, opening: bool = False) -> dict:
        """One Claude turn: speak (unless the user already moved on), then get the answer."""
        try:
            self.last_activity = time.monotonic()
            if not opening and (self.utterances or self.user_has_more()):
                # The user talked while Claude was thinking: hand that over instead.
                said = await self.collect_answer(0)
                cut, self.pending_interruption = self.pending_interruption, None
                if said and cut:  # ...cutting off a piece said with --sigue: this text was not said either
                    return {"status": "interrumpido", "text": said, **join_cuts(cut, {"heard": "", "not_heard": text})}
                if said:
                    return {"status": "nuevo", "text": said}
            if self.speech_chain and not self.speech_chain.done():
                await self.until(self.queue_speech(text))  # right behind the pieces still sounding
                interrupted, self.pending_interruption = self.pending_interruption, None
            else:
                interrupted = await self.speak(text)
            said = await self.collect_answer(ANSWER_WAIT_SECONDS)
            if interrupted:
                return {"status": "interrumpido", "text": said, **interrupted}
            return {"status": "ok" if said else "silencio", "text": said}
        finally:
            self.last_activity = time.monotonic()
            if not self.ended:
                await self.state("thinking")


    # ----- agent mode (Herdr) -------------------------------------------------------------
    async def deliver(self, text: str) -> None:
        """Hand the user's words to the agent as a prompt in its terminal."""
        cut, self.pending_interruption = self.pending_interruption, None
        message = agent_message(text, cut, self.introduced)
        self.turn_sent_at = time.monotonic()
        self.introduced = True
        try:
            await herdr_agent.prompt(self.agent, message)
        except herdr_agent.HerdrError as exc:
            log.warning("could not reach agent %s: %s", self.agent, exc)
            await self.queue_speech("No pude pasarle tu mensaje al agente. Revisa herdr en la Mac.")
            return
        self._start_waiting()

    def queue_speech(self, text: str, save_as: Path | None = None) -> asyncio.Future:
        """Speak after whatever is already queued (agent mode, and --sigue pieces): synthesized while the one
        before still sounds, so it follows with no gap. If he barges in it is dropped and reported as not said."""
        generation = self.generation
        previous, previous_synthesized = self.speech_chain, self.speech_synthesized
        synthesized = self.speech_synthesized = asyncio.Event()

        async def run() -> None:
            try:
                if previous_synthesized:
                    await previous_synthesized.wait()
                if self.ended:
                    return
                if generation != self.generation:
                    cut = {"heard": "", "not_heard": text}
                else:
                    try:
                        cut = await self.speak(text, save_as, synthesized)
                    except CallEnded:
                        return
                if previous:
                    await asyncio.wait({previous})  # its report first: in the order they were said
                if cut:
                    self.pending_interruption = join_cuts(self.pending_interruption, cut)
                elif not self.ended and generation == self.generation and self.speech_chain is chain:
                    await self.state("listening" if self.agent_status != "working" else "thinking")
            finally:
                synthesized.set()

        chain = self.speech_chain = asyncio.ensure_future(run())
        return chain

    def play_recorded(self, path: Path, text: str) -> asyncio.Future:
        """Say a saved recording right away (no models needed), like queue_speech."""
        pcm, _ = soundfile.read(path, dtype="int16")
        generation = self.generation
        synthesized = self.speech_synthesized = asyncio.Event()

        async def run() -> None:
            try:
                self.transcript.append(("claude", text))
                await self.state("speaking")
                segment = Segment(text, pcm.reshape(-1), on_start=self.show_line("agent", text))
                self.out.enqueue(segment)
                synthesized.set()
                await self.until(segment.done.wait())
                await self.until(asyncio.sleep(PLAYOUT_DELAY))
                if not self.ended and generation == self.generation and self.speech_chain is chain:
                    await self.state("listening")
            except CallEnded:
                return
            finally:
                synthesized.set()

        chain = self.speech_chain = asyncio.ensure_future(run())
        return chain

    async def watch_agent(self) -> None:
        """Mirror the agent's Herdr state on the line: chime while it works."""
        offline = 0
        while not self.ended:
            status = await herdr_agent.status(self.agent)
            offline = offline + 1 if status == "offline" else 0
            if offline >= 3:
                await self.queue_speech("El agente se desconectó. Cuelgo.")
                await asyncio.wait({self.speech_chain})
                self.end("error", "agent offline")
                return
            self.agent_status = status
            speaking = self.out.playing or (self.speech_chain and not self.speech_chain.done())
            if not speaking and not self.user_speaking:
                wanted = "thinking" if status == "working" else "listening"
                if self.out.thinking != (wanted == "thinking"):
                    await self.state(wanted)
            await asyncio.sleep(0.7)


class Server:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.engine = VoiceEngine()
        self.jev = jev.Jev(CONFIG_DIR)
        self.texto = TextChannel()
        self.call: Call | None = None
        self.busy = asyncio.Lock()
        self.device_file = CONFIG_DIR / "device.json"
        self.push = None
        if config.get("service_account"):
            import push
            self.push = push.FcmSender(Path(config["service_account"]).expanduser())

    # ----- phone side ------------------------------------------------------------------
    def phone_authorized(self, request: web.Request) -> bool:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        token = token or request.query.get("token", "")
        return secrets.compare_digest(token, self.config["phone_token"])

    async def register(self, request: web.Request) -> web.Response:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        body = await request.json()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.device_file.write_text(json.dumps({"fcm_token": body["fcm_token"],
                                                "registered": time.time()}))
        log.info("phone registered")
        if self.call is None or self.call.ended:
            self.engine.warm(WARM_SECONDS)
        return web.json_response({"ok": True})

    async def decline(self, request: web.Request) -> web.Response:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        body = await request.json()
        if self.call and self.call.id == body.get("call_id") and not self.call.ws:
            self.call.declined = True
            self.call.end("rechazada")
        return web.json_response({"ok": True})

    async def flota(self, request: web.Request) -> web.Response:
        """Fleet status and quotas for the widget, as written by flota_snapshot.py (optional)."""
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        try:
            return web.Response(text=(CONFIG_DIR / "flota.json").read_text(), content_type="application/json")
        except OSError:
            return web.json_response({"error": "sin datos"}, status=503)

    async def fcm_token(self, request: web.Request) -> web.Response:
        """The phone's FCM token for a remote machine (agent API only): read on demand, so it follows token rotations."""
        try:
            device = json.loads(self.device_file.read_text())
        except FileNotFoundError:
            return web.json_response({"error": "sin teléfono registrado"}, status=404)
        return web.json_response({"fcm_token": device["fcm_token"], "registered": device.get("registered")})

    def contacts_config(self) -> list[dict]:
        return self.config.get("contacts", [])

    @staticmethod
    async def contact_status(contact: dict) -> str:
        """The agent's state; a contact reached by inbox is never 'offline' just because its pane is unseen."""
        status = await herdr_agent.status(contact["agent"])
        return "unknown" if status == "offline" and contact.get("inbox") else status

    async def contacts(self, request: web.Request) -> web.Response:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        if self.call is None or self.call.ended:
            self.engine.warm(WARM_SECONDS)  # the app is open: a call is likely, load the voice now
        listed = []
        for contact in self.contacts_config():
            status = await self.contact_status(contact)
            listed.append({"id": contact["id"], "name": contact["name"],
                           "subtitle": contact.get("subtitle", ""), "status": status})
        return web.json_response({"contacts": listed})

    async def dial(self, request: web.Request) -> web.Response:
        """The user calls an agent from the app."""
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        body = await request.json()
        contact = next((c for c in self.contacts_config() if c["id"] == body.get("contact")), None)
        if not contact:
            return web.json_response({"error": "unknown contact"}, status=404)
        if self.call and not self.call.ended:
            return web.json_response({"error": "busy"}, status=409)
        if await self.contact_status(contact) == "offline":
            return web.json_response({"error": "offline"}, status=503)
        call = Call(secrets.token_urlsafe(12), self.engine, agent=contact["agent"], inbound=True, jev=self.jev)
        self.call = call
        self.engine.start()
        asyncio.ensure_future(self._run_inbound(call, contact))
        log.info("user dialing %s (call %s)", contact["id"], call.id)
        return web.json_response({"call_id": call.id, "name": contact["name"]})

    async def _run_inbound(self, call: Call, contact: dict) -> None:
        try:
            await asyncio.wait_for(call.answered.wait(), CONNECT_SECONDS)
            await asyncio.wait_for(call.audio_up.wait(), CONNECT_SECONDS)
            greeting = contact.get("greeting", "¿Sí? Te escucho.")
            saved = greeting_path(greeting)
            if saved.exists():
                call.play_recorded(saved, greeting)  # heard while the models are still loading
            else:
                await call.state("thinking")
            await call.until(asyncio.shield(self.engine.ready))
        except (asyncio.TimeoutError, CallEnded):
            call.end("error", "phone never connected")
        except Exception as exc:
            call.end("error", f"voice engine: {exc}")
        if call.ended:
            await self._finish(call)
            return
        asyncio.ensure_future(call.watch_agent())
        asyncio.ensure_future(self._watch_idle(call))
        if not saved.exists():
            call.queue_speech(greeting, save_as=saved)
        await call.ended_event.wait()
        await self._finish(call)

    async def call_status(self, request: web.Request) -> web.Response:
        """Lets the app tell a live incoming call from a stale one Android kept around."""
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        call = self.call
        live = bool(call and call.id == request.query.get("call_id") and not call.ended and not call.ws)
        return web.json_response({"answerable": live})

    async def phone_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        call = self.call
        if not call or call.id != request.query.get("call_id") or call.ws or call.ended:
            raise web.HTTPGone()
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        call.ws = ws
        call.answered.set()
        log.info("phone answered call %s", call.id)
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                data = json.loads(message.data)
                if data["type"] == "offer":
                    await ws.send_str(json.dumps(await self._answer_offer(call, data)))
                elif data["type"] == "route":
                    call.phone_route(data)
                elif data["type"] == "hangup":
                    call.end("colgó")
                    break
        finally:
            call.end("colgó", "phone disconnected")
            log.info("phone left call %s", call.id)
            # Let go of this side whoever ended it: in turn mode with no turn running nobody else would close
            # the WebRTC connection, and the socket would stay open until the phone dropped it.
            await self._finish(call)
            if call.agent and call.ended.status == "colgó":
                try:
                    await herdr_agent.prompt(call.agent, f"[llamada] {NOMBRE} colgó la llamada.")
                except herdr_agent.HerdrError:
                    pass
        return ws

    async def _answer_offer(self, call: Call, offer: dict) -> dict:
        pc = RTCPeerConnection()
        call.pc = pc

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                asyncio.ensure_future(call.consume(track))

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("webrtc %s", pc.connectionState)
            if pc.connectionState in {"failed", "closed"}:
                call.end("colgó", f"webrtc {pc.connectionState}")

        await pc.setRemoteDescription(RTCSessionDescription(offer["sdp"], "offer"))
        pc.addTrack(call.out)
        await pc.setLocalDescription(await pc.createAnswer())
        return {"type": "answer", "sdp": pc.localDescription.sdp}

    # ----- local side (CLI) ------------------------------------------------------------
    async def start(self, request: web.Request) -> web.Response:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return web.json_response({"status": "error", "detail": "text required"}, status=400)
        if self.call and not self.call.ended:
            return web.json_response({"status": "error", "detail": "a call is already active"}, status=409)
        if not self.device_file.exists():
            return web.json_response({"status": "error", "detail": "no phone registered; open the app once"}, status=412)
        if not self.push:
            return web.json_response({"status": "error", "detail": "Firebase is not configured"}, status=500)
        call = Call(secrets.token_urlsafe(12), self.engine, agent=body.get("agent") or None, jev=self.jev)
        self.call = call
        self.engine.start()  # load models while the phone rings
        fcm_token = json.loads(self.device_file.read_text())["fcm_token"]
        try:
            await self.push.send(fcm_token, {"type": "incoming_call", "call_id": call.id,
                                             "caller": body.get("caller", "Claude"),
                                             "reason": body.get("reason", text)[:120]})
        except Exception as exc:
            call.end("error")
            await self._finish(call)
            return web.json_response({"status": "error", "detail": f"push failed: {exc}"}, status=502)
        log.info("ringing call %s", call.id)
        try:
            await asyncio.wait_for(call.answered.wait(), RING_SECONDS)
            await asyncio.wait_for(call.audio_up.wait(), CONNECT_SECONDS)
        except asyncio.TimeoutError:
            call.end("sin_respuesta" if not call.ws else "error", "no audio")
        if not call.ended:
            await call.state("thinking")
            try:
                await call.until(asyncio.shield(self.engine.ready))
            except CallEnded:
                pass
            except Exception as exc:
                call.end("error", f"voice engine: {exc}")
        if call.ended:
            if not call.ws:
                await self._cancel_ring(fcm_token, call)
            await self._finish(call)
            return web.json_response({"status": call.ended.status, "detail": call.ended.detail})
        asyncio.ensure_future(self._watch_idle(call))
        if call.agent:
            # Agent mode: say the opening, then his answers arrive as prompts via Herdr.
            asyncio.ensure_future(call.watch_agent())
            await call.queue_speech(text)
            if call.ended:
                await self._finish(call)
                return web.json_response({"status": call.ended.status})
            asyncio.ensure_future(self._finish_when_ended(call))
            return web.json_response({"status": "en_curso"})
        return await self._turn(call, text, opening=True)

    async def _finish_when_ended(self, call: Call) -> None:
        await call.ended_event.wait()
        await self._finish(call)

    async def say(self, request: web.Request) -> web.Response:
        call = self.call
        if not call or call.ended:
            if self.texto.active():  # a text conversation from Hermes, not a call: keep the reply
                body = await request.json()
                text = body.get("text", "").strip()
                if text:
                    self.texto.touch()
                    await self.texto.push(text, more=bool(body.get("sigue")))
                return web.json_response({"status": "en_curso"})
            status = call.ended.status if call and call.ended else "sin_llamada"
            return web.json_response({"status": status})
        body = await request.json()
        text = body.get("text", "").strip()
        if call.agent or body.get("sigue"):
            # Turn mode with --sigue: a piece said without waiting for his answer; the next `decir` follows it.
            call.last_activity = time.monotonic()
            call.queue_speech(text)
            return web.json_response({"status": "en_curso"})
        return await self._turn(call, text)

    async def end(self, request: web.Request) -> web.Response:
        call = self.call
        if not call or call.ended:
            if self.texto.active():
                text = (await request.json()).get("text", "").strip()
                if text:
                    await self.texto.push(text)
                return web.json_response({"status": "terminada", "text": "", "transcript": []})
            return web.json_response({"status": "sin_llamada"})
        text = (await request.json()).get("text", "").strip()
        async with self.busy:
            pending = call.take_utterances()
            try:
                if call.speech_chain:
                    await call.until(asyncio.wait({call.speech_chain}))
                if text:
                    await call.speak(text)
                await call.send({"type": "end"})
            except CallEnded:
                pass
            call.end("terminada")
            await self._finish(call)
        return web.json_response({"status": "terminada", "text": pending, "transcript": call.transcript})

    # ----- text conversation with the firstmate (from Hermes), local port only ----------------------
    async def texto_enviar(self, request: web.Request) -> web.Response:
        try:
            text = str((await request.json()).get("texto", "")).strip()
        except ValueError:
            text = ""
        return await self.enviar_a_firstmate(text)

    async def enviar_a_firstmate(self, text: str) -> web.Response:
        if not text:
            return web.json_response({"error": "no text"}, status=400)
        contact = next((c for c in self.contacts_config() if c.get("id") == "firstmate"), None)
        if not contact:
            return web.json_response({"error": "no firstmate contact configured"}, status=404)
        message = agent_message(text, None, self.texto.introduced, INTRO_TEXTO)
        try:
            await herdr_agent.prompt(contact["agent"], message)
        except herdr_agent.HerdrError as exc:
            return web.json_response({"error": f"could not reach the firstmate: {exc}"}, status=502)
        self.texto.introduced = True
        self.texto.touch()
        return web.json_response({"ok": True})

    async def texto_respuesta(self, request: web.Request) -> web.Response:
        """Wait up to `timeout` seconds for the firstmate's reply; whatever arrived is handed over once. With
        `tramos=1` each piece comes as soon as it is said; `completo` tells whether the last one is in."""
        try:
            timeout = max(0.0, min(float(request.query.get("timeout", "30")), 120.0))
        except ValueError:
            timeout = 30.0
        replies, complete = await self.texto.take(timeout, pieces=request.query.get("tramos") == "1")
        contact = next((c for c in self.contacts_config() if c.get("id") == "firstmate"), None)
        state = await self.contact_status(contact) if contact else "unknown"
        return web.json_response({"respuestas": replies, "completo": complete, "estado": state})

    # ----- the phone: dictate, talk to firstmate / Hermes, leave a note, hear the answer -----
    async def phone_dictar(self, request: web.Request) -> web.Response:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        return await self.dictar(request)

    async def phone_voz(self, request: web.Request) -> web.StreamResponse:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        return await self.voz(request)

    async def phone_respuesta(self, request: web.Request) -> web.Response:
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        return await self.texto_respuesta(request)

    async def phone_hablar(self, request: web.Request) -> web.Response:
        """The «Hablar» (talk) widget: quien = firstmate (text inbox), hermes (optional, hermes-agent) or nota (firstmate note)."""
        if not self.phone_authorized(request):
            raise web.HTTPUnauthorized()
        try:
            body = await request.json()
            text, quien = str(body.get("texto", "")).strip(), str(body.get("quien", ""))
        except ValueError:
            return web.json_response({"error": "bad request"}, status=400)
        if not text:
            return web.json_response({"error": "no text"}, status=400)
        if quien == "firstmate":
            return await self.enviar_a_firstmate(text)
        if quien == "hermes":
            try:
                return web.json_response({"respuesta": await preguntar_a_hermes(text)})
            except (OSError, asyncio.TimeoutError) as exc:
                log.warning("hermes: %s", exc)
                return web.json_response({"error": "hermes no contestó"}, status=502)
        if quien == "nota":
            ok = await guardar_nota(text)
            return web.json_response({"ok": ok}, status=200 if ok else 502)
        return web.json_response({"error": "quien desconocido"}, status=400)

    # ----- local helpers (e.g. a menu-bar app, not included): dictate and speak, local port only -------------
    async def dictar(self, request: web.Request) -> web.Response:
        """Raw 16 kHz mono int16 audio in, the transcription out (Whisper, same as the calls)."""
        pcm = await request.read()
        if len(pcm) < 16000 * 2 * 0.3:
            return web.json_response({"error": "audio too short"}, status=400)
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self.engine.start()
        path = RUNTIME / f"dictar-{secrets.token_hex(4)}.pcm"
        path.write_bytes(pcm)
        try:
            reply = await self.engine.request({"op": "stt", "pcm": str(path)})
        finally:
            path.unlink(missing_ok=True)
        self.engine.warm(WARM_SECONDS)
        return web.json_response({"text": reply.get("text", "")})

    async def voz(self, request: web.Request) -> web.StreamResponse:
        """Speak this text in the configured voice: raw 48 kHz mono int16 streamed as each chunk is generated."""
        try:
            text = str((await request.json()).get("text", "")).strip()
        except ValueError:
            text = ""
        pieces = split_for_speech(text)
        if not pieces:
            return web.json_response({"error": "no text"}, status=400)
        response = web.StreamResponse(headers={"Content-Type": "audio/L16; rate=48000; channels=1"})
        await response.prepare(request)
        self.engine.start()
        RUNTIME.mkdir(parents=True, exist_ok=True)
        try:
            for piece in pieces:
                stream = self.engine.request_stream(
                    {"op": "tts_stream", "text": piece, "out": str(RUNTIME / f"voz-{secrets.token_hex(4)}")})
                async for path in stream:
                    data = Path(path).read_bytes()
                    Path(path).unlink(missing_ok=True)
                    await response.write(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # the helper closed (he pressed again): stop speaking
        finally:
            self.engine.warm(WARM_SECONDS)
        with contextlib.suppress(Exception):
            await response.write_eof()
        return response

    async def jev_decidir(self, request: web.Request) -> web.Response:
        """The bar asks the same quick choices as a call: {"pregunta": "muletilla"|"interrupcion", "texto"} in,
        {"decision": category or verdict, or null without the jev-on flag, in doubt or past the timeout} out."""
        try:
            body = await request.json()
            question, text = body.get("pregunta"), str(body.get("texto", "")).strip()
        except (ValueError, AttributeError):
            return web.json_response({"error": "bad request"}, status=400)
        ask = {"muletilla": self.jev.filler_category, "interrupcion": self.jev.interruption}.get(question)
        if not ask or not text:
            return web.json_response({"error": "bad request"}, status=400)
        if not jev.enabled(CONFIG_DIR):
            return web.json_response({"decision": None})
        return web.json_response({"decision": await ask(text)})

    async def jev_calentar(self, request: web.Request) -> web.Response:
        """He started talking to the bar: open the connection to Jev before his words are transcribed."""
        if jev.enabled(CONFIG_DIR):
            self.jev.warm()
        return web.json_response({"ok": True})

    async def status(self, request: web.Request) -> web.Response:
        call = self.call
        return web.json_response({
            "phone_registered": self.device_file.exists(),
            "call": None if not call else {"id": call.id, "connected": call.phone_connected(),
                                           "ended": call.ended.status if call.ended else None},
        })

    async def _turn(self, call: Call, text: str, opening: bool = False) -> web.Response:
        async with self.busy:
            try:
                result = await call.turn(text, opening)
            except CallEnded as ended:
                await self._finish(call)
                return web.json_response({"status": ended.status, "text": call.take_utterances()})
            except Exception as exc:
                log.exception("turn failed")
                return web.json_response({"status": "error", "detail": str(exc)}, status=500)
        return web.json_response(result)

    async def _watch_idle(self, call: Call) -> None:
        while not call.ended:
            await asyncio.sleep(15)
            if not self.busy.locked() and time.monotonic() - call.last_activity > IDLE_HANGUP_SECONDS:
                await call.send({"type": "end"})
                call.end("terminada", "idle")
                await self._finish(call)

    async def _cancel_ring(self, fcm_token: str, call: Call) -> None:
        if call.declined:
            return
        try:
            await self.push.send(fcm_token, {"type": "cancel_call", "call_id": call.id})
        except Exception as exc:
            log.warning("cancel push failed: %s", exc)

    async def _finish(self, call: Call) -> None:
        if call.ws and not call.ws.closed:
            await call.ws.close()
        if call.pc:
            await call.pc.close()
        if self.call is call:
            self.engine.release_after(WARM_SECONDS)


# Optional: the «Hablar» widget can also ask Hermes (hermes-agent) if it is installed on this Mac.
HERMES = Path(os.environ.get("LLAMADA_HERMES") or Path.home() / ".local/bin/hermes")
HERMES_SESION = os.environ.get("LLAMADA_HERMES_SESION", "llamada-voz")  # the Hermes conversation it continues
HERMES_SKILLS = os.environ.get("LLAMADA_HERMES_SKILLS", "firstmate")    # the skills Hermes loads
HERMES_CONSULTA = ("Esta conversación es por voz: responde en español, "
                   "breve y natural, pensado para leerse en voz alta, sin markdown ni listas.\n\n")
# Optional: the firstmate home on this Mac, for the «nota rápida» of the «Hablar» widget (bin/fm-inbox.sh).
FM_HOME = Path(os.environ.get("LLAMADA_FM_HOME") or Path.home() / "firstmate")


async def preguntar_a_hermes(text: str) -> str:
    """Optional: ask Hermes (`hermes chat`, same session and skills every time); returns its cleaned answer."""
    env = dict(os.environ, PATH=f"/opt/homebrew/bin:{Path.home()}/.local/bin:/usr/bin:/bin")
    proc = await asyncio.create_subprocess_exec(
        str(HERMES), "chat", "-q", HERMES_CONSULTA + text, "-s", HERMES_SKILLS, "-Q",
        "--continue", HERMES_SESION, "--create-if-missing",
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        crudo, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    lines = [ln for ln in crudo.decode("utf-8", "replace").splitlines()
             if not ln.startswith("session_id:") and not ln.startswith("Aviso:")]
    respuesta = "\n".join(lines).strip()
    log.info("hermes: texto=%r salida=%d bytes código=%s", text[:120], len(crudo), proc.returncode)
    if not respuesta:
        log.warning("hermes sin respuesta: stdout=%r stderr=%r", crudo.decode("utf-8", "replace")[:300],
                    err.decode("utf-8", "replace")[-300:])
    return respuesta


async def guardar_nota(text: str) -> bool:
    """A quick note goes to the firstmate inbox (fm-inbox note): it is stored and the firstmate is notified."""
    proc = await asyncio.create_subprocess_exec(
        str(FM_HOME / "bin/fm-inbox.sh"), "note", "--", text, cwd=str(FM_HOME),
        env=dict(os.environ, FM_HOME=str(FM_HOME)),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait() == 0


async def ping(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_apps(server: Server) -> tuple[web.Application, web.Application]:
    local = web.Application()
    local.add_routes([web.post("/start", server.start), web.post("/say", server.say),
                      web.post("/end", server.end), web.get("/status", server.status),
                      web.post("/dictar", server.dictar), web.post("/voz", server.voz),
                      web.post("/jev", server.jev_decidir), web.post("/jev/calentar", server.jev_calentar),
                      web.post("/texto/enviar", server.texto_enviar), web.get("/texto/respuesta", server.texto_respuesta)])
    phone = web.Application()
    phone.add_routes([web.post("/register", server.register), web.post("/decline", server.decline),
                      web.get("/contacts", server.contacts), web.post("/dial", server.dial),
                      web.get("/call", server.call_status),
                      web.get("/ws", server.phone_ws), web.get("/ping", ping),
                      web.get("/flota", server.flota), web.post("/dictar", server.phone_dictar),
                      web.post("/voz", server.phone_voz), web.post("/hablar", server.phone_hablar),
                      web.get("/respuesta", server.phone_respuesta)])
    return local, phone


def build_agent_app(server: Server, token: str) -> web.Application:
    """The local API for agents and helpers on other machines (over Tailscale): same routes, bearer token required."""
    @web.middleware
    async def require_token(request: web.Request, handler):
        if not secrets.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            return web.json_response({"status": "error", "detail": "unauthorized"}, status=401)
        return await handler(request)

    agent = web.Application(middlewares=[require_token])
    agent.add_routes([web.post("/start", server.start), web.post("/say", server.say),
                      web.post("/end", server.end), web.get("/status", server.status),
                      web.get("/fcm-token", server.fcm_token),
                      web.post("/dictar", server.dictar), web.post("/voz", server.voz),
                      web.post("/jev", server.jev_decidir), web.post("/jev/calentar", server.jev_calentar),
                      web.post("/texto/enviar", server.texto_enviar), web.get("/texto/respuesta", server.texto_respuesta)])
    return agent


async def start_site(app: web.Application, host: str, port: int, retry_seconds: float = 15) -> web.AppRunner:
    """Listen on host:port; while the address does not exist yet (Tailscale after a reboot) keep trying."""
    while True:
        runner = web.AppRunner(app, handle_signals=False)
        await runner.setup()
        try:
            await web.TCPSite(runner, host, port).start()
        except OSError as exc:
            await runner.cleanup()
            log.warning("cannot listen on %s:%s yet (%s); retrying in %ss", host, port, exc, retry_seconds)
            await asyncio.sleep(retry_seconds)
            continue
        log.info("listening on %s:%s", host, port)
        return runner


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    config = load_config()
    server = Server(config)
    local, phone = build_apps(server)
    sites = [(local, "127.0.0.1", config["local_port"]), (phone, config["phone_host"], config["phone_port"])]
    if config.get("agent_host"):
        token = (CONFIG_DIR / "local-token").read_text(encoding="utf-8").strip()
        sites.append((build_agent_app(server, token), config["agent_host"], config["local_port"]))
    # The loopback and phone ports come up at once; the tailnet address may appear later.
    for app, host, port in sites[:2]:
        await start_site(app, host, port)
    for app, host, port in sites[2:]:
        asyncio.ensure_future(start_site(app, host, port))
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
