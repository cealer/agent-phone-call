# agent-phone-call: phone calls between you and your AI agent

The command and the code folder are named `llamada` (Spanish for "call").

Your coding agent (Claude Code, a firstmate coordinator, any agent that can run a shell command)
**calls your Android phone and talks with you by voice**, and you can **call it back** and keep talking,
for as long as you like. Speech recognition and the voice run **on your own Mac**; the call travels
over your **private network**.

> **What it costs: only the plan your agent already uses (for example a $100/month Claude
> subscription). Nothing else is billed.**
> Speech-to-text and text-to-speech run locally, so you can talk for hours without interruptions
> and without paying per minute, per token of audio or per call. There is no phone number to buy and no
> telephony provider (no Twilio, no SIP trunk): the call goes through the app in this repository.

## Use cases

**1. Your agent calls you when it needs you.** It is in the middle of a task and needs a decision,
an approval or a fact only you have, and you are away from the computer. Your phone rings
full-screen (even locked); the agent explains, you answer by voice, it hangs up and carries on.

**2. You call your agent and talk with it.** Open the app, tap the agent (for example your main
firstmate coordinator) and talk: ask for status, give instructions, think out loud. You can
interrupt it at any moment, the way you would interrupt a person.

## What it costs, honestly

| Piece | Needed? | Cost |
|---|---|---|
| Your agent's plan (e.g. Claude subscription) | yes | what you already pay; calls use it like any other prompt |
| Speech-to-text: Whisper large-v3-turbo on your Mac (MLX) | yes (or your own, see below) | free, local |
| Text-to-speech: Qwen3-TTS 1.7B on your Mac (MLX) | yes (or your own, see below) | free, local |
| Tailscale (private network between phone, Mac and VPS) | yes | free Personal plan; the author uses only that and it is enough |
| Firebase Cloud Messaging (wakes the phone to ring) | yes | free (Spark plan); FCM itself has no charge |
| Jev by typesafe.ai (instant, fitting acknowledgements) | **optional, off by default** | your own key; check typesafe.ai for its pricing |
| Hermes (hermes-agent) for the «Hablar» widget | optional | whatever that agent's model costs you |
| A cloud speech API instead of the local models | optional | billed by that provider; a local server costs nothing |

Everything optional can be switched off (and is off until you turn it on). The only exception to
"nothing else is billed" is if you choose a paid option yourself.

## Why a custom Flutter app

A phone call from an agent usually means a telephony provider: buy a number, pay per minute,
send your audio through a third party. This project avoids all of that. The Android app in
[`llamada/app`](llamada/app) receives a silent push (FCM) that makes it ring like a real
incoming call (full screen, even when locked), and the audio flows over **WebRTC directly
between the phone and your Mac, inside your Tailscale network**. So the only running cost stays
your agent's subscription, and your voice never goes to a call provider.

## How it works

```
 Android phone (app)                       Your Mac (Apple Silicon)                      VPS (optional)
 ┌────────────────────┐   WebRTC audio    ┌─────────────────────────────┐   ssh (herdr / ┌────────────────┐
 │ incoming call UI   │◄─────────────────►│ llamada/server/server.py    │   fm-send)     │ your main agent │
 │ contacts, widgets  │   HTTP + WS on    │  ├ phone API   :8771        │──────────────► │ (firstmate)    │
 │                    │   :8771 (token)   │  ├ local API   127.0.0.1:8770│               │                │
 └────────▲───────────┘                   │  ├ agent API   <tailscale>:8770 (Bearer) ◄───│ `llamada` CLI  │
          │ FCM data push                 │  └ voice engine (subprocess) │   HTTP         └────────────────┘
          └──────── Firebase ◄────────────│     engine.py: Whisper + Qwen3-TTS (MLX)
                                          │ `llamada` CLI + skill for the local agent
                                          └─────────────────────────────┘
```

