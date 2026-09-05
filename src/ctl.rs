use crate::Ev;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::thread;

/// Runtime status shared with the control socket.
#[derive(Debug, Default, Clone)]
pub struct Status {
    inner: Arc<Mutex<StatusInner>>,
}

#[derive(Debug, Default)]
struct StatusInner {
    pub recording: bool,
    pub backend_up: bool,
}

impl Status {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn set_recording(&self, v: bool) {
        self.inner.lock().unwrap().recording = v;
    }
    pub fn set_backend(&self, v: bool) {
        self.inner.lock().unwrap().backend_up = v;
    }
    pub fn is_recording(&self) -> bool {
        self.inner.lock().unwrap().recording
    }
    pub fn line(&self) -> String {
        let s = self.inner.lock().unwrap();
        format!(
            "recording={} backend={}",
            if s.recording { "yes" } else { "no" },
            if s.backend_up { "up" } else { "down" }
        )
    }
}

fn dispatch(line: &str, tx: &Sender<Ev>, status: &Status) -> String {
    match line {
        "toggle" => {
            let _ = tx.send(Ev::Toggle);
            "toggling".into()
        }
        "new_session" => {
            let _ = tx.send(Ev::NewSession);
            "start new session".into()
        }
        "status" => status.line(),
        "quit" => {
            let _ = tx.send(Ev::Quit);
            "quitting".into()
        }
        other => format!("unknown command: {other}"),
    }
}

/// Serve the unix control socket on a background thread (toggle/status/quit).
pub fn spawn_server(sock: &std::path::Path, tx: Sender<Ev>, status: Status) -> thread::JoinHandle<()> {
    let sock = sock.to_path_buf();
    thread::spawn(move || {
        // Remove a stale socket only if nothing is listening on it.
        if sock.exists() {
            if UnixStream::connect(&sock).is_err() {
                let _ = std::fs::remove_file(&sock);
            } else {
                log::warn!("another resnote instance holds {}", sock.display());
                return;
            }
        }
        let listener = match UnixListener::bind(&sock) {
            Ok(l) => l,
            Err(e) => {
                log::warn!("control socket bind failed: {e}");
                return;
            }
        };
        log::info!("control socket at {}", sock.display());
        for stream in listener.incoming() {
            match stream {
                Ok(s) => {
                    let tx = tx.clone();
                    let status = status.clone();
                    thread::spawn(move || {
                        let mut reader = BufReader::new(&s);
                        let mut line = String::new();
                        if reader.read_line(&mut line).is_err() {
                            return;
                        }
                        let reply = dispatch(line.trim(), &tx, &status);
                        let mut w = s;
                        let _ = w.write_all(reply.as_bytes());
                    });
                }
                Err(e) => log::debug!("control socket accept error: {e}"),
            }
        }
    })
}

pub fn client(cmd: &str) -> Result<String, String> {
    let home = dirs::home_dir().ok_or("no home dir")?;
    let sock = home.join(crate::config::APP_DIR).join("control.sock");
    let mut stream = UnixStream::connect(&sock).map_err(|e| format!("resonote not running ({e})"))?;
    stream
        .write_all(format!("{cmd}\n").as_bytes())
        .map_err(|e| e.to_string())?;
    let mut reader = BufReader::new(&stream);
    let mut reply = String::new();
    reader
        .read_line(&mut reply)
        .map_err(|e| e.to_string())?;
    Ok(reply.trim().to_string())
}