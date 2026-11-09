#!/bin/sh
# Installs agent-phone-call (the `phonecall` command). Run from the repository root.
#
#   sh install.sh            the Mac that runs the call server (Apple Silicon, Homebrew)
#   sh install.sh --remote   another machine whose agent should call (a VPS, Linux or macOS):
#                            only the `phonecall` command and the skill, nothing else
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
mkdir -p ~/.local/bin ~/.config/phonecall ~/.claude/skills/phonecall
chmod 700 ~/.config/phonecall

if [ "$1" = "--remote" ]; then
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
echo "Done. Still to do: models and voice, config.json, Firebase, the service and the app (see SETUP.md)."
