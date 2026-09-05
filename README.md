# ResoNote
This is a lightweight, privacy-conscious desktop/system-wide companion that sits quietly in your tray or navbar. With a single shortcut click, it records your environment, pushes the audio to a free LLM to clean it up into gorgeous notes while it runs, and keeps re-writing those notes in a session folder as you go.
No corporate bloat. No expensive subscriptions. Just your voice, turned into action.

## What It Actually Does

- **Manual recording, always on your terms** — `SUPER+S` toggles the mic on and off. Nothing auto-stops, nothing auto-merges; you are in control of every take.
- **Two recording modes**
  - **Standalone** (`SUPER+S` with no session open): one recording → one note. Each note is processed on its own and never mixes in other recordings.
  - **Session** (`SUPER+SHIFT+J`): creates or loads a named session. Every take you record while it's open lands in `<notes>/<session>/` and **re-writes the session notes after each take** — `summary.md`, `notes.md`, plus one `.md` per big topic (e.g. `Unit-1.md`, `SQLI.md`, `XSS.md`, `SSRF.md`) — so the page grows as the lesson/meeting unfolds. Press `SUPER+SHIFT+J` again to stop the session.
- **Name it on the fly**: before a recording you're asked for a name (session or note). Press Enter to use the current date & time.
- One-Touch Capture: Hit a global keyboard shortcut, click your navbar, or press a physical button. Boom—it's recording.
- Smart LLM Summaries: Once you stop a take, it zips the audio off to a free LLM backend to strip out the "ums," "ahs," and ramblings, leaving you with structured notes, bullet points, or action items.
- Rewrites as it goes: inside a session every new take rebuilds the notes files immediately, so the summary and topic notes stay current without waiting for the session to end.
- Action Item & Task Extraction: Automatically parses your ramblings to pull out to-dos, deadlines, or follow-ups.
- Speaker Diarization: Knows who is speaking (great for multi-person chats or interviews).
- Keyword Triggers: Say "Note to self..." mid-sentence to automatically bookmark a timestamp or flag a priority item.
- Quiet confirmations: recording start/stop use desktop notifications only (no spoken interruptions).
- Local-First / Offline Queueing: No internet? No problem. It caches recordings locally and syncs them up once you're back online.

## Stack (100% free, $0 budget)

| Component  | Provider                                   | Notes                                   |
|------------|--------------------------------------------|-----------------------------------------|
| Speech→Text| Groq `whisper-large-v3-turbo` (free tier)  | One key from https://console.groq.com    |
| Notes LLM  | Groq `qwen/qwen3.8-27b` (free tier)        | Same key as STT; Gemini is the fallback |
| Notes LLM (fallback) | Google `gemini-3.6-flash` (free tier) | Key from https://aistudio.google.com   |
| TTS        | `edge-tts` (Microsoft neural voices)       | No key required                          |

Nothing is self-hosted. Transcription, note generation, and voice synthesis all use free APIs. Audio is only ever uploaded to Groq/Google for the two processing steps — there is no analytics, no tracking, no account beyond your API keys.

## Layout

- **Rust** (`src/`) — system tray, two global hotkeys, an in-app name dialog (zenity/kdialog/rofi, or `RESONOTE_NAME`), microphone capture (cpal → 16 kHz mono WAV), launches the Python backend, and talks to it over local HTTP (127.0.0.1 only).
- **Python** (`python/`) — FastAPI backend: Groq transcription (auto-chunking for long takes), Groq/Gemini note generation with Groq-first fallback, per-session accumulators that rewrite `summary.md`/`notes.md`/topic files live, edge-tts playback, and the offline retry queue.

## Setup

Requires: Rust 1.75+, Python 3.10+, and a free player such as `mpv`, `ffplay`, or `mpg123` for TTS.

```bash
./install.sh
```

Then edit `~/.config/resonote/config.json` and add your free API keys (the Groq key alone is enough — it powers both STT and notes):

```jsonc
{ "groq_api_key": "gsk_...", "gemini_api_key": "AIza..." }
```

Run it:

```bash
resonote                # tray app (record / session / open notes / quit)
resonote toggle         # toggle recording (scripts, keybinds)
resonote session        # create / load / stop a session (scripts, keybinds)
resonote status         # "recording=no backend=up"
resonote quit
```

## Recording modes & naming

- **Standalone** — a popup asks "Save note as (blank = date & time):". The take is saved to `recordings/<name>.wav` (or the date-time stamp) and the note to `<notes>/<name>.md`. Every standalone take is processed in isolation — previous recordings are never pulled in. Press `SUPER+S` again to stop and submit it.
- **Session** — `SUPER+SHIFT+J` opens a menu: *Create new session* or *Load existing session* (past sessions = folders under the notes dir that have a `summary.md`). Creating asks "Name this session (blank = date & time):". While a session is open, `SUPER+S` records straight into it; every take re-renders these files inside `<notes>/<session>/`:
  - `summary.md` — session meta (takes, duration) + overall summary + action items
  - `notes.md` — the full accumulated markdown notes
  - `<topic>.md` — one file per notable topic when the LLM decides to break it out
  - plus the raw `.wav` takes themselves
  Press `SUPER+SHIFT+J` again to stop the session (its recordings and notes stay where they are).

