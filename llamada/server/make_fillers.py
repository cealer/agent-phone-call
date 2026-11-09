"""Records the short phrases the server plays while the agent thinks: acknowledgements ("mmm, ya", ...),
acknowledgements that fit a kind of phrase (with Jev on: "de nada" after a thanks, ...),
the neutral "mm" played first when Jev is on (fillers/mm/)
waiting reassurances ("revisando un momento", "ya casi lo tengo", ...)
and the «still working» cues of fillers/espera/: a first phrase per kind of message (pregunta, orden, otro),
the shorter ones that follow (seguir) and a soft signal (senal, not a voice).

Run once (and again if the voice changes) under the repository root venv:
  ../../.venv/bin/python make_fillers.py   (from llamada/server)
Writes 48 kHz mono wavs into ~/.config/llamada/fillers/, fillers/<category>/ and waiting/ (LLAMADA_CONFIG moves them).
The categories are jev.py's FILLER_CATEGORIES; keep each phrase under 1.5 s.
In waiting/ (the old layout, used only without espera/), names starting with "primero" are said first.
In fillers/espera/ keep every phrase under 1.5 s.
"""
import os
from pathlib import Path
import sys

import numpy as np
import soundfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

CONFIG = Path(os.environ.get("LLAMADA_CONFIG", Path.home() / ".config" / "llamada"))
FILLERS = {"mmm-ya": "Mmm, ya.", "ok": "Ok.", "si-te-escucho": "Sí, te escucho.", "dime": "Dime.",
           "mmm-a-ver": "Mmm, a ver."}
FILLER_CATEGORIES = {
    "pregunta": {"a-ver": "Mmm, a ver.", "dejame-ver": "Déjame ver.", "lo-reviso": "Lo reviso."},
    "orden": {"ok-dale": "Ok, dale.", "entendido": "Entendido.", "listo-voy": "Listo, voy."},
    "saludo": {"hola-te-escucho": "Hola, te escucho.", "hola-aqui-estoy": "¡Hola! Aquí estoy."},
    "despedida": {"listo-chao": "Listo, chao.", "hasta-luego": "Dale, hasta luego."},
    "agradecimiento": {"de-nada": "De nada.", "con-gusto": "Con gusto."},
    "otro": {"mmm-ya": "Mmm, ya.", "entiendo": "Ok, entiendo.", "aja": "Ajá."},
}
NODS = {"aja": "Ajá.", "mm": "Mm.", "ya": "Ya.", "si": "Sí."}  # fillers/asentir/: soft nods while he talks on; <= 0.6 s
MM = {"mm": "Mm.", "aja": "Ajá."}  # jev-on: the neutral clip at 0.3 s, before the fitted one; <=0.5 s
WAITING = {"primero-revisando": "Revisando un momento, por favor.",
           "primero-un-momento": "Un momento, lo reviso.",
           "despues-ya-casi": "Ya casi tengo la respuesta.",
           "despues-sigo": "Sigo revisando, un momento más.",
           "despues-un-poco": "Un poquito más y te cuento.",
           "despues-casi-listo": "Ya casi lo tengo."}

# fillers/espera/<folder>: the server says one of "pregunta"/"orden"/"otro" (by Jev's category; "otro" without a
# decision) about 4 s after the muletilla if the answer has not started, then up to 3 of "seguir", then the signal.
ESPERA = {
    "pregunta": {"dejame-revisarlo": "Déjame revisarlo.", "lo-estoy-mirando": "Lo estoy mirando."},
    "orden": {"lo-estoy-haciendo": "Lo estoy haciendo.", "dame-un-momento": "Dame un momento."},
    "otro": {"un-momento": "Un momento."},
    "seguir": {"sigo-con-ello": "Sigo con ello.", "ya-casi": "Ya casi.", "un-poco-mas": "Un poco más.",
               "casi-listo": "Casi listo."},
}


def soft_signal(rate: int = 48000) -> np.ndarray:
    """Two quiet rounded notes (E5, A5), ~0.45 s: «sigo vivo» without words."""
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
    folders.append(("fillers/asentir", NODS))
    folders += [(f"fillers/{category}", texts) for category, texts in FILLER_CATEGORIES.items()]
    folders += [(f"fillers/espera/{name}", texts) for name, texts in ESPERA.items()]
    signal = CONFIG / "fillers" / "espera" / "senal" / "suave.wav"
    if not signal.exists() or "--force" in sys.argv:
        signal.parent.mkdir(parents=True, exist_ok=True)
        soundfile.write(signal, soft_signal(), 48000, subtype="PCM_16")
        print("recorded fillers/espera/senal suave")
    for folder, texts in folders:
        (CONFIG / folder).mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            target = CONFIG / folder / f"{name}.wav"
            if target.exists() and "--force" not in sys.argv:
                continue
            voice.synthesize(text, target)
            if folder in ("fillers/mm", "fillers/asentir") or folder.startswith("fillers/espera/"):
                trim_silence(target)
            print("recorded", folder, name)
