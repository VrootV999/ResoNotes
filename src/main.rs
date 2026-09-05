mod config;
mod ctl;
mod dialog;
mod ipc;
mod python;
mod recorder;
mod tray;

use crate::config::Config;
use crate::python::PythonSupervisor;
use crate::recorder::Recorder;
use crate::tray::Tray;
use global_hotkey::hotkey::{Code, HotKey, Modifiers};
use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use log::LevelFilter;
use notify_rust::Notification;
use std::path::PathBuf;
use std::sync::mpsc::{channel, Sender};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RecMode {
    Standalone,
    Session,
}

impl RecMode {
    fn as_str(self) -> &'static str {
        match self {
            RecMode::Standalone => "standalone",
            RecMode::Session => "session",
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum Ev {
    Toggle, // start/stop recording (standalone, or the active session's take)
    Session, // (re)open/create session, or stop the current session
    OpenNotes,
    Quit,
}

/// What the currently active take is about (for stop-time naming).
#[derive(Debug, Clone)]
struct ActiveTake {
    mode: RecMode,
    safe: String,
}

/// An open session: takes in the same session share folder `<notes>/<safe>`
/// and combined notes `<notes>/<safe>/summary.md` + notes.md + topic files.
#[derive(Debug, Clone)]
struct SessionCtx {
    display: String,
    safe: String,
}

/// Offline queue entry kept in memory while the backend is unreachable.
#[derive(Debug, Clone)]
struct PendingTake {
    path: PathBuf,
    take_id: String,
    kind: &'static str,
    name: String,
}

struct App {
    cfg: Config,
    supervisor: PythonSupervisor,
    recorder: Recorder,
    status: crate::ctl::Status,
    tray: Option<Tray>,
    pending: Vec<PendingTake>,
    take_seq: u64,
    session: Option<SessionCtx>,
    active: Option<ActiveTake>,
}

fn notify(summary: &str, body: &str) {
    log::info!("notify: {summary}: {body}");
    let _ = Notification::new()
        .summary(summary)
        .body(body)
        .timeout(3000)
        .show();
}

fn stamp() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (y, mo, d, h, mi, s) = local_fields(secs);
    format!("{y:04}{mo:02}{d:02}-{h:02}{mi:02}{s:02}")
}

/// Local wall-clock breakdown of a unix timestamp.
fn local_fields(secs: i64) -> (i32, u32, u32, u32, u32, u32) {
    #[cfg(unix)]
    {
        use std::mem::zeroed;
        let t: libc::time_t = secs as libc::time_t;
        let mut tm: libc::tm = unsafe { zeroed() };
        unsafe {
            libc::localtime_r(&t, &mut tm);
        }
        (
            tm.tm_year + 1900,
            tm.tm_mon as u32 + 1,
            tm.tm_mday as u32,
            tm.tm_hour as u32,
            tm.tm_min as u32,
            tm.tm_sec as u32,
        )
    }
    #[cfg(not(unix))]
    {
        // UTC fallback
        let mut y = 1970i32;
        let mut rem = secs.div_euclid(86400);
        loop {
            let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
            let dias = if leap { 366 } else { 365 };
            if rem < dias {
                break;
            }
            rem -= dias;
            y += 1;
        }
        let dim = [
            31,
            if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 { 29 } else { 28 },
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ];
        let mut mo = 1u32;
        let mut d = 0u32;
        for (i, m) in dim.iter().enumerate() {
            if rem < *m {
                mo = i as u32 + 1;
                d = rem as u32 + 1;
                break;
            }
            rem -= *m;
        }
        let left = secs.rem_euclid(86400);
        (y, mo, d, (left / 3600) as u32, ((left % 3600) / 60) as u32, (left % 60) as u32)
    }
}

fn main() {
    env_logger::Builder::new()
        .filter(None, LevelFilter::Info)
        .init();

    let args: Vec<String> = std::env::args().collect();
    if args.len() > 1 {
        #[cfg(unix)]
        {
            match args[1].as_str() {
                "toggle" | "session" | "status" | "quit" => {
                    match crate::ctl::client(&args[1]) {
                        Ok(reply) => {
                            println!("{reply}");
                            std::process::exit(0)
                        }
                        Err(e) => {
                            eprintln!("{e}");
                            std::process::exit(1)
                        }
                    }
                }
                _ => {}
            }
        }
    }

    let cfg = Config::load();
    cfg.ensure_dirs();

    let status = crate::ctl::Status::new();
    let (tx, rx) = channel::<Ev>();

    #[cfg(unix)]
    let _ctl_thread = crate::ctl::spawn_server(&cfg.control_sock, tx.clone(), status.clone());

    let mut app = App::assemble(cfg, status, tx);

    let hotkeys = register_hotkey();

    let mut last_retry = Instant::now();
    loop {
        // global hotkey (X11/XWayland)
        while let Ok(ev) = GlobalHotKeyEvent::receiver().try_recv() {
            if ev.state != HotKeyState::Pressed {
                continue;
            }
            if let Some(hk) = &hotkeys {
                if hk.toggle_ids.contains(&ev.id) {
                    app.handle(Ev::Toggle);
                } else if ev.id == hk.session_id {
                    app.handle(Ev::Session);
                }
            }
        }
        while let Ok(ev) = rx.try_recv() {
            match ev {
                Ev::Quit => {
                    app.quit();
                    return;
                }
                _ => app.handle(ev),
            }
        }
        if last_retry.elapsed() >= Duration::from_secs(8) {
            last_retry = Instant::now();
            app.retry_pending();
        }
        std::thread::sleep(Duration::from_millis(120));
    }
}

impl App {
    fn tray_refresh(&mut self) {
        if let Some(t) = &mut self.tray {
            t.refresh(&self.status);
        }
    }

