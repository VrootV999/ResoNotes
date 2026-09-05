# ResoNote — build & feature tracking

- [x] Two recording modes:
  - Standalone (`SUPER+J` / `resonote toggle`): one recording -> one note, never merged
  - Session (`SUPER+SHIFT+J` / `resonote session`): takes group under a named session,
    stored in `recordings/<session>/` and combined into one `~/VoiceNotes/<session>.md`
- [x] Name prompt before recording (zenity/kdialog/rofi; `RESONOTE_NAME` env override;
      Enter = date-time) for both session and note names
- [x] Desktop notification when recording starts (mode + name) and when a take is saved
- [x] Global shortcuts in Rust (two hotkeys, X11/XWayland) with CLI fallback
- [x] Lightweight system tray UI in Rust (standalone + session + open notes + quit)
- [x] Low-latency microphone capture loop in Rust (16-bit PCM WAV, mono, 16 kHz)
- [x] Python backend: Groq (whisper) transcription + Gemini note generation, TTS
- [x] Per-session accumulators (`state/sessions/<name>.json`) so a session note re-renders
      from exactly its own takes (standalone notes are fully isolated)
- [x] Offline caching: takes + `.meta` sidecar move to `~/.config/resonote/queue/`,
      the worker retries when the network returns
- [x] Action-item extraction, keyword bookmarks ("note to self"), speaker labelling
- [x] Install script, config/example, README,TODO

## How to run

```bash
./install.sh                      # builds Rust, creates ~/.config/resonote/venv, writes config
~/.config/resonote/config.json    # add free GROQ + GEMINI keys
~/.local/bin/resonote             # run the app
```

Keys: `SUPER+J` standalone · `SUPER+SHIFT+J` session · tray · `resonote toggle|session|status|quit`