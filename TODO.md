# ResoNote — build & feature tracking

- [x] Implement global shortcut listener in Rust (SUPER + SHIFT + J) to toggle recording state
      (X11/XWayland via `global-hotkey`; Wayland fallback = `resonote toggle` CLI / tray)
- [x] Build a lightweight system tray UI in Rust with Record/Stop, New Session, Open Notes, and Quit controls
      (StatusNotifier host required; app keeps working without one)
- [x] Write the low-latency microphone capture loop in Rust to save raw audio (16-bit PCM WAV, mono, 16 kHz)
- [x] Set up the Python backend environment and install dependencies for transcription, LLMs, and TTS
- [x] Write Python script to process audio via free endpoints — Groq (whisper) + Gemini (notes)
- [x] Implement text-to-speech in Python for real-time background audio feedback (edge-tts, free, no key)
- [x] Establish IPC between Rust and Python over local HTTP (127.0.0.1, handled by the FastAPI backend)
- [x] Wire the Rust shortcut/tray action to trigger the Python processing pipeline asynchronously
- [x] Automatically save generated markdown notes to ~/VoiceNotes/ (session-aware merging in `python/notes.py`)
- [x] Add offline caching: takes are kept locally and the queue worker retries when the network returns
      (`python/pipeline.py`, `~/.resonote/queue/`)
- [x] Build action-item extraction, keyword bookmarks ("note to self"), speaker labelling, and custom
      prompt switching into the Python logic (see `python/llm.py`, `python/stt.py`)

## How to run

```bash
./install.sh                      # builds Rust, creates ~/.resonote/venv, writes config
~/.resonote/config.json           # add free GROQ + GEMINI keys
~/.local/bin/resonote             # run the app
```