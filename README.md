# ResoNote
This is a lightweight, privacy-conscious desktop/system-wide companion that sits quietly in your tray or navbar. With a single shortcut click, it records your environment, pushes the audio to a free LLM to clean it up into gorgeous notes, and can even whisper back context using live Text-to-Speech (TTS) while it runs.
No corporate bloat. No expensive subscriptions. Just your voice, turned into action.

## What It Actually Does

- One-Touch Capture: Hit a global keyboard shortcut, click your navbar, or press a physical button. Boom—it’s recording.
- Smart LLM Summaries: Once you stop recording, it zips the audio off to a free LLM backend to strip out the "ums," "ahs," and ramblings, leaving you with structured notes, bullet points, or action items.
- Live Background TTS: Need a quick recap or verbal confirmation while recording or processing? The built-in TTS engine handles background audio playback without skipping a beat.
- Action Item & Task Extraction: Automatically parses your ramblings to pull out to-dos, deadlines, or follow-ups.
- Speaker Diarization: Knows who is speaking (great for multi-person chats or interviews).
- Keyword Triggers: Say "Note to self..." mid-sentence to automatically bookmark a timestamp or flag a priority item.
- Local-First / Offline Queueing: No internet? No problem. It caches recordings locally and syncs them up once you're back online

## Stack (100% free, $0 budget)

| Component  | Provider                                   | Notes                                   |
|------------|--------------------------------------------|-----------------------------------------|
| Speech→Text| Groq `whisper-large-v3-turbo` (free tier)  | One key from https://console.groq.com    |
| Notes LLM  | Google `gemini-2.0-flash` (free tier)      | One key from https://aistudio.google.com |
| TTS        | `edge-tts` (Microsoft neural voices)       | No key required                          |

Nothing is self-hosted. Transcription, note generation, and voice synthesis all use free APIs. Audio is only ever uploaded to Groq/Google for the two processing steps — there is no analytics, no tracking, no account beyond your API keys.

## Layout

- **Rust** (`src/`) — system tray, global hotkey, microphone capture (cpal → 16 kHz mono WAV), launches the Python backend, and talks to it over local HTTP (127.0.0.1 only).
- **Python** (`python/`) — FastAPI backend: Groq transcription (auto-chunking for long takes), Gemini note/action-item generation, edge-tts playback, and the offline retry queue.

## Setup

Requires: Rust 1.75+, Python 3.10+, and a free player such as `mpv`, `ffplay`, or `mpg123` for TTS.

```bash
./install.sh
```

Then edit `~/.config/resonote/config.json` and add your two free API keys:

```jsonc
{ "groq_api_key": "gsk_...", "gemini_api_key": "AIza..." }
```

Run it:

```bash
resonote                      # tray app (record / stop / new session / open notes / quit)
resonote toggle               # toggle recording (scripts, keybinds)
resonote status               # "recording=no backend=up"
resonote new_session          # force a new note
resonote quit
```

Notes are written as `~/VoiceNotes/<session>_session.md` (session name is the time of the first take). Takes within `session_merge_minutes` (default 10) coalesce into the same note.

## Keyboard shortcut

The built-in hotkey is **SUPER + SHIFT + J** and needs an X11/XWayland session (the `global-hotkey` crate is X11-only). On Wayland compositors you can bind the same key to the control command instead — for Hyprland, add to `~/.config/hypr/hyprland.conf`:

```
bind = SUPER, SHIFT, J, exec, ~/.local/bin/resonote toggle
```

Works on Sway/i3 and any other compositor/DE with its own keybind system too.

## Configuration (`~/.config/resonote/config.json`)

| Key                     | Default                | Meaning                                      |
|-------------------------|------------------------|----------------------------------------------|
| `port`                  | `42123`                | localhost backend port                       |
| `groq_api_key`          | `""`                   | Groq API key (STT)                           |
| `gemini_api_key`        | `""`                   | Google AI Studio key (notes)                 |
| `groq_stt_model`        | `whisper-large-v3-turbo` | Whisper model                             |
| `gemini_model`          | `gemini-2.0-flash`     | Notes model                                  |
| `voice`                 | `en-US-AriaNeural`     | edge-tts voice                               |
| `language`              | `en`                   | transcript language                          |
| `chunk_minutes`         | `8`                    | long takes are split into chunks this long   |
| `notes_dir`             | `~/VoiceNotes`         | where markdown notes land                    |
| `session_merge_minutes` | `10`                   | gap before the next take becomes a new note  |
| `speaker_diarization`   | `true`                 | ask the LLM to label speakers when clear     |
| `keyword_flag`          | `true`                 | surface "note to self / bookmark / flag"     |
| `tts_feedback`          | `true`                 | spoken + desktop confirmations               |
| `player`                | auto                   | `mpv` / `ffplay` / `mpg123` (or `none`)      |
| `python`                | auto                   | executable for the backend                   |
| `backend_dir`           | `./python`             | where `server.py` lives                      |

Everything else the app needs lives next to the config file under `~/.config/resonote/`: the Python venv (`venv/`), hot takes (`recordings/`), retry queue (`queue/`), processed-take markers (`state/done/`), log output (`logs/`), and the control socket (`control.sock`).

## Offline behaviour

Takes are always written to `~/.config/resonote/recordings/` first. If the backend or providers are unreachable the wav moves to `~/.config/resonote/queue/` and a worker retries every ~20 s. Processed takes get a marker in `~/.config/resonote/state/done/`, so nothing is uploaded or summarized twice. A take whose transcription succeeds but note generation fails is still saved as a raw-transcript note, so you never lose the audio text.

## Limitations

- **Global hotkey** needs X11/XWayland; on pure Wayland use a keybind (see above) or the tray.
- **Tray icon** needs a StatusNotifier host (Waybar, nwg-panel, KDE, GNOME…). Without one, use the CLI.
- **Speaker diarization** is best-effort: Whisper (via Groq) has no built-in diarization, so ResoNote asks Gemini to attribute speakers only when the audio text makes it unambiguous.
- **TTS quality** depends on the network (edge-tts streams from Microsoft's servers).

## License

MIT