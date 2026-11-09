"""llamada: phone the user and talk, one turn per command.

  llamada iniciar "texto"   ring the phone, say the text, print what the user answers
  llamada decir "texto"     say the text, print the next answer
  llamada decir --sigue "texto"   say a first piece now and go on working: returns at once, the next
                                  `decir` follows it with no gap (and Hermes gets them all together)
  llamada colgar "texto"    say goodbye and hang up (the goodbye is required)
  llamada estado            server and call status

Text may also come on stdin ("-"). He can talk at any time, so besides his answer:
  * he cut you off  -> first line "[te interrumpió]" plus what he heard and did not hear
  * he spoke while you were thinking -> first line "[habló mientras pensabas]"; your text
    was NOT said: read his words and decide again
Exit codes: 0 answered, 6 interrupted you, 7 spoke while you thought (text not said),
3 no answer/declined, 4 user hung up, 5 user stayed silent, 1 error.
"""
import json
import os
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("LLAMADA_PORT", "8770"))
CONFIG_DIR = os.path.expanduser(os.environ.get("LLAMADA_CONFIG", "~/.config/llamada"))


def _setting(env: str, filename: str, default: str) -> str:
    """An environment variable, else the first line of a file in the config dir, else the default."""
    if os.environ.get(env):
        return os.environ[env]
    try:
        with open(os.path.join(CONFIG_DIR, filename), encoding="utf-8") as handle:
            return handle.read().strip() or default
    except OSError:
        return default


# Another machine reaches the Mac over Tailscale with a bearer token; on the Mac itself, loopback.
HOST = _setting("LLAMADA_HOST", "host", "127.0.0.1")
TOKEN = _setting("LLAMADA_TOKEN", "local-token", "") if HOST != "127.0.0.1" else ""
EXIT = {"ok": 0, "interrumpido": 6, "nuevo": 7, "silencio": 5, "sin_respuesta": 3, "rechazada": 3, "colgó": 4,
        "terminada": 0, "sin_llamada": 4}
MESSAGES = {"sin_respuesta": "No contestó.", "rechazada": "Rechazó la llamada.",
            "colgó": "Colgó la llamada.", "silencio": "(No dijo nada.)",
            "terminada": "Llamada terminada.", "sin_llamada": "No hay una llamada activa."}


def call(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        return json.load(exc)
    except urllib.error.URLError as exc:
        return {"status": "error", "detail": f"call server not running ({exc.reason})"}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"iniciar", "decir", "colgar", "estado"}:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == "estado":
        print(json.dumps(call("/status"), ensure_ascii=False, indent=2))
        return 0
    words = argv[2:]
    sigue = command == "decir" and words[:1] == ["--sigue"]
    text = " ".join(words[1:] if sigue else words).strip()
    if text in {"", "-"}:
        text = "" if command == "colgar" and sys.stdin.isatty() else sys.stdin.read().strip()
    if not text:  # colgar too: a mute hang-up just cuts the user off
        print("Falta el texto.", file=sys.stderr)
        return 2
    path = {"iniciar": "/start", "decir": "/say", "colgar": "/end"}[command]
    body = {"text": text}
    if sigue:
        body["sigue"] = True
    if os.environ.get("HERDR_ENV") == "1" and os.environ.get("HERDR_PANE_ID"):
        # Inside Herdr: his answers come back to this pane as "[llamada] USUARIO dijo: …" prompts.
        body["agent"] = os.environ["HERDR_PANE_ID"]
    result = call(path, body)
    status = result.get("status", "error")
    if status == "error":
        print(f"Error: {result.get('detail', result)}", file=sys.stderr)
        return 1
    if status == "en_curso":
        if command == "iniciar":
            print("Contestó y ya escuchó tu mensaje. Lo que diga te llegará como «[llamada] USUARIO dijo: …».")
        return 0
    if status == "ok":
        print(result["text"])
    elif status == "interrumpido":
        print("[USUARIO te interrumpió mientras TÚ hablabas]")
        print(f"YO dije (él alcanzó a oír): «{result.get('heard', '')}»")
        print(f"YO no alcancé a decir: «{result.get('not_heard', '')}»")
        print(f"USUARIO dijo: {result.get('text') or '(nada claro)'}")
    elif status == "nuevo":
        print("[habló mientras pensabas; tu texto no se dijo]")
        print(result["text"])
    else:
        print(MESSAGES.get(status, status))
        if result.get("text"):  # what he said before the call ended
            print(f"Antes dijo: {result['text']}")
    return EXIT.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
