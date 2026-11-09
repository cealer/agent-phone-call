# Set up agent-phone-call

This guide installs everything needed for an AI agent to phone you and for you to phone it:

```text
your agent -> phonecall CLI -> call server on your Mac -> (Tailscale) -> Agent Call app on your Android phone
```

It is written so that a coding agent such as **Claude Code can follow it on its own, from start to finish**.
Every step has exact commands and a check («you should see…»). Steps marked **👤 Human** need a person
(an account login, a tap on the phone, a file downloaded from a web console): the agent stops there, tells
the human exactly what to do, and goes on once the check passes.

> Paste this into Claude Code, from the cloned repository:
>
> ```text
> Follow SETUP.md to install agent-phone-call on this Mac. Run each step and its check. When a step
> is marked Human, stop, tell me exactly what to do, and wait for me. Never print the contents of
> tokens or key files.
> ```

Scope: the call server needs **macOS on Apple Silicon** (the default voice engine uses MLX). The app needs
**Android 8 or newer**. An optional remote agent machine (a VPS) can be **Linux or macOS**.

## What each part does

| Part | Where it runs | Job |
|---|---|---|
| Call server (`server/server.py`) | Mac | Rings the phone, carries the call (WebRTC), turns your voice into text and the agent's text into voice, hands your words to the agent. |
| Voice engine (`server/engine.py`) | Mac | Whisper (speech-to-text) and Qwen3-TTS (text-to-speech), run locally with MLX; loaded only around calls. |
| `phonecall` CLI + skill | Mac, and optionally a VPS | What the agent runs to call you and talk (`phonecall start / say / hangup / status`), and the skill that teaches it when and how. |
| Agent Call app (`app/`) | Android phone | Full-screen incoming calls, calling your agents, live subtitles, notices, decisions, widgets. |
| [Tailscale](https://tailscale.com) | Mac, phone, VPS | The private network the call travels on. Free Personal plan. |
| [Firebase Cloud Messaging](https://firebase.google.com/docs/cloud-messaging) | Google | Wakes the phone so it can ring. Free. |
| [Herdr](https://herdr.dev) | Mac and/or VPS | Optional but recommended: terminal workspaces for agents. With it, your words reach the agent as prompts, and you can call the agent from the app. |
| `remote/` scripts | Mac and VPS | Optional: let the Mac reach an agent on a VPS through Herdr over ssh, with a key that can do nothing else. |

## Where a human has to step in

1. Log in to Tailscale on the Mac, the phone and the VPS (step 2).
2. Provide a voice reference clip you have the right to use (step 4).
3. Create the Firebase project and download two files from its console (step 5).
4. Connect the phone by USB with USB debugging on, or copy the APK to it (step 8).
5. Open the app on the phone and grant notifications, microphone and full-screen calls (step 9).
6. Answer the test call, and call the agent from the app (step 12).

## 1. Requirements

### The Mac

- Apple Silicon (M1 or newer), macOS 14 or newer, **16 GB RAM recommended** (both speech models stay loaded
  during calls), about **8 GB of free disk** (models ~6 GB, Python environments, the Android build).
- [Homebrew](https://brew.sh). Check: `brew --version` prints a version.
- Git: `git --version`.
- Flutter (stable) with the Android toolchain, to build the app. If `flutter` is missing:

  ```sh
  brew install --cask flutter
  brew install --cask android-commandlinetools   # or install Android Studio, which brings the SDK
  brew install --cask temurin@17                  # a JDK for the Android build, if you have none
  flutter doctor --android-licenses               # 👤 Human: accept the licences (type y)
  ```

  Check: `flutter doctor` shows ✓ for **Flutter** and **Android toolchain** (Xcode, Chrome and Android Studio
  lines may stay ✗; they are not needed).
- `adb` to install the app over USB: `brew install --cask android-platform-tools`. Check: `adb version`.

### The phone

- Android 8 or newer, with the Tailscale app (step 2). About 60 MB free for the app.

### Optional: a VPS for a remote agent

- Linux or macOS, Python 3.10 or newer (`python3 --version`), Tailscale, and the agent you want to reach
  (for example a firstmate coordinator running in Herdr). Steps 10 and 11.

## 2. Private network: Tailscale

1. **👤 Human, Mac:** install Tailscale from <https://tailscale.com/download/mac> (or the Mac App Store), open
   it and log in. In its settings, install the command-line tool if it offers to.
2. **👤 Human, phone:** install Tailscale from Google Play and log in **with the same account**.
3. **👤 Human, VPS (optional):** `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`, then
   open the printed link and log in with the same account.

Check on the Mac:

```sh
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
"$TS" status            # you should see this Mac, the phone (and the VPS) listed
"$TS" ip -4             # you should see one address like 100.x.y.z: the Mac's Tailscale IP
```

Write that address down; this guide calls it `MAC_TS_IP`. Use the IP (not a MagicDNS name) in the steps
below unless you know MagicDNS works on the phone.

## 3. Get the code and install the server

```sh
git clone https://github.com/OWNER/agent-phone-call ~/agent-phone-call   # skip if you are already in a clone
cd ~/agent-phone-call
sh install.sh
```

(`OWNER` is the GitHub account that hosts the repository; this guide assumes the clone lives at
`~/agent-phone-call`. If yours is elsewhere, use its path wherever the guide says `~/agent-phone-call`.)

`install.sh` installs `uv` and `ffmpeg` with Homebrew if missing, creates two Python 3.12 environments
(`.venv` for the voice engine, `server/.venv` for the server), writes the `phonecall` command to
`~/.local/bin/phonecall`, copies the skill to `~/.claude/skills/phonecall/`, and puts a starting
`~/.config/phonecall/config.json` in place (mode 600).

Make sure `~/.local/bin` is on `PATH` (add the line to `~/.zshrc` if not):

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Check:

```sh
command -v phonecall                       # you should see ~/.local/bin/phonecall
ls ~/.claude/skills/phonecall/SKILL.md     # the skill is in place
phonecall status                           # you should see "call server not running": it is not started yet
```

## 4. Speech models and the voice

Download the models (about 1.6 GB and 4 GB; the engine itself runs offline afterwards):

```sh
cd ~/agent-phone-call
HF_HUB_OFFLINE=0 .venv/bin/hf download mlx-community/whisper-large-v3-turbo
HF_HUB_OFFLINE=0 .venv/bin/hf download mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16 \
  --revision a6eb4f68e4b056f1215157bb696209bc82a6db48
```

**👤 Human:** the voice is cloned from a short clip. Provide **5–15 seconds of clean speech in a voice you have
the right to use** (your own recording is the simplest: QuickTime Player → New Audio Recording), and its exact
transcript. Never clone a real person without their consent. Then:

```sh
mkdir -p voices/my-voice
cp voices/my-voice/voice.json.example voices/my-voice/voice.json
# put the clip at voices/my-voice/reference.wav (any format ffmpeg reads, converted to wav):
#   ffmpeg -i ~/Desktop/recording.m4a -ac 1 -ar 24000 voices/my-voice/reference.wav
# edit voices/my-voice/voice.json: "reference_text" = the exact transcript,
#   "language" = the clip's language for Qwen3-TTS ("English", "Spanish", ...)
```

Pick the conversation language now: `en` (default) or `es` (see README, «Language»). If it is not English,
export it for the commands below and set it again in step 7: `export PHONECALL_LANGUAGE=es`.

Check: the engine loads, speaks and transcribes.

```sh
cd ~/agent-phone-call
printf '{"id":1,"op":"tts","text":"Hello, this is a test of the voice.","out":"/tmp/phonecall-test.wav"}\n' \
  | .venv/bin/python server/engine.py
# you should see {"ok": true, "ready": true} and then {"ok": true, "out": "/tmp/phonecall-test.wav", "id": 1}
afplay /tmp/phonecall-test.wav    # 👤 Human: the voice should sound like the clip
```

Record the short acknowledgements played while the agent thinks, and turn on streamed speech:

```sh
cd ~/agent-phone-call/server && ../.venv/bin/python make_fillers.py && cd ..
touch ~/.config/phonecall/stream-tts
ls ~/.config/phonecall/fillers    # you should see wav files and folders such as question/, wait/, nod/
```

## 5. Firebase (what makes the phone ring)

**👤 Human, in <https://console.firebase.google.com>:**

1. Create a project (the free Spark plan is enough; Google Analytics can stay off).
2. Add an **Android app** with the package name **`com.example.agentphonecall`**. (To use another one, change
   `applicationId` and `namespace` in `app/android/app/build.gradle.kts` and the Kotlin package first.)
3. Download its **`google-services.json`** and save it as `~/agent-phone-call/app/android/app/google-services.json`.
4. Project settings → Service accounts → **Generate new private key**. Save the file as
   `~/.config/phonecall/service-account.json`.

Then protect the key and check both files without printing them:

```sh
chmod 600 ~/.config/phonecall/service-account.json
cd ~/agent-phone-call
python3 -c "import json;d=json.load(open('app/android/app/google-services.json'));print('google-services ok:', d['client'][0]['client_info']['android_client_info']['package_name'])"
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.config/phonecall/service-account.json')));print('service account ok:', d['type'])"
# you should see "google-services ok: com.example.agentphonecall" and "service account ok: service_account"
git status --short app/android/app/google-services.json   # you should see nothing: git ignores it
```

## 6. Configuration

Generate the phone token and fill in `~/.config/phonecall/config.json` (the token is written straight to the
file, never printed):

```sh
MAC_TS_IP=100.x.y.z    # the address from step 2
python3 - "$MAC_TS_IP" <<'EOF'
import json, os, secrets, sys
path = os.path.expanduser("~/.config/phonecall/config.json")
config = json.load(open(path))
if config["phone_token"].startswith("REPLACE"):
    config["phone_token"] = secrets.token_hex(32)
config["phone_host"] = sys.argv[1]
config["agent_host"] = sys.argv[1]     # remove this key if no agent on another machine will call
config["service_account"] = "~/.config/phonecall/service-account.json"
json.dump(config, open(path, "w"), indent=2, ensure_ascii=False)
print("config.json updated")
EOF
chmod 600 ~/.config/phonecall/config.json
```

If `agent_host` stays, also create the token agents on other machines will use:

```sh
openssl rand -hex 32 > ~/.config/phonecall/local-token && chmod 600 ~/.config/phonecall/local-token
```

**Contacts** are the agents you can call from the app. Edit the `contacts` list in `config.json`:

- `id`: a short id (`firstmate` is special: it also receives the text channel and the «Talk» widget).
- `name`, `subtitle`: what the app shows.
- `agent`: the agent's Herdr id. Inside that agent's Herdr pane, run `echo $HERDR_PANE_ID` to get it.
- Optional: `greeting` (what it says when you call), `inbox` + `herdr_bin` for an agent on a VPS (step 11).

Delete the example contacts you will not use. Check:

```sh
python3 -c "import json,os;c=json.load(open(os.path.expanduser('~/.config/phonecall/config.json')));print('host', c['phone_host'], '| token set', len(c['phone_token'])==64, '|', len(c.get('contacts',[])), 'contacts')"
# you should see your Tailscale IP, "token set True" and the number of contacts
```

## 7. Run the server as a service

```sh
cd ~/agent-phone-call
sed -e "s#/PATH/TO/REPO#$PWD#g" -e "s#YOUR_HOME#$HOME#g" -e "s#YOUR_NAME#Alex#" \
  examples/phonecall.server.plist.example > ~/Library/LaunchAgents/phonecall.server.plist
```

Replace `Alex` with the name the agent should call you, and set `PHONECALL_LANGUAGE` in the plist if you
chose a language other than English. Then start it:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/phonecall.server.plist
sleep 3
phonecall status
# you should see {"phone_registered": false, "call": null}: the server runs; the phone comes in step 9
tail -n 5 ~/.config/phonecall/logs/server.log   # you should see "listening on 127.0.0.1:8770" and the phone port
```

After any change to `config.json` or the plist: `launchctl kickstart -k gui/$(id -u)/phonecall.server`.
Only one Mac should run the server for a given phone.

## 8. Build and install the Android app

The server URL and the phone token are compiled into the app:

```sh
cd ~/agent-phone-call/app
flutter pub get
TOKEN=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/phonecall/config.json')))['phone_token'])")
flutter build apk --release --dart-define=SERVER_URL=http://$MAC_TS_IP:8771 --dart-define=PHONE_TOKEN=$TOKEN
unset TOKEN
ls -lh build/app/outputs/flutter-apk/app-release.apk    # you should see the APK (tens of MB)
```

**👤 Human:** on the phone, enable Developer options and **USB debugging**, connect it by USB and accept the
prompt. Then:

```sh
adb devices          # you should see your phone listed as "device"
adb install -r build/app/outputs/flutter-apk/app-release.apk     # you should see "Success"
```

(Without USB: copy the APK to the phone and open it; Android asks to allow installing unknown apps.)
The APK carries the phone token: do not share it. The Flutter build leaves a few GB in `app/build/`; once
installed, `flutter clean` frees them.

## 9. First launch on the phone

**👤 Human, on the phone:** make sure Tailscale is connected, open **Agent Call** and accept every permission
it asks for: notifications, microphone, and «Full-screen calls» (tap **Fix** on any row that is not green).
«No battery restriction» is optional but recommended.

You should see the home screen say **«Ready: your agent can call you»**, with every «Phone status» row green
and your contacts listed. Check on the Mac:

```sh
phonecall status     # you should see "phone_registered": true
```

## 10. Optional: an agent on another machine calls you (VPS → Mac)

On the Mac, `agent_host` and `local-token` must exist (step 6) and the server restarted. On the VPS:

```sh
git clone https://github.com/OWNER/agent-phone-call ~/agent-phone-call && cd ~/agent-phone-call
sh install.sh --remote        # only the phonecall command (Python 3.10+, no dependencies) and the skill
echo "MAC_TS_IP" > ~/.config/phonecall/host    # the Mac's Tailscale IP from step 2
```

**👤 Human (or the agent, over Tailscale):** copy the Mac's `~/.config/phonecall/local-token` to the same path on
the VPS without pasting it anywhere, for example from the Mac:
`scp ~/.config/phonecall/local-token USER@VPS_TAILSCALE_IP:.config/phonecall/local-token`. Then on the VPS:

```sh
chmod 600 ~/.config/phonecall/local-token
phonecall status     # you should see the same JSON as on the Mac ("phone_registered": true)
```

With a `host` file the CLI talks to `http://<host>:8770` with `Authorization: Bearer <local-token>`
(`PHONECALL_HOST`, `PHONECALL_TOKEN` and `PHONECALL_PORT` override it).

## 11. Optional: your words reach an agent on the VPS (Mac → VPS, through Herdr)

To call the VPS agent from the app, and for your words to reach it during a call it started from Herdr, the Mac
runs `herdr` (or `fm-send`, for a firstmate reached through its inbox) on the VPS over ssh, with a key that
can run nothing else.

On the Mac:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_phonecall -N "" -C phonecall
cat ~/.ssh/id_ed25519_phonecall.pub      # a public key: safe to copy
```

On the VPS (Herdr installed and the agent running in it):

```sh
cd ~/agent-phone-call
install -m 755 remote/phonecall-ssh-gate ~/.local/bin/phonecall-ssh-gate
# add ONE line to ~/.ssh/authorized_keys (absolute path, the Mac's public key at the end):
#   command="/home/USER/.local/bin/phonecall-ssh-gate",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... phonecall
```

Back on the Mac, install the two wrappers, fill in `VPS_USER`, `VPS_HOST` (the VPS's Tailscale IP or name)
and `VPS_PORT`, and accept the VPS host key once:

```sh
cd ~/agent-phone-call
cp remote/herdr-vps.example ~/.local/bin/herdr-vps && cp remote/fm-send-vps.example ~/.local/bin/fm-send-vps
chmod +x ~/.local/bin/herdr-vps ~/.local/bin/fm-send-vps
# edit the placeholders at the top of both files, then:
ssh -i ~/.ssh/id_ed25519_phonecall VPS_USER@VPS_HOST true   # accept the host key; then it prints "command not allowed": no shell
```

In `config.json`, the remote agent's contact gets `"herdr_bin": "~/.local/bin/herdr-vps"`, `"agent"` = its
`HERDR_PANE_ID` on the VPS, and, if it is a firstmate reached through its inbox, `"inbox": "<fm-send target>"`.
Restart the server (`launchctl kickstart -k gui/$(id -u)/phonecall.server`). Check:

```sh
~/.local/bin/herdr-vps agent get THE_AGENT_ID   # you should see JSON describing the agent (its status)
ssh -i ~/.ssh/id_ed25519_phonecall VPS_USER@VPS_HOST ls   # you should see "command not allowed" (exit 126)
```

The app now shows that contact as Available / Working instead of Offline.

## 12. End-to-end test

**The agent calls you.** On the Mac (or the VPS):

```sh
phonecall start "Hi, this is a test call from your agent. Can you hear me well?"
```

**👤 Human:** the phone rings full-screen (lock it first to see the lock-screen call). Answer and say
something like «Yes, loud and clear».

- In turn mode (outside Herdr) the command prints what you said and exits with code 0.
- Inside a Herdr pane it prints that you answered; your words reach that pane as
  `[phonecall] USER said: …`.

Then end it (a goodbye is required):

```sh
phonecall hangup "Perfect, thanks. Talk soon."
echo $?        # 0
```

**You call the agent.** Make sure the agent runs in a Herdr pane and is one of your contacts.

**👤 Human:** in the app, tap the agent's green call button. You hear its greeting; say «Hello, are you
there?». In the agent's pane a prompt arrives that starts with `[phonecall] Alex called you on the phone`
and ends with `USER said: Hello, are you there?`. The agent answers with `phonecall say "…"` (the skill
tells it to) and you hear the answer, with subtitles on screen. Say «Bye» and hang up; the pane receives
`[phonecall] Alex hung up the call.`

You should see in the log, for each call, a `call … ended:` line with the reason (never what was said):

```sh
grep "ended:" ~/.config/phonecall/logs/server.log | tail -n 2
```

## 13. Optional extras

| Extra | How |
|---|---|
| Soft nods while you talk at length | `touch ~/.config/phonecall/nod-on` (add `nod-speaker` to allow them on the loudspeaker). |
| Jev: fitting acknowledgements, «uh-huh» does not interrupt | Your typesafe.ai key in `~/.config/phonecall/jev-token` (`chmod 600`), then `touch ~/.config/phonecall/jev-on`. |
| «Fleet» widget (quotas and backlog) | Run `server/.venv/bin/python server/fleet_snapshot.py` every 15 minutes (launchd); see its docstring. |
| Another speech engine (local or cloud) | README, «Use your own model, or an existing one». |
| Home-screen widgets | On the phone, long-press the home screen → Widgets → Agent Call: Decisions, Fleet, Talk. |

## Updating

```sh
cd ~/agent-phone-call && git pull --ff-only && sh install.sh
launchctl kickstart -k gui/$(id -u)/phonecall.server
```

Rebuild and reinstall the app (step 8) when `app/` changed, and re-run `make_fillers.py --force` when the
voice or the language changed.

## Troubleshooting

| Symptom | Check |
|---|---|
| `phonecall status` says the server is not running | `tail -n 30 ~/.config/phonecall/logs/server.log`; `launchctl print gui/$(id -u)/phonecall.server \| head`. |
| The app says it cannot reach the Mac | Tailscale connected on both? `SERVER_URL` built with the Mac's Tailscale IP and port 8771? `phone_host` in `config.json` is that same IP? |
| `phone_registered` stays false | Open the app once with Tailscale on; the server log should show `phone registered`. |
| The phone does not ring | Notifications and «Full-screen calls» allowed (green in the app)? `service_account` path right? `phonecall start` prints `Error: push failed: …` with the reason. |
| `push failed: FCM 404/403` | `google-services.json` and the service account must come from the **same** Firebase project. |
| The call connects but there is no voice | Step 4's engine check; the log shows `voice engine` errors. Models downloaded with the exact revision? |
| You cannot interrupt the agent on speakerphone | Normal on some phones; use the earpiece or headphones. |
| A VPS contact shows Offline | Step 11's checks; `herdr_bin` and `agent` in that contact; the agent is running in Herdr on the VPS. |

## Uninstall

```sh
launchctl bootout gui/$(id -u)/phonecall.server; rm ~/Library/LaunchAgents/phonecall.server.plist
rm ~/.local/bin/phonecall ~/.local/bin/herdr-vps ~/.local/bin/fm-send-vps 2>/dev/null
rm -r ~/.claude/skills/phonecall ~/.config/phonecall ~/agent-phone-call
```

Uninstall Agent Call on the phone, and delete the Firebase project if you created it only for this.

## Security boundary

A call gives your voice direct access to an agent that can run commands with your user account's
permissions. Keep the server inside Tailscale (never forward its ports), keep tokens and key files at mode 600
and out of chats and git, restrict the tailnet with Tailscale ACLs, and keep the skill's rule that the agent
repeats back anything important or destructive and waits for a yes. More in the README's «Security».