- **`llamada` CLI** ([`llamada/server/llamada.py`](llamada/server/llamada.py)): what the agent runs:
  `llamada iniciar "…"`, `llamada decir "…"`, `llamada colgar "…"`, `llamada estado`.
  It calls the server's `/start`, `/say`, `/end` and `/status`.
- **Skill** ([`skills/llamar/SKILL.md`](skills/llamar/SKILL.md)): teaches a Claude Code agent when and
  how to call (only when blocked on you, how to speak, what the exit codes mean).
- **Server** ([`llamada/server/server.py`](llamada/server/server.py)): rings the phone through FCM,
  runs the WebRTC call (aiortc), detects when you speak (Silero VAD), lets you interrupt (barge-in),
  plays short recorded acknowledgements while the agent thinks, and hands your words to the agent.
- **Voice engine** ([`llamada/server/engine.py`](llamada/server/engine.py)): a subprocess that keeps
  the speech models loaded only while calls happen (and 10 minutes after), then frees the memory.
- **App** ([`llamada/app`](llamada/app)): Flutter, Android. Incoming call screen, contacts you can
  call, and optional home-screen widgets (pending decisions, fleet status, talk by text).

Two ways the agent hears you:

- **Turn mode** (any agent): `llamada decir "…"` blocks until you answer and prints your words.
- **Herdr mode** (the agent runs inside a Herdr pane, a terminal workspace for agents): your words arrive as
  prompts `[llamada] USUARIO dijo: …` and `llamada decir` returns at once. This is also what lets
  **you** call an agent: the server types your words into its pane (or sends them to its
  firstmate inbox with `fm-send`).

## Repository layout

```
README.md
LICENSE                          MIT
install.sh                       installer: the Mac (default) or a remote agent machine (--remote)
requirements-engine.txt          voice engine deps (MLX, mlx-whisper, mlx-audio), repository root .venv
examples/
  config.json.example            ~/.config/llamada/config.json
  llamada.server.plist.example   launchd service for the server
remote/
  herdr-vps.example              Mac → VPS: run herdr there over a restricted ssh key
  fm-send-vps.example            Mac → VPS: run fm-send there over the same key
  llamada-ssh-gate               VPS: forced command of that key (only herdr / fm-send)
skills/llamar/SKILL.md           the skill for Claude Code
voices/
  README.md                      how to make the voice
  my-voice/voice.json.example
llamada/
  server/                        call server, CLI, voice engines, tests
    server.py  llamada.py  engine.py  engine_openai.py  vad.py  push.py  jev.py
    herdr_agent.py  make_fillers.py  flota_snapshot.py  models/silero_vad.onnx
    test_server.py  test_engine_openai.py  requirements.txt
  app/                           Flutter Android app
```

## Installation

