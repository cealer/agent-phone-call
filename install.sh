#!/bin/sh
# Installs agent-phone-call (the `phonecall` command). Run from the repository root.
#
#   sh install.sh [--language CODE]   the Mac that runs the call server (Apple Silicon, Homebrew)
#   sh install.sh --fillers [--language CODE] [--force]
#                            only (re)record the short phrases, e.g. after setting up the voice
#                            (--force: record all of them again and clear clips it did not record)
#   sh install.sh --remote   another machine whose agent should call (a VPS, Linux or macOS):
#                            only the `phonecall` command and the skill, nothing else
#
# CODE is the language you speak on calls: a file phrases/CODE.toml (en and es ship; default en, or the
# language chosen last time). Run from a terminal without --language, it asks.
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
CONFIG="${PHONECALL_CONFIG:-$HOME/.config/phonecall}"
MODE=install
LANGUAGE=""
FORCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) MODE=remote ;;
    --fillers) MODE=fillers ;;
    --force) FORCE=--force ;;
    --language) LANGUAGE="$2"; shift ;;
    --language=*) LANGUAGE="${1#--language=}" ;;
    *) echo "Unknown option: $1 (see the top of install.sh)"; exit 1 ;;
  esac
  shift
done
mkdir -p ~/.local/bin "$CONFIG" ~/.claude/skills/phonecall
chmod 700 "$CONFIG"

choose_language() {
  current=$(cat "$CONFIG/language" 2>/dev/null || echo en)
  if [ -z "$LANGUAGE" ] && [ -t 0 ]; then
    printf 'Language you speak on calls (%s) [%s]: ' "$(ls phrases "$CONFIG/phrases" 2>/dev/null | sed -n 's/\.toml$//p' | sort -u | tr '\n' ' ' | sed 's/ $//')" "$current"
    read -r LANGUAGE
  fi
  LANGUAGE=$(echo "${LANGUAGE:-$current}" | tr 'A-Z' 'a-z')
  [ -f "phrases/$LANGUAGE.toml" ] || [ -f "$CONFIG/phrases/$LANGUAGE.toml" ] || {
    echo "No phrases/$LANGUAGE.toml. Available: $(ls phrases | sed -n 's/\.toml$//p' | tr '\n' ' ')"
    echo "To add a language, see phrases/README.md."
    exit 1
  }
  echo "$LANGUAGE" > "$CONFIG/language"
  echo "Conversation language: $LANGUAGE (saved in $CONFIG/language)"
}

# The short phrases (acknowledgements, nods, waiting cues) recorded with your voice, in your language.
# They need the voice set up first (SETUP.md, step 4); until then this only says how to record them later.
record_fillers() {
  if [ -n "${PHONECALL_ENGINE:-}" ] || [ -f "${PHONECALL_VOICE:-voices/my-voice/voice.json}" ]; then
    .venv/bin/python server/make_fillers.py --language "$LANGUAGE" $FORCE \
      || { echo "Recording the phrases failed (see above). Fix the voice (SETUP.md step 4), then: sh install.sh --fillers"; return 1; }
  else
    echo "The voice is not set up yet: after SETUP.md step 4, run  sh install.sh --fillers"
  fi
}

if [ "$MODE" = "fillers" ]; then
  choose_language
  [ -x .venv/bin/python ] || { echo "Run sh install.sh first"; exit 1; }
  record_fillers
  exit 0
fi

if [ "$MODE" = "remote" ]; then
  PYTHON=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    found=$(command -v "$candidate") || continue
    "$found" -c 'import sys; sys.exit(sys.version_info < (3, 10))' && { PYTHON="$found"; break; }
  done
  [ -n "$PYTHON" ] || { echo "Needs Python 3.10 or newer"; exit 1; }
  printf '#!/bin/sh\nexec "%s" "%s/server/phonecall.py" "$@"\n' "$PYTHON" "$ROOT" > ~/.local/bin/phonecall
  chmod +x ~/.local/bin/phonecall
  cp skills/phonecall/SKILL.md ~/.claude/skills/phonecall/SKILL.md
  echo "Done. Still to do on this machine (see SETUP.md, step 10):"
  echo "  ~/.config/phonecall/host         the Mac's Tailscale name or IP"
  echo "  ~/.config/phonecall/local-token  the same token as the Mac's ~/.config/phonecall/local-token (chmod 600)"
  exit 0
fi

command -v brew >/dev/null || { echo "Needs Homebrew (https://brew.sh)"; exit 1; }
choose_language
command -v uv >/dev/null || brew install uv
command -v ffmpeg >/dev/null || brew install ffmpeg
# Voice engine (Whisper + Qwen3-TTS on MLX): repository root .venv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-engine.txt
# Call server (aiohttp + WebRTC): its own venv
uv venv --python 3.12 server/.venv
uv pip install --python server/.venv/bin/python -r server/requirements.txt
mkdir -p ~/.config/phonecall/logs
printf '#!/bin/sh\nexec "%s/server/.venv/bin/python" "%s/server/phonecall.py" "$@"\n' "$ROOT" "$ROOT" > ~/.local/bin/phonecall
chmod +x ~/.local/bin/phonecall
cp skills/phonecall/SKILL.md ~/.claude/skills/phonecall/SKILL.md
[ -e ~/.config/phonecall/config.json ] || { cp examples/config.json.example ~/.config/phonecall/config.json; chmod 600 ~/.config/phonecall/config.json; }
record_fillers
echo "Done. Still to do: models and voice, config.json, Firebase, the service and the app (see SETUP.md)."