Name dialogs need `zenity`, `kdialog`, `rofi`, or `resonote` launched with `RESONOTE_NAME` / `RESONOTE_PICK` set. The in-app hotkeys work over X11/XWayland; on Wayland use your compositor's keybinds (below).

## Keyboard shortcut

Two built-in hotkeys (X11/XWayland only):

- **SUPER + S** → toggle recording (standalone, or into the open session)
- **SUPER + SHIFT + J** → session control: create new / load existing / stop the open session

On Wayland compositors bind the commands instead — for Hyprland, add to `~/.config/hypr/hyprland.conf`:

```
bind = SUPER, S, exec, ~/.local/bin/resonote toggle
bind = SUPER, SHIFT, J, exec, ~/.local/bin/resonote session
```

Works on Sway/i3 and any other compositor/DE with its own keybind system too.

## Configuration (`~/.config/resonote/config.json`)

| Key                     | Default                | Meaning                                      |
|-------------------------|------------------------|----------------------------------------------|
| `port`                  | `42123`                | localhost backend port                       |
| `groq_api_key`          | `""`                   | Groq API key (STT + notes)                   |
| `gemini_api_key`        | `""`                   | Google AI Studio key (notes fallback)        |
| `llm_provider`          | `groq`                 | notes provider order: `groq` (Groq first, Gemini fallback) or `gemini` |
| `groq_stt_model`        | `whisper-large-v3-turbo` | Whisper (STT) model                        |
| `llm_model`             | `qwen/qwen3.8-27b`     | Groq chat model used for note generation     |
| `gemini_model`          | `gemini-3.6-flash`     | Gemini model used for notes fallback         |
| `voice`                 | `en-US-AriaNeural`     | edge-tts voice                               |
| `language`              | `en`                   | transcript language                          |
| `chunk_minutes`         | `8`                    | long takes are split into chunks this long   |
| `notes_dir`             | `~/VoiceNotes`         | where markdown notes (and session folders) land |
| `speaker_diarization`   | `true`                 | ask the LLM to label speakers when clear     |
| `keyword_flag`          | `true`                 | surface "note to self / bookmark / flag"     |
| `tts_feedback`          | `true`                 | spoken confirmations for save/queue events (not for record start/stop) |
| `name_prompt`           | `auto`                 | `auto` / `zenity` / `kdialog` / `rofi` / `none` for the name dialog |
| `player`                | auto                   | `mpv` / `ffplay` / `mpg123` (or `none`)      |
| `python`                | auto                   | executable for the backend                   |
| `backend_dir`           | `./python`             | where `server.py` lives                      |

(`session_merge_minutes` from older versions is no longer used — sessions now only merge when you actively start one.)

Everything else the app needs lives next to the config file under `~/.config/resonote/`: the Python venv (`venv/`), standalone takes (`recordings/`), retry queue (`queue/`, with session subfolders), session state (`state/sessions/`), processed-take markers (`state/done/`), log output (`logs/`), and the control socket (`control.sock`).

## Offline behaviour

Standalone takes are written to `~/.config/resonote/recordings/` first and session takes into their `<notes>/<session>/` folder. If the backend or providers are unreachable the wav moves to `~/.config/resonote/queue/` (keeping its session folder and a small `.meta` sidecar with its name) and a worker retries every ~20 s. Processed takes get a marker in `~/.config/resonote/state/done/`, so nothing is uploaded or summarized twice. A take whose transcription succeeds but note generation fails is still saved as a minimal fallback note ("AI note generation is unavailable right now"), so you never silently lose a recording.

## Limitations

- **Global hotkeys** need X11/XWayland; on pure Wayland use the compositor keybinds (see above) or the tray.
- **Name dialogs** need `zenity`, `kdialog`, or `rofi` installed; without one the date-time name is used silently.
- **Tray icon** needs a StatusNotifier host (Waybar, nwg-panel, KDE, GNOME…). Without one, use the CLI.
- **Speaker diarization** is best-effort: Whisper (via Groq) has no built-in diarization, so ResoNote asks the notes LLM to attribute speakers only when the audio text makes it unambiguous.
- **TTS quality** depends on the network (edge-tts streams from Microsoft's servers).
- **Free-tier rate limits**: Groq's free tier can return `429 Too Many Requests` when hammered; takes are queued and retried automatically.

## License

MIT