    fn assemble(cfg: Config, status: crate::ctl::Status, tx: Sender<Ev>) -> Self {
        let mut supervisor = PythonSupervisor::new(&cfg);
        let backend_up = supervisor.ensure_ready();
        status.set_backend(backend_up);
        if !backend_up {
            notify(
                "ResoNote",
                "Python backend unreachable. Run install.sh, then restart.",
            );
        }
        let recorder = Recorder::new();
        let tray = match Tray::build(tx, &status) {
            Ok(t) => Some(t),
            Err(e) => {
                log::warn!("{e} — continuing without tray (use CLI 'resonote toggle')");
                None
            }
        };
        Self {
            cfg,
            supervisor,
            recorder,
            status,
            tray,
            pending: Vec::new(),
            take_seq: 0,
            session: None,
            active: None,
        }
    }

    fn handle(&mut self, ev: Ev) {
        match ev {
            Ev::Toggle => self.toggle(),
            Ev::Session => self.session_action(),
            Ev::OpenNotes => self.open_notes(),
            Ev::Quit => self.quit(),
        }
        self.tray_refresh();
    }

    /// SUPER+S: toggle recording. Already recording -> stop and hand the take
    /// to the backend. Otherwise start a take: into the active session if we
    /// are inside one, else a standalone note (name asked first).
    fn toggle(&mut self) {
        if self.active.is_some() {
            self.stop_take(true);
            return;
        }

        // Starting a new take: ask for the standalone note name only when we
        // are not inside a session (a session take already has its name).
        let (display, safe, mode) = if let Some(s) = self.session.clone() {
            (s.display.clone(), s.safe.clone(), RecMode::Session)
        } else {
            let (d, sf) = match dialog::prompt_name(
                "ResoNote — Standalone note",
                "Save note as (blank = date & time):",
            ) {
                Some(n) => {
                    let safe = crate::config::safe_name(&n);
                    (n, safe)
                }
                None => {
                    let s = stamp();
                    (s.clone(), s)
                }
            };
            (d, sf, RecMode::Standalone)
        };

        match self.recorder.start(&self.cfg) {
            Ok(()) => {
                self.active = Some(ActiveTake { mode, safe });
                self.status.set_recording(true);
                self.status.set_mode(Some(mode.as_str()));
                notify(
                    "ResoNote",
                    &format!("Recording started — {} ({display})", mode.as_str()),
                );
            }
            Err(e) => {
                notify("ResoNote", &format!("Could not start mic: {e}"));
            }
        }
    }

    /// SUPER+SHIFT+J: session control. If recording, first stop the current
    /// take. If a session is open, close it (its recordings & notes stay).
    /// Otherwise ask whether to create a new session or load an old one.
    fn session_action(&mut self) {
        if self.active.is_some() {
            self.stop_take(self.session.is_some());
        }
        if let Some(s) = self.session.take() {
            self.status.set_recording(false);
            self.status.set_mode(None);
            notify(
                "Session stopped",
                &format!("'{}' is closed. Its recordings are in the notes folder.", s.display),
            );
            return;
        }
        let choices: Vec<String> = vec!["Create new session".into(), "Load existing session".into()];
        match dialog::picker(
            "ResoNote — Session",
            "New session or load an old one?",
            &choices,
        ) {
            Some(0) => {
                let (display, safe) = match dialog::prompt_name(
                    "ResoNote — New session",
                    "Name this session (blank = date & time):",
                ) {
                    Some(n) => (n.clone(), crate::config::safe_name(&n)),
                    None => {
                        let s = stamp();
                        (s.clone(), s)
                    }
                };
                self.session = Some(SessionCtx { display, safe });
                notify(
                    "Session started",
                    "Recording now saves into this session. Press SUPER+S to start.",
                );
            }
            Some(1) => {
                if let Some((display, safe)) = self.choose_session() {
                    let d = display.clone();
                    self.session = Some(SessionCtx { display, safe });
                    notify(
                        "Session loaded",
                        &format!("Recording now continues the session '{d}'."),
                    );
                }
            }
            _ => {}
        }
    }

