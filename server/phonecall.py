"""phonecall: phone the user and talk, one turn per command.

  phonecall start "text"     ring the phone, say the text, print what the user answers
  phonecall say "text"       say the text, print the next answer
  phonecall say --more "text"   say a first piece now and go on working: returns at once, the next
                                `say` follows it with no gap (and Hermes gets them all together)
  phonecall hangup "text"    say goodbye and hang up (the goodbye is required)
  phonecall status           server and call status

Text may also come on stdin ("-"). The user can talk at any time, so besides the answer:
  * they cut you off -> first line "[USER interrupted you while YOU were speaking]" plus what they heard and did not
  * they spoke while you were thinking -> first line "[spoke while you were thinking; your text was not said]":
    your text was NOT said: read their words and decide again
Exit codes: 0 answered, 6 interrupted you, 7 spoke while you thought (text not said),
3 no answer/declined, 4 user hung up, 5 user stayed silent, 1 error.
"""
import json
import os
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("PHONECALL_PORT", "8770"))
CONFIG_DIR = os.path.expanduser(os.environ.get("PHONECALL_CONFIG", "~/.config/phonecall"))


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
HOST = _setting("PHONECALL_HOST", "host", "127.0.0.1")
TOKEN = _setting("PHONECALL_TOKEN", "local-token", "") if HOST != "127.0.0.1" else ""
EXIT = {"ok": 0, "interrupted": 6, "new": 7, "silence": 5, "no_answer": 3, "declined": 3, "hung_up": 4,
        "ended": 0, "no_call": 4}
MESSAGES = {"no_answer": "No answer.", "declined": "The call was declined.",
            "hung_up": "The user hung up.", "silence": "(The user said nothing.)",
            "ended": "Call ended.", "no_call": "There is no active call."}
COMMANDS = {"start": "/start", "say": "/say", "hangup": "/end"}


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
    if len(argv) < 2 or argv[1] not in {*COMMANDS, "status"}:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == "status":
        print(json.dumps(call("/status"), ensure_ascii=False, indent=2))
        return 0
    words = argv[2:]
    more = command == "say" and words[:1] == ["--more"]
    text = " ".join(words[1:] if more else words).strip()
    if text in {"", "-"}:
        text = "" if command == "hangup" and sys.stdin.isatty() else sys.stdin.read().strip()
    if not text:  # hangup too: a mute hang-up just cuts the user off
        print("The text is missing.", file=sys.stderr)
        return 2
    body = {"text": text}
    if more:
        body["more"] = True
    if os.environ.get("HERDR_ENV") == "1" and os.environ.get("HERDR_PANE_ID"):
        # Inside Herdr: the user's answers come back to this pane as "[phonecall] USER said: …" prompts.
        body["agent"] = os.environ["HERDR_PANE_ID"]
    result = call(COMMANDS[command], body)
    status = result.get("status", "error")
    if status == "error":
        print(f"Error: {result.get('detail', result)}", file=sys.stderr)
        return 1
    if status == "in_progress":
        if command == "start":
            print("They answered and already heard your message. What they say will reach you as "
                  "«[phonecall] USER said: …».")
        return 0
    if status == "ok":
        print(result["text"])
    elif status == "interrupted":
        print("[USER interrupted you while YOU were speaking]")
        print(f"YOU said (they heard): «{result.get('heard', '')}»")
        print(f"YOU did not get to say: «{result.get('not_heard', '')}»")
        print(f"USER said: {result.get('text') or '(nothing clear)'}")
    elif status == "new":
        print("[spoke while you were thinking; your text was not said]")
        print(result["text"])
    else:
        print(MESSAGES.get(status, status))
        if result.get("text"):  # what they said before the call ended
            print(f"Before that they said: {result['text']}")
    return EXIT.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
