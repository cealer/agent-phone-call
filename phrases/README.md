# Phrases: what a call says, per language

Each file here is one conversation language: `en.toml` (English, the default) and `es.toml` (Spanish).
It holds everything language-specific about a call:

| Key | What it is |
|---|---|
| `name` | The language's name in English; the agent is told "Speak {name}." |
| `whisper`, `whisper_prompt` | The language code Whisper transcribes and a vocabulary hint. |
| `trailing` | Words a sentence does not end on: after a pause on one of them the call waits for more. |
| `greeting`, `agent_unreachable`, `agent_offline`, `hermes_preamble` | Said live by the voice (not recorded). |
| `[fillers]` | Generic acknowledgements right after you stop talking ("Mm, okay."), ≤ 1.5 s each. |
| `[filler_categories.*]` | With Jev on, the acknowledgement that fits what you said: `question`, `order`, `greeting`, `goodbye`, `thanks`, `other`. |
| `[nods]` | Soft nods while you talk for a long time ("Uh-huh."), ≤ 0.6 s each. |
| `[mm]` | With Jev on, the neutral sound played first ("Mm."), ≤ 0.5 s each. |
| `[wait.*]` | «Still working» cues when the answer takes a while: a first one per kind (`question`, `order`, `other`), then `more`. |

The short phrases are **recorded on your Mac at install time**, with the voice you configured, by
`server/make_fillers.py` (which `install.sh` runs). No recorded audio is kept in this repository.

## Choose the language

```sh
sh install.sh --language es        # at install time (run from a terminal without it, it asks)
sh install.sh --fillers --language en   # later: switch language and record the phrases again
launchctl kickstart -k gui/$(id -u)/phonecall.server
```

The choice is saved in `~/.config/phonecall/language`; the `PHONECALL_LANGUAGE` environment variable
overrides it. Switching records the new language's clips and deletes the old ones. Set the voice's
`"language"` in `voices/my-voice/voice.json` to the same language, so Qwen3-TTS pronounces it right.

## Change a phrase

Edit a copy in `~/.config/phonecall/phrases/<code>.toml` (it wins over the repository's file and
survives `git pull`), then:

```sh
sh install.sh --fillers            # records only the clips whose phrase changed
```

Coming from an install older than this phrase system: `sh install.sh --fillers --force` records
everything again and clears the old clips it does not know.

## Add a language

1. Copy a file under the new language's code (ISO 639-1, as Whisper knows it):
   `cp phrases/en.toml ~/.config/phonecall/phrases/fr.toml` (or into `phrases/` to contribute it).
2. Translate every phrase. Set `name` (in English: `"French"`), `whisper` (`"fr"`), a `whisper_prompt`
   in the language, and `trailing` (its conjunctions, articles and prepositions).
3. Keep every table name as it is. The categories under `filler_categories` and `wait` are Jev's
   (`server/jev.py`): Jev reads what you said in any language and picks one of these fixed categories;
   the file only says which clips answer each one. A file with other categories is rejected.
4. Clip names (left of `=`) become file names: lowercase letters, digits and dashes. Keep the phrases
   short (the limits are in the table above): these play while the agent thinks.
5. Check and record: `sh install.sh --fillers --language fr` (a broken file stops here with the reason;
   `.venv/bin/python server/make_fillers.py --language fr --check` lists the clips without recording).
6. Set `"language"` in `voice.json` and restart the server.