You need: a Mac with Apple Silicon (16 GB RAM recommended: both models stay loaded in memory
during calls), [Homebrew](https://brew.sh), an Android phone (Android 8+), a free
[Tailscale](https://tailscale.com) account, a free [Firebase](https://firebase.google.com) project,
and [Flutter](https://docs.flutter.dev/get-started/install) (stable) with the Android SDK to build the app.

### 1. Private network

Install Tailscale on the Mac, on the phone and (if you use one) on the VPS, all in the same tailnet.
Note the Mac's Tailscale IP (`tailscale ip -4`, a `100.x.y.z` address) or its MagicDNS name.

### 2. Server and CLI on the Mac

```sh
git clone https://github.com/Cesargonzales1/agent-phone-call ~/agent-phone-call && cd ~/agent-phone-call
sh install.sh
```

This creates two Python 3.12 environments (`.venv` for the voice engine, `llamada/server/.venv` for the
server), installs `ffmpeg`, writes `~/.local/bin/llamada`, copies the skill to
`~/.claude/skills/llamar/` and puts a starting `~/.config/llamada/config.json` in place.

### 3. Speech models and voice

```sh
# speech-to-text (about 1.6 GB)
HF_HUB_OFFLINE=0 .venv/bin/hf download mlx-community/whisper-large-v3-turbo
# text-to-speech (about 4 GB), pinned revision
HF_HUB_OFFLINE=0 .venv/bin/hf download mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16 \
  --revision a6eb4f68e4b056f1215157bb696209bc82a6db48
```

Then create the voice: follow [`voices/README.md`](voices/README.md) (a `voice.json` and a short
reference clip you have the right to use). The engine runs offline (`HF_HUB_OFFLINE=1`): if a model is
missing it reports an error instead of downloading.

Optionally (without them the line is simply quiet while the agent thinks) record the short acknowledgements played while the agent thinks ("Mmm, ya.", "Déjame ver.",
the soft waiting signal…), in the same voice:

```sh
cd llamada/server && ../../.venv/bin/python make_fillers.py
```

### 4. Firebase (ringing the phone)

1. In the Firebase console create a project (the free Spark plan is enough) and add an **Android app**
   with the package name `com.example.agentphonecall` (or change `applicationId` and `namespace` in
   [`llamada/app/android/app/build.gradle.kts`](llamada/app/android/app/build.gradle.kts) first).
2. Download its `google-services.json` into `llamada/app/android/app/` (git ignores it).
3. Project settings → Service accounts → **Generate new private key**. Save it as
   `~/.config/llamada/service-account.json` (`chmod 600`). The server uses it to send the FCM
   pushes (HTTP v1 API).

### 5. Configuration

Edit `~/.config/llamada/config.json` (started from
[`examples/config.json.example`](examples/config.json.example)):

| Key | Meaning |
|---|---|
| `phone_token` | Long random secret shared with the app (`openssl rand -hex 32`). |
| `phone_host`, `phone_port` | Where the phone API listens: the Mac's **Tailscale IP**, port 8771. |
| `local_port` | Local API for the `llamada` CLI on this Mac (always `127.0.0.1`), 8770. |
| `agent_host` | Optional: the Mac's Tailscale IP, to let agents on other machines call (same port, Bearer token). |
| `service_account` | Path to the Firebase service-account key. |
| `contacts` | The agents the app can call: `id`, `name`, `subtitle`, `agent` (Herdr agent/pane id), optional `inbox` (send through `fm-send` instead of typing in the pane), optional `herdr_bin` (e.g. `~/.local/bin/herdr-vps` for an agent on the VPS), optional `greeting` (what it says when you call). A contact with `"id": "firstmate"` also receives the text channel (`/texto/enviar`, the «Hablar» widget). |

If you set `agent_host`, create the agent token: `openssl rand -hex 32 > ~/.config/llamada/local-token && chmod 600 ~/.config/llamada/local-token`.

Files in `~/.config/llamada/` (presence of a file switches a feature on, checked live, no restart):

| File | Effect |
|---|---|
| `stream-tts` | Stream the voice as it is generated (first sound in ~0.7 s instead of after the whole phrase). Recommended. |
| `asentir-on` | Soft nods ("ajá", "mm") while you talk for a long time (with headphones). `asentir-altavoz` allows them on speaker too. |
| `jev-on` + `jev-token` | Turn Jev on (see below) with your own typesafe.ai key. |
| `fillers/`, `greetings/` | Recorded acknowledgements and contact greetings (written by `make_fillers.py` and by the server). |
| `device.json` | Written by the server when the app registers (the phone's FCM token). |

Environment variables for the server (set them in the launchd plist):

| Variable | Default | Meaning |
|---|---|---|
| `LLAMADA_NOMBRE` | `Tu usuario` | Your name as the agent reads it: `[llamada] Ana te llamó por teléfono…` |
| `LLAMADA_CONFIG` | `~/.config/llamada` | Configuration folder. |
| `LLAMADA_ENGINE`, `LLAMADA_ENGINE_PYTHON` | `engine.py`, root `.venv` | Swap the voice engine (see below). |
| `LLAMADA_VOICE` | `voices/my-voice/voice.json` | The TTS voice. |
| `LLAMADA_WHISPER` | `mlx-community/whisper-large-v3-turbo` | Any MLX Whisper model. |
| `LLAMADA_WHISPER_LANGUAGE` | `es` | The language you speak. |
| `LLAMADA_WHISPER_PROMPT` | generic | Vocabulary hint for Whisper (names, project words). |
| `LLAMADA_WARM_SECONDS` | `600` | How long models stay loaded after a call or after the app opens. |
| `LLAMADA_FM_SEND` | `~/.local/bin/fm-send-vps` | Command used for contacts with an `inbox`. |
| `LLAMADA_FM_HOME`, `LLAMADA_HERMES*` | | Optional widget extras (firstmate notes, Hermes). |
| `LLAMADA_RECORD` | off | `1` saves the phone's audio to disk for debugging. Leave off. |

### 6. Build the Android app

```sh
cd llamada/app
flutter pub get
flutter build apk --release \
  --dart-define=SERVER_URL=http://YOUR_MAC_TAILSCALE_IP:8771 \
  --dart-define=PHONE_TOKEN=THE_SAME_phone_token
adb install build/app/outputs/flutter-apk/app-release.apk
```

The release build refuses to compile without both `--dart-define` values. On the phone, open the app
once: it registers with the server and asks for the permissions it needs (notifications, microphone,
full-screen calls). Its status screen shows whether the Mac is reachable. The app's text is in Spanish.

### 7. Run the server as a service

```sh
cp examples/llamada.server.plist.example ~/Library/LaunchAgents/llamada.server.plist
# edit /PATH/TO/REPO, YOUR_HOME and LLAMADA_NOMBRE in it
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/llamada.server.plist
llamada estado        # phone_registered should be true once the app opened
```

Log: `~/.config/llamada/logs/server.log`. Only one Mac should run the server for a given phone.

### 8. First call

```sh
llamada iniciar "Hola, soy tu agente. ¿Me escuchas bien?"
llamada colgar "Perfecto, hasta luego."
```

From Claude Code, the skill takes it from there: the agent calls you when it is blocked on you,
or when you ask it ("llámame cuando termines").

## Let a remote agent call you (and call it from the phone)

This is how a main agent on a VPS (for example a firstmate coordinator) reaches the Mac, and how the
Mac reaches it back. Everything travels inside Tailscale.

**VPS → Mac (the agent calls you).** On the VPS:

```sh
git clone https://github.com/Cesargonzales1/agent-phone-call ~/agent-phone-call && cd ~/agent-phone-call
sh install.sh --remote        # only the `llamada` command (Python 3.10+, no deps) and the skill
echo "YOUR_MAC_TAILSCALE_NAME_OR_IP" > ~/.config/llamada/host
# copy the Mac's ~/.config/llamada/local-token here, privately (scp over Tailscale), then:
chmod 600 ~/.config/llamada/local-token
llamada estado
```

With a `host` file the CLI talks to `http://<host>:8770` with `Authorization: Bearer <local-token>`
(`LLAMADA_HOST` / `LLAMADA_TOKEN` / `LLAMADA_PORT` override it). The Mac must have `agent_host` set.

**Mac → VPS (your words reach the agent).** When the remote agent runs in Herdr, the server sends
your words to its pane (or its firstmate inbox) over ssh, with a key that can run nothing else:

1. On the Mac: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_llamada -N ""`.
2. On the VPS: install [`remote/llamada-ssh-gate`](remote/llamada-ssh-gate) as
   `~/.local/bin/llamada-ssh-gate` and add the public key to `~/.ssh/authorized_keys` with the forced
   command shown at the top of that file (`command="…",no-pty,no-port-forwarding,…`).
3. On the Mac: copy [`remote/herdr-vps.example`](remote/herdr-vps.example) and
   [`remote/fm-send-vps.example`](remote/fm-send-vps.example) to `~/.local/bin/herdr-vps` and
   `~/.local/bin/fm-send-vps`, fill in user, host and port, `chmod +x`, and accept the VPS host key
   once (`ssh -i ~/.ssh/id_ed25519_llamada user@host` adds it to `known_hosts`).
4. In `config.json`, the remote agent's contact gets `"herdr_bin": "~/.local/bin/herdr-vps"` and,
   if it is a firstmate reached through its inbox, `"inbox": "<fm-send target>"`. Its `agent` is the
   agent's Herdr pane id on the VPS (the `HERDR_PANE_ID` it runs with).

**Notifications from the remote agent (optional).** The app also shows FCM data messages the agent
sends itself, using its own copy of the service account and the phone token it reads from the
Mac's agent API (`GET /fcm-token`, Bearer):

- `{"type": "aviso", "id", "categoria": resumen|reunion|correo|decision|salud, "titulo", "cuerpo", "urgente": "0"|"1"}`:
  a notification.
- `{"type": "tareas", "generado": ISO-8601, "tareas": "<JSON: {marcha: [...], espera: [...], cola: [...]}>"}`:
  feeds the «Decisiones» and «Flota» widgets; `espera` items may be `{id, titulo, detalle, opciones}`
  and are answered from the widget through the server's `/hablar`.

## Speech: speech-to-text (STT) and text-to-speech (TTS)

### What it uses today

- **STT: Whisper large-v3-turbo** through [mlx-whisper](https://github.com/ml-explore/mlx-examples),
  locally on the Mac's GPU, Spanish by default. Phone audio is cut into turns by
  **Silero VAD** (ONNX, included, 2 MB) and each turn is transcribed as soon as you stop. Known Whisper
  hallucinations on silence ("Gracias por ver el video", "Subtítulos…") are dropped.
- **TTS: Qwen3-TTS 1.7B Base (bf16)** through [mlx-audio](https://github.com/Blaizzy/mlx-audio),
  locally, cloning the voice from your reference clip, streamed in ~1 s chunks, 48 kHz.
- **Acknowledgement clips** (`make_fillers.py`): short phrases recorded once in the same voice and
  played instantly ("Mmm, ya.", "Déjame ver.", a soft signal while the agent keeps working), so the
  line is never dead while the agent thinks.
- **Jev (optional, typesafe.ai, your own key)**: a very fast classifier asked within 0.4 s. It is how
  the call **does not go silent**: as soon as you stop talking it picks the acknowledgement that fits
  what you said (a question gets "Déjame ver", an order "Ok, dale", a thanks "De nada"), and when you
  speak over the agent it tells a real interruption ("espera, no") from a mere "ajá" so the agent keeps
  talking. Without Jev, a neutral acknowledgement plays and any speech over the agent interrupts it.
  On with `~/.config/llamada/jev-on` plus your key in `~/.config/llamada/jev-token`.
- **Hex / Parakeet (not included)**: the author also uses a separate macOS menu-bar helper that can
  transcribe with Parakeet through the Hex app; it is not needed for calls and is not in this repository.

### Use your own model, or an existing one

The server never imports a model. It talks to a **voice engine subprocess** over a tiny JSON-lines
protocol on stdin/stdout, documented at the top of [`engine.py`](llamada/server/engine.py):

```
{"id": 1, "op": "stt", "pcm": "/tmp/x.pcm"}            -> {"id": 1, "ok": true, "text": "..."}
{"id": 2, "op": "tts", "text": "Hola.", "out": "/tmp/y.wav"} -> {"id": 2, "ok": true, "out": "/tmp/y.wav"}
{"id": 3, "op": "tts_stream", "text": "Hola.", "out": "/tmp/z"} -> {"id": 3, "ok": true, "chunk": "/tmp/z.0.pcm"} … {"id": 3, "ok": true, "final": true}
```

(STT input: raw 16 kHz mono int16. TTS output: 48 kHz mono 16-bit, WAV or raw chunks.) There are
three levels of change:

1. **Another MLX Whisper or another cloned voice**: just settings: `LLAMADA_WHISPER`,
   `LLAMADA_WHISPER_LANGUAGE`, `LLAMADA_VOICE`.
2. **Any OpenAI-compatible speech API, local or cloud**: use the included
   [`engine_openai.py`](llamada/server/engine_openai.py) (standard library + ffmpeg):

   ```
   LLAMADA_ENGINE=/PATH/TO/REPO/llamada/server/engine_openai.py
   LLAMADA_ENGINE_PYTHON=/PATH/TO/REPO/llamada/server/.venv/bin/python
   LLAMADA_API_BASE=http://127.0.0.1:8000/v1     # a local server: speaches, LocalAI, Kokoro-FastAPI…
   LLAMADA_STT_MODEL=…  LLAMADA_TTS_MODEL=…  LLAMADA_TTS_VOICE=…  LLAMADA_API_KEY=… (if needed)
   ```

   Pointed at a local server it stays free; pointed at a cloud API (OpenAI, Groq…) each request is billed
   by that provider.
3. **Your own model**: write a script that speaks the protocol above (copy `engine_openai.py` and
   replace `transcribe()` and `_speech()`), and point `LLAMADA_ENGINE` / `LLAMADA_ENGINE_PYTHON` at it.
   The server needs nothing else.

## Security

The server is **not designed to be exposed to the internet**. Use it only inside a private network
such as Tailscale.

- Do **not** open ports on your router or set up port forwarding to the Mac, and do not bind
  `phone_host` / `agent_host` to `0.0.0.0` or a public address: use the Mac's Tailscale IP.
  The local API is always `127.0.0.1` only.
- The phone API requires `phone_token`; the agent API requires `Authorization: Bearer <local-token>`.
  Make both long and random (`openssl rand -hex 32`), keep them in files with `chmod 600`, and never
  commit or paste them anywhere. If one leaks, generate a new one, update `config.json` /
  `local-token` (and rebuild the app or copy the token to the VPS), and restart the server.
- Note that the phone token is compiled into the APK: do not share your APK.
- Restrict the tailnet with **Tailscale ACLs**: only your phone and your VPS need to reach the Mac on
  ports 8771 and 8770.
- The ssh key the Mac uses to reach the VPS is limited to `llamada-ssh-gate` (only `herdr` and `fm-send`,
  no shell, no forwarding). Keep `StrictHostKeyChecking=yes`.
- Keep `service-account.json`, `google-services.json`, `jev-token`, `device.json` and your voice
  recordings out of git (the `.gitignore` already does).
- A call gives your voice direct access to an agent that can run commands. The skill tells the agent to
  repeat back anything important or destructive and wait for a yes; keep that rule.

## HTTP API

| Port | Who | Routes |
|---|---|---|
| `127.0.0.1:8770` (local) | `llamada` CLI, local helpers | `POST /start /say /end`, `GET /status`, `POST /dictar /voz /jev /jev/calentar /texto/enviar`, `GET /texto/respuesta` |
| `<tailscale>:8770` (agent, Bearer) | agents on other machines | the same, plus `GET /fcm-token` |
| `<tailscale>:8771` (phone, token) | the app | `POST /register /decline /dial /dictar /voz /hablar`, `GET /contacts /call /ws /ping /flota /respuesta` |

`llamada` exit codes: `0` answered · `6` you interrupted the agent · `7` you spoke while it was thinking
(its text was not said) · `3` no answer or declined · `4` you hung up · `5` silence · `1` error.

## Tests

```sh
cd llamada/server
.venv/bin/python -m unittest test_engine_openai -v     # the alternative engine against a fake API
.venv/bin/python -m unittest test_server -v            # server unit tests
cd ../app && flutter analyze && flutter test
```

Some server tests synthesize real speech with macOS `say` (voice Paulina; `LLAMADA_TEST_VOICE` picks
another), and the realtime call tests need the installed engine and voice (step 3). The engine tests
need `ffmpeg`.

## Not included

- The models and any voice recording (download / record your own, see above).
- Your credentials: Firebase config, service account, tokens, contacts.
- The author's macOS menu-bar helper and Hermes voice integration (not needed for calls).
- The firstmate / Herdr tooling itself (`herdr`, `fm-send`): this repository only talks to it.

## License

[MIT](LICENSE) © 2026 Cesar Gonzales.