    /// List past sessions (folders under the notes dir) and let the user pick.
    fn choose_session(&self) -> Option<(String, String)> {
        let mut folders: Vec<PathBuf> = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&self.cfg.notes_dir) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() && p.join("summary.md").exists() {
                    folders.push(p);
                }
            }
        }
        folders.sort_by_key(|p| std::fs::metadata(p).and_then(|m| m.modified()).unwrap_or(std::time::SystemTime::UNIX_EPOCH));
        folders.reverse();
        if folders.is_empty() {
            notify("ResoNote", "No past sessions found in the notes folder yet.");
            return None;
        }
        let names: Vec<String> = folders
            .iter()
            .map(|p| p.file_name().unwrap_or_default().to_string_lossy().into_owned())
            .collect();
        match dialog::picker("ResoNote — Load session", "Pick a session:", &names) {
            Some(i) if i < names.len() => {
                let safe = names[i].clone();
                Some((safe.clone(), safe))
            }
            _ => None,
        }
    }

    /// Save the active take, then submit it.
    fn stop_take(&mut self, submit: bool) {
        let Some(active) = self.active.take() else {
            self.status.set_recording(false);
            return;
        };
        let stem = format!("take-{}-{:03}", stamp(), self.take_seq);
        let dir = match active.mode {
            // Session recordings live inside the session folder (notes/<safe>)
            RecMode::Session => self.cfg.notes_dir.join(&active.safe),
            RecMode::Standalone => self.cfg.recordings_dir.clone(),
        };
        if let Err(e) = std::fs::create_dir_all(&dir) {
            log::warn!("could not create {}: {e}", dir.display());
        }
        match self.recorder.stop(&dir, &stem) {
            Some((path, dur)) => {
                self.take_seq += 1;
                self.status.set_recording(false);
                self.status.set_mode(None);
                notify(
                    "Recording saved",
                    &format!("{} ({dur:.0}s) — processing…", path.file_name().unwrap().to_string_lossy()),
                );
                if submit {
                    self.submit(path, stem, active.mode, active.safe);
                }
            }
            None => {
                self.status.set_recording(false);
                self.status.set_mode(None);
                notify("ResoNote", "Take was too short or mic failed.");
            }
        }
    }

    fn submit(&mut self, path: PathBuf, take_id: String, mode: RecMode, name: String) {
        let kind = mode.as_str();
        if self.supervisor.backend().health() {
            let _ = self.status.set_backend(true);
            if self.supervisor.backend().process_file(&path, &take_id, kind, &name) {
                return;
            }
        }
        self.status.set_backend(false);
        log::info!("queueing {} for offline retry", path.display());
        self.pending.push(PendingTake { path, take_id, kind, name });
        let _ = self
            .supervisor
            .backend()
            .say("Note is queued offline. It will sync when the network returns.");
    }

    fn retry_pending(&mut self) {
        if self.pending.is_empty() {
            return;
        }
        if !self.supervisor.backend().health() {
            self.status.set_backend(false);
            return;
        }
        self.status.set_backend(true);
        let mut remaining = Vec::new();
        for t in self.pending.drain(..) {
            if !self
                .supervisor
                .backend()
                .process_file(&t.path, &t.take_id, t.kind, &t.name)
            {
                remaining.push(t);
            }
        }
        self.pending = remaining;
    }

    fn open_notes(&self) {
        let _ = std::process::Command::new("xdg-open")
            .arg(&self.cfg.notes_dir)
            .spawn();
    }

    fn quit(&mut self) {
        if self.active.is_some() {
            self.stop_take(false);
        }
        self.supervisor.shutdown();
        notify("ResoNote", "Goodbye.");
    }
}

struct Hotkeys {
    _manager: GlobalHotKeyManager,
    toggle_ids: Vec<u32>,
    session_id: u32,
}

fn register_hotkey() -> Option<Hotkeys> {
    let manager = GlobalHotKeyManager::new().ok()?;

    let mut toggle_ids = Vec::new();
    let mut session_id: Option<u32> = None;

    let toggle = HotKey::new(Some(Modifiers::SUPER), Code::KeyS);
    match manager.register(toggle) {
        Ok(()) => {
            log::info!("registered global hotkey SUPER+S (record)");
            toggle_ids.push(toggle.id());
        }
        Err(e) => log::warn!("could not register SUPER+S: {e}"),
    }
    let session = HotKey::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyJ);
    match manager.register(session) {
        Ok(()) => {
            log::info!("registered global hotkey SUPER+SHIFT+J (session)");
            session_id = Some(session.id());
        }
        Err(e) => log::warn!("could not register SUPER+SHIFT+J: {e}"),
    }

    if toggle_ids.is_empty() && session_id.is_none() {
        notify(
            "ResoNote",
            "Global hotkeys unavailable (X11/XWayland required). Use the tray icon or 'resonote toggle'.",
        );
        return None;
    }

    Some(Hotkeys {
        _manager: manager,
        toggle_ids,
        session_id: session_id.unwrap_or(0),
    })
}