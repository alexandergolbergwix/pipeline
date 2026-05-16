Generate Hebrew text-to-speech audio for the Bar-Ilan PhD pipeline presentation.

Use the existing script; do not rewrite the TTS workflow.

## Gemini TTS

Run from the repo root:

```bash
python3 docs/presentations/generate_hebrew_speaker_audio.py --engine gemini --voice Kore --parallel 4
```

If `API_KEY` is not set, the script prompts for the Gemini API key with hidden
terminal input. Never print, echo, or store the key.

Use `--parallel 4` by default. If Gemini rate-limits, rerun with `--parallel 2`
or `--parallel 3`. If the user explicitly wants faster generation, try
`--parallel 8` and warn that rate limits are possible.

Expected output:

```text
docs/presentations/audio/bar-ilan-phd-pipeline-speaker-notes-he-gemini.wav
```

## Local fallback

If Gemini fails, especially because Hebrew is not supported by the current TTS
model, offer the macOS Hebrew voice fallback:

```bash
python3 docs/presentations/generate_hebrew_speaker_audio.py --engine local
ffmpeg -y -i docs/presentations/audio/bar-ilan-phd-pipeline-speaker-notes-he-local.wav \
  -codec:a libmp3lame -b:a 128k \
  docs/presentations/audio/bar-ilan-phd-pipeline-speaker-notes-he-local.mp3
```

The local path may require permission to access macOS speech services.

## Validation

Check duration and size:

```bash
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 \
  docs/presentations/audio/bar-ilan-phd-pipeline-speaker-notes-he-gemini.wav
```

The script should report `Slides: 16`. If it reports many more chunks, the slide
splitting logic regressed.
