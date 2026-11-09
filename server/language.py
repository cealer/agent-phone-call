"""What the call says and listens for, in the language the user speaks: one phrases/<code>.toml per language.

The language is PHONECALL_LANGUAGE, else the code in ~/.config/phonecall/language (install.sh --language writes
it), else `en`. Its file is looked up first in ~/.config/phonecall/phrases/ (your own edits or a new language,
kept across updates), then in the repository's phrases/ (en and es ship). The same setting tells Whisper which
language to transcribe (engine.py, engine_openai.py), which words mean the user is not done talking (server.py),
the default greeting, and the short phrases make_fillers.py records. The agent prompts themselves are in English
for every language: the agent is told which language to speak back.

The table names are fixed. The categories of `filler_categories` and `wait` are Jev's (jev.py): Jev classifies
what the user said into the same categories whatever the language, and the phrase file only says which clips
answer each one. load() rejects a file whose categories differ, so a translation can never break Jev.
"""
import os
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("PHONECALL_CONFIG", Path.home() / ".config" / "phonecall"))
DEFAULT = "en"

# Jev's filler categories (jev.FILLER_CATEGORIES, checked by the tests) and the kinds of waiting phrases.
FILLER_CATEGORIES = ("question", "order", "greeting", "goodbye", "thanks", "other")
WAIT_KINDS = ("question", "order", "other", "more")
TEXTS = ("name", "whisper", "whisper_prompt", "greeting", "agent_unreachable", "agent_offline", "hermes_preamble")
CLIP_GROUPS = ("fillers", "nods", "mm")
CLIP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PhrasesError(ValueError):
    pass


def configured_code() -> str:
    code = os.environ.get("PHONECALL_LANGUAGE", "").strip()
    if not code:
        try:
            code = (CONFIG / "language").read_text(encoding="utf-8").strip()
        except OSError:
            code = ""
    return (code or DEFAULT).lower()


def phrases_file(code: str) -> Path | None:
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]+)?", code):
        return None
    for folder in (CONFIG / "phrases", REPO / "phrases"):
        if (folder / f"{code}.toml").is_file():
            return folder / f"{code}.toml"
    return None


def available() -> list[str]:
    """The language codes there is a phrase file for."""
    return sorted({p.stem for folder in (REPO / "phrases", CONFIG / "phrases") for p in folder.glob("*.toml")})


def _clips(where: str, table) -> dict[str, str]:
    if not isinstance(table, dict) or not table:
        raise PhrasesError(f"{where}: needs at least one clip")
    for name, text in table.items():
        if not CLIP_NAME.match(name):
            raise PhrasesError(f"{where}.{name}: clip names are lowercase letters, digits and dashes")
        if not isinstance(text, str) or not text.strip():
            raise PhrasesError(f"{where}.{name}: needs a phrase")
    return dict(table)


def _categories(where: str, table, expected: tuple[str, ...]) -> dict[str, dict[str, str]]:
    if not isinstance(table, dict) or set(table) != set(expected):
        found = sorted(table) if isinstance(table, dict) else []
        raise PhrasesError(f"{where}: needs exactly the tables {', '.join(expected)} (found {', '.join(found)})")
    return {kind: _clips(f"{where}.{kind}", table[kind]) for kind in expected}


def parse(data: dict, where: str = "phrases") -> dict:
    """A phrase file's contents, checked: every piece the server needs, Jev's categories, safe clip names."""
    pack = {}
    for key in TEXTS:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise PhrasesError(f"{where}: needs `{key}`")
        pack[key] = data[key]
    trailing = data.get("trailing")
    if not isinstance(trailing, list) or not all(isinstance(w, str) and w for w in trailing):
        raise PhrasesError(f"{where}: `trailing` is a list of words")
    pack["trailing"] = {w.lower() for w in trailing}
    for group in CLIP_GROUPS:
        pack[group] = _clips(f"{where}: [{group}]", data.get(group))
    pack["filler_categories"] = _categories(f"{where}: [filler_categories]", data.get("filler_categories"),
                                            FILLER_CATEGORIES)
    pack["wait"] = _categories(f"{where}: [wait]", data.get("wait"), WAIT_KINDS)
    unknown = set(data) - set(TEXTS) - {"trailing", "filler_categories", "wait", *CLIP_GROUPS}
    if unknown:
        raise PhrasesError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    return pack


def load(code: str) -> dict:
    path = phrases_file(code)
    if path is None:
        raise PhrasesError(f"no phrases/{code}.toml (available: {', '.join(available())})")
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise PhrasesError(f"{path}: {exc}") from exc
    return parse(data, str(path))


def _startup() -> tuple[str, dict]:
    code = configured_code()
    try:
        return code, load(code)
    except PhrasesError as exc:
        if code == DEFAULT:
            raise
        # The server keeps working in English rather than not starting; make_fillers.py fails instead.
        print(f"language {code!r}: {exc}; using {DEFAULT}", file=sys.stderr)
        return DEFAULT, load(DEFAULT)


LANGUAGE, PACK = _startup()
