"""Records the short phrases the server plays around the agent's answers, with the configured voice and in the
configured language: acknowledgements ("mm, okay", ...), acknowledgements that fit a kind of phrase (with Jev on:
"you're welcome" after a thanks, ...), the neutral "mm" played first when Jev is on (fillers/mm/), the soft nods
(fillers/nod/) and the «still working» cues of fillers/wait/: a first phrase per kind of message (question, order,
other), the shorter ones that follow (more) and a soft chime (not a voice).

The phrases come from phrases/<language>.toml (see language.py and phrases/README.md). install.sh runs this; run
it again by hand after changing the voice, the language or a phrase:
  .venv/bin/python server/make_fillers.py              record what is missing or changed
  .venv/bin/python server/make_fillers.py --language es  pick the language (saved for the server too)
  .venv/bin/python server/make_fillers.py --force      record everything again
  .venv/bin/python server/make_fillers.py --check      only say what would be recorded
It speaks to the configured voice engine over its JSON-lines protocol (PHONECALL_ENGINE / PHONECALL_ENGINE_PYTHON,
default the repository's engine.py under .venv), so a clip sounds exactly like the calls. Clips go to
~/.config/phonecall/fillers/ (PHONECALL_CONFIG moves it), 48 kHz mono wav, with a manifest (fillers/phrases.json)
of what each one says and in which voice: a clip is recorded again when its phrase or the voice changes, and clips
of a phrase that is gone (another language, a removed line) are deleted. Nothing recorded lives in the repository.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
import soundfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CONFIG = Path(os.environ.get("PHONECALL_CONFIG", Path.home() / ".config" / "phonecall"))
FILLERS_DIR = CONFIG / "fillers"
MANIFEST = FILLERS_DIR / "phrases.json"
CHIME = "wait/chime/soft"
ENGINE_PYTHON = Path(os.environ.get("PHONECALL_ENGINE_PYTHON") or REPO / ".venv" / "bin" / "python")
ENGINE = Path(os.environ.get("PHONECALL_ENGINE") or HERE / "engine.py")
TRIMMED = ("mm/", "nod/", "wait/")  # clips queued back to back: no silence around them

sys.path.insert(0, str(HERE))
import language  # noqa: E402

PACK = language.PACK
FILLERS = PACK["fillers"]
FILLER_CATEGORIES = PACK["filler_categories"]
NODS = PACK["nods"]   # fillers/nod/: soft nods while the user talks on; <= 0.6 s
MM = PACK["mm"]       # jev-on: the neutral clip at 0.3 s, before the fitted one; <= 0.5 s
WAIT = PACK["wait"]   # fillers/wait/<kind>/: first phrase by Jev's category, then "more", then the chime


def clips(pack: dict) -> dict[str, str]:
    """Every clip to record, as {path under fillers/ without .wav: phrase}."""
    out = {name: text for name, text in pack["fillers"].items()}
    out |= {f"mm/{name}": text for name, text in pack["mm"].items()}
    out |= {f"nod/{name}": text for name, text in pack["nods"].items()}
    for category, texts in pack["filler_categories"].items():
        out |= {f"{category}/{name}": text for name, text in texts.items()}
    for kind, texts in pack["wait"].items():
        out |= {f"wait/{kind}/{name}": text for name, text in texts.items()}
    return out


def voice_id() -> str:
    """What the clips would sound like: the engine and, for the default one, the voice and its reference clip;
    for engine_openai.py its model, voice and API."""
    h = hashlib.sha256(ENGINE.name.encode())
    if ENGINE.name == "engine.py":
        voice = Path(os.environ.get("PHONECALL_VOICE") or REPO / "voices" / "my-voice" / "voice.json")
        try:
            h.update(voice.read_bytes())
            h.update((voice.parent / json.loads(voice.read_text(encoding="utf-8"))["reference"]).read_bytes())
        except (OSError, ValueError, KeyError):
            h.update(b"no voice")
    else:
        for key in ("PHONECALL_API_BASE", "PHONECALL_TTS_MODEL", "PHONECALL_TTS_VOICE"):
            h.update(os.environ.get(key, "").encode() + b"\0")
    return h.hexdigest()[:16]


def plan(wanted: dict[str, str], manifest: dict, voice: str, force: bool) -> tuple[list[str], list[str]]:
    """(clips to record, clips to delete). The manifest is {"voice": ..., "clips": {path: phrase}}."""
    recorded = manifest.get("clips", {}) if manifest.get("voice") == voice else {}
    record = [p for p, text in wanted.items()
              if force or recorded.get(p) != text or not (FILLERS_DIR / f"{p}.wav").exists()]
    stale = [p for p in manifest.get("clips", {}) if p not in wanted and p != CHIME]
    return record, stale


def soft_signal(rate: int = 48000) -> np.ndarray:
    """Two quiet rounded notes (E5, A5), ~0.45 s: «still here» without words."""
    notes = []
    for hz, length in ((659.3, 0.2), (880.0, 0.25)):
        t = np.arange(int(length * rate)) / rate
        envelope = np.sin(np.pi * np.minimum(t / 0.03, 1.0) / 2) * np.exp(-6 * t)
        notes.append(np.sin(2 * np.pi * hz * t) * envelope)
    wave = np.concatenate(notes)
    return (0.12 * 32767 * wave / np.abs(wave).max()).astype(np.int16)


def trim_silence(path: Path, lead: float = 0.03, tail: float = 0.06) -> None:
    """Cut the silence the voice leaves around a clip, so the next one follows without a gap
    (the "mm" is queued right before the fitted filler)."""
    pcm, rate = soundfile.read(path, dtype="int16")
    loud = np.flatnonzero(np.abs(pcm.astype(np.int32)) > 0.02 * np.abs(pcm).max())
    if len(loud):
        start, end = max(0, loud[0] - int(lead * rate)), min(len(pcm), loud[-1] + int(tail * rate))
        soundfile.write(path, pcm[start:end], rate, subtype="PCM_16")


class Voice:
    """The configured engine, spoken to like the server does (one JSON object per line)."""

    def __init__(self) -> None:
        env = dict(os.environ, PHONECALL_LANGUAGE=language.LANGUAGE)
        self.process = subprocess.Popen([str(ENGINE_PYTHON), str(ENGINE)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, env=env)
        self.next_id = 0
        ready = self._reply()
        if not ready.get("ok"):
            raise SystemExit(f"the voice engine did not start: {ready.get('error')}")

    def _reply(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            raise SystemExit("the voice engine stopped (see the messages above)")
        return json.loads(line)

    def synthesize(self, text: str, out: Path) -> None:
        self.next_id += 1
        self.process.stdin.write(json.dumps({"id": self.next_id, "op": "tts", "text": text, "out": str(out)}) + "\n")
        self.process.stdin.flush()
        reply = self._reply()
        if not reply.get("ok"):
            raise SystemExit(f"could not record {text!r}: {reply.get('error')}")

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=30)
        self.process.stdout.close()


def main(argv: list[str]) -> int:
    global PACK
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    args.add_argument("--language", help=f"language code with a phrases/<code>.toml ({', '.join(language.available())})")
    args.add_argument("--force", action="store_true", help="record every clip again")
    args.add_argument("--check", action="store_true", help="only print what would be recorded or deleted")
    opts = args.parse_args(argv)

    code = (opts.language or language.configured_code()).strip().lower()
    try:
        PACK = language.load(code)  # a wrong language or a broken file stops here, unlike the server
    except language.PhrasesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    language.LANGUAGE = code
    if opts.language and not opts.check:
        CONFIG.mkdir(parents=True, exist_ok=True)
        (CONFIG / "language").write_text(code + "\n", encoding="utf-8")

    wanted = clips(PACK)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    voice = voice_id()
    record, stale = plan(wanted, manifest, voice, opts.force)
    print(f"{PACK['name']} ({code}): {len(wanted)} clips, {len(record)} to record, {len(stale)} to delete")
    if opts.check:
        for p in record:
            print("  record", p)
        for p in stale:
            print("  delete", p)
        return 0

    if opts.force:  # also clips this script did not record (an install from before the manifest)
        stale += [str(w.relative_to(FILLERS_DIR).with_suffix("")) for w in FILLERS_DIR.rglob("*.wav")]
        stale = sorted({p for p in stale if p not in wanted and p != CHIME})
    for p in stale:
        (FILLERS_DIR / f"{p}.wav").unlink(missing_ok=True)
        print("deleted", p)
    for folder in sorted(FILLERS_DIR.rglob("*"), reverse=True) if FILLERS_DIR.is_dir() else []:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    chime = FILLERS_DIR / f"{CHIME}.wav"
    if not chime.exists() or opts.force:
        chime.parent.mkdir(parents=True, exist_ok=True)
        soundfile.write(chime, soft_signal(), 48000, subtype="PCM_16")
        print("recorded", CHIME)

    done = {p: text for p, text in manifest.get("clips", {}).items()
            if p in wanted and p not in record and manifest.get("voice") == voice}
    if record:
        engine = Voice()
        try:
            for p in record:
                target = FILLERS_DIR / f"{p}.wav"
                target.parent.mkdir(parents=True, exist_ok=True)
                # Recorded beside fillers/ (same disk, outside the folders the server reads): a cut-off run
                # never leaves half a clip.
                with tempfile.TemporaryDirectory(prefix=".recording-", dir=CONFIG) as tmp:
                    part = Path(tmp) / "clip.wav"
                    engine.synthesize(wanted[p], part)
                    if p.startswith(TRIMMED):
                        trim_silence(part)
                    os.replace(part, target)
                done[p] = wanted[p]
                MANIFEST.write_text(json.dumps({"language": code, "voice": voice, "clips": done},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
                print("recorded", p)
        finally:
            engine.close()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"language": code, "voice": voice, "clips": done | {CHIME: ""}},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
