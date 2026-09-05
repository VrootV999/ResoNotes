mod config;
mod ctl;
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
use std::sync::mpsc::{channel, Sender};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy)]
enum Ev {
    Toggle,
    NewSession,
    OpenNotes,
    Quit,
}

struct App {
    cfg: Config,
    supervisor: PythonSupervisor,
    recorder: Recorder,
    status: crate::ctl::Status,
    tray: Option<Tray>,
    pending: Vec<(std::path::PathBuf, String)>,
    take_seq: u64,
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
                "toggle" | "status" | "new_session" | "quit" => {
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

    let _manager = register_hotkey();
    if app.cfg.tts_feedback {
        app.supervisor.backend().say("ResoNote is ready");
    }

    let mut last_retry = Instant::now();
    loop {
        // global hotkey (X11/XWayland)
        while let Ok(ev) = GlobalHotKeyEvent::receiver().try_recv() {
            if ev.state == HotKeyState::Pressed {
                app.handle(Ev::Toggle);
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
        }
    }

    fn handle(&mut self, ev: Ev) {
        match ev {
            Ev::Toggle => self.toggle(),
            Ev::NewSession => self.new_session(),
            Ev::OpenNotes => self.open_notes(),
            Ev::Quit => self.quit(),
        }
        self.tray_refresh();
    }

    fn toggle(&mut self) {
        if self.recorder.is_recording() {
            let stem = format!("take-{}-{:03}", stamp(), self.take_seq);
            match self.recorder.stop(&self.cfg.recordings_dir, &stem) {
                Some((path, dur)) => {
                    self.take_seq += 1;
                    self.status.set_recording(false);
                    self.tray_refresh();
                    notify(
                        "Recording saved",
                        &format!("{} ({:.0}s) — processing…", path.file_name().unwrap().to_string_lossy(), dur),
                    );
                    if self.cfg.tts_feedback {
                        self.supervisor.backend().say("Recording finished");
                    }
                    self.submit(path, stem);
                }
                None => {
                    self.status.set_recording(false);
                    notify("ResoNote", "Take was too short or mic failed.");
                }
            }
        } else {
            match self.recorder.start(&self.cfg) {
                Ok(()) => {
                    self.status.set_recording(true);
                    if self.cfg.tts_feedback {
                        self.supervisor.backend().say("Recording started");
                    }
                }
                Err(e) => notify("ResoNote", &format!("Could not start mic: {e}")),
            }
        }
    }

    fn submit(&mut self, path: std::path::PathBuf, take_id: String) {
        if self.supervisor.backend().health() {
            let _ = self.status.set_backend(true);
            if self.supervisor.backend().process_file(&path, &take_id) {
                return;
            }
        }
        self.status.set_backend(false);
        log::info!("queueing {} for offline retry", path.display());
        self.pending.push((path, take_id));
        let _ = self.supervisor.backend().say("Note is queued offline. It will sync when the network returns.");
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
        for (path, take_id) in self.pending.drain(..) {
            if !self.supervisor.backend().process_file(&path, &take_id) {
                remaining.push((path, take_id));
            }
        }
        self.pending = remaining;
    }

    fn new_session(&mut self) {
        if self.recorder.is_recording() {
            self.toggle();
        }
        let _ = self.supervisor.backend().new_session();
        notify("ResoNote", "New note session started.");
    }

    fn open_notes(&self) {
        let _ = std::process::Command::new("xdg-open")
            .arg(&self.cfg.notes_dir)
            .spawn();
    }

    fn quit(&mut self) {
        if self.recorder.is_recording() {
            let stem = format!("take-{}-{:03}", stamp(), self.take_seq);
            let _ = self.recorder.stop(&self.cfg.recordings_dir, &stem);
        }
        self.supervisor.shutdown();
        notify("ResoNote", "Goodbye.");
    }
}

fn register_hotkey() -> Option<GlobalHotKeyManager> {
    let manager = GlobalHotKeyManager::new().ok()?;
    let hotkey = HotKey::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyJ);
    match manager.register(hotkey) {
        Ok(()) => {
            log::info!("registered global hotkey SUPER+SHIFT+J");
            Some(manager)
        }
        Err(e) => {
            log::warn!("could not register global hotkey (XWayland/X11 required): {e}");
            notify(
                "ResoNote",
                "Global hotkey unavailable on this session. Use the tray icon, or 'resonote toggle'.",
            );
            None
        }
    }
}