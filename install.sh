#!/bin/sh
# Installs agent-phone-call (the `llamada` command). Run from the repository root.
#
#   sh install.sh            the Mac that runs the call server (Apple Silicon, Homebrew)
#   sh install.sh --remote   another machine whose agent should call (a VPS, Linux or macOS):
#                            only the `llamada` command and the skill, nothing else
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
mkdir -p ~/.local/bin ~/.config/llamada ~/.claude/skills/llamar
chmod 700 ~/.config/llamada

if [ "$1" = "--remote" ]; then
  PYTHON=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    found=$(command -v "$candidate") || continue
    "$found" -c 'import sys; sys.exit(sys.version_info < (3, 10))' && { PYTHON="$found"; break; }
  done
  [ -n "$PYTHON" ] || { echo "Needs Python 3.10 or newer"; exit 1; }
  printf '#!/bin/sh\nexec "%s" "%s/llamada/server/llamada.py" "$@"\n' "$PYTHON" "$ROOT" > ~/.local/bin/llamada
  chmod +x ~/.local/bin/llamada
  cp skills/llamar/SKILL.md ~/.claude/skills/llamar/SKILL.md
  echo "Done. Still to do on this machine (see README, 'Let a remote agent call you'):"
  echo "  ~/.config/llamada/host         the Mac's Tailscale name or IP"
  echo "  ~/.config/llamada/local-token  the same token as the Mac's ~/.config/llamada/local-token (chmod 600)"
  exit 0
fi

command -v brew >/dev/null || { echo "Needs Homebrew (https://brew.sh)"; exit 1; }
command -v uv >/dev/null || brew install uv
command -v ffmpeg >/dev/null || brew install ffmpeg
# Voice engine (Whisper + Qwen3-TTS on MLX): repository root .venv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-engine.txt
# Call server (aiohttp + WebRTC): its own venv
uv venv --python 3.12 llamada/server/.venv
uv pip install --python llamada/server/.venv/bin/python -r llamada/server/requirements.txt
mkdir -p ~/.config/llamada/logs
printf '#!/bin/sh\nexec "%s/llamada/server/.venv/bin/python" "%s/llamada/server/llamada.py" "$@"\n' "$ROOT" "$ROOT" > ~/.local/bin/llamada
chmod +x ~/.local/bin/llamada
cp skills/llamar/SKILL.md ~/.claude/skills/llamar/SKILL.md
[ -e ~/.config/llamada/config.json ] || { cp examples/config.json.example ~/.config/llamada/config.json; chmod 600 ~/.config/llamada/config.json; }
echo "Done. Still to do: models and voice, config.json, Firebase, the service and the app (see README)."
