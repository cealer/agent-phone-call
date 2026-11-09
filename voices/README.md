# Voices

The default engine (`server/engine.py`) speaks with **Qwen3-TTS 1.7B Base** (run locally with
MLX on Apple Silicon). The Base model clones a voice from a short reference clip, so a voice here is two files:

```
voices/my-voice/
  voice.json      model, pinned revision, reference transcript, language, seed, temperature
                  ("language" is the voice's language for Qwen3-TTS: "English", "Spanish", ...)
  reference.wav   5-15 s of clean speech in the voice you want
```

1. `cp voices/my-voice/voice.json.example voices/my-voice/voice.json`
2. Put a `reference.wav` next to it and write its **exact** transcript in `reference_text`.
3. Download the model once (about 4 GB; the engine itself runs offline):
   `HF_HUB_OFFLINE=0 .venv/bin/hf download mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16 --revision a6eb4f68e4b056f1215157bb696209bc82a6db48`

Another folder works too: set `PHONECALL_VOICE=/path/to/voice.json` for the server.

**Use a voice you have the right to use**: your own recording, a voice actor who agreed to it, or a
clip under a licence that allows voice cloning. Never clone a real person without their consent.
No reference clip ships with this repository, and `.gitignore` keeps `voices/*/*.wav` and
`voices/*/voice.json` out of git so a recording is not published by accident.

`seed` and `temperature` keep the voice consistent between phrases; once you like how it sounds,
run `sh install.sh --fillers` so the recorded short phrases use the same voice.
