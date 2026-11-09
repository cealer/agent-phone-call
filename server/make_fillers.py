"""Records the short phrases the server plays while the agent thinks, in the configured voice and language:
acknowledgements ("mm, okay", ...), acknowledgements that fit a kind of phrase (with Jev on: "you're welcome"
after a thanks, ...), the neutral "mm" played first when Jev is on (fillers/mm/), the soft nods (fillers/nod/),
waiting reassurances (waiting/, the old layout) and the «still working» cues of fillers/wait/: a first phrase per
kind of message (question, order, other), the shorter ones that follow (more) and a soft chime (not a voice).

The phrases come from language.py (PHONECALL_LANGUAGE, default en). Run once, and again if the voice or the
language changes, under the repository root venv:
  ../.venv/bin/python make_fillers.py   (from server/; --force records everything again)
Writes 48 kHz mono wavs into ~/.config/phonecall/fillers/, fillers/<category>/ and waiting/ (PHONECALL_CONFIG moves
them). The categories are jev.py's FILLER_CATEGORIES; keep each phrase under 1.5 s. In waiting/ (used only without
fillers/wait/), names starting with "first" are said first.
"""
import os
from pathlib import Path
import sys

import numpy as np
import soundfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
from language import PACK  # noqa: E402

CONFIG = Path(os.environ.get("PHONECALL_CONFIG", Path.home() / ".config" / "phonecall"))
FILLERS = PACK["fillers"]
FILLER_CATEGORIES = PACK["filler_categories"]
NODS = PACK["nods"]          # fillers/nod/: soft nods while the user talks on; <= 0.6 s
MM = PACK["mm"]              # jev-on: the neutral clip at 0.3 s, before the fitted one; <= 0.5 s
WAITING = PACK["waiting"]
# fillers/wait/<folder>: the server says one of "question"/"order"/"other" (by Jev's category; "other" without a
# decision) about 4 s after the filler if the answer has not started, then up to 3 of "more", then the chime.
WAIT = PACK["wait"]


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


if __name__ == "__main__":
    voice = engine.Engine()
    folders = [("fillers", FILLERS), ("waiting", WAITING)]
    folders.append(("fillers/mm", MM))
    folders.append(("fillers/nod", NODS))
    folders += [(f"fillers/{category}", texts) for category, texts in FILLER_CATEGORIES.items()]
    folders += [(f"fillers/wait/{name}", texts) for name, texts in WAIT.items()]
    signal = CONFIG / "fillers" / "wait" / "chime" / "soft.wav"
    if not signal.exists() or "--force" in sys.argv:
        signal.parent.mkdir(parents=True, exist_ok=True)
        soundfile.write(signal, soft_signal(), 48000, subtype="PCM_16")
        print("recorded fillers/wait/chime soft")
    for folder, texts in folders:
        (CONFIG / folder).mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            target = CONFIG / folder / f"{name}.wav"
            if target.exists() and "--force" not in sys.argv:
                continue
            voice.synthesize(text, target)
            if folder in ("fillers/mm", "fillers/nod") or folder.startswith("fillers/wait/"):
                trim_silence(target)
            print("recorded", folder, name)
