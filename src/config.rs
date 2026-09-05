use serde::Deserialize;
use std::path::PathBuf;

pub const APP_DIR: &str = ".config/resonote";
pub const DEFAULT_PORT: u16 = 42123;
pub const DEFAULT_NOTE_DIR: &str = "~/VoiceNotes";
pub const DEFAULT_SAMPLE_RATE: u32 = 16000;

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
struct FileConfig {
    port: Option<u16>,
    notes_dir: Option<String>,
    sample_rate: Option<u32>,
    tts_feedback: Option<bool>,
    session_merge_minutes: Option<u64>,
    python: Option<String>,
    backend_dir: Option<String>,
    hotkey: Option<String>,
    name_prompt: Option<String>,
}

fn expand(p: &str, home: &str) -> PathBuf {
    if let Some(rest) = p.strip_prefix("~/") {
        PathBuf::from(format!("{home}/{rest}"))
    } else if p == "~" {
        PathBuf::from(home)
    } else {
        PathBuf::from(p)
    }
}

/// Filesystem-safe version of a user-chosen name ("Meeting / 2" -> "Meeting--2"
/// sanitized into "Meeting-2"); falls back to "untitled" when empty.
pub fn safe_name(input: &str) -> String {
    let mut out: String = input
        .trim()
        .chars()
        .map(|c| match c {
            'a'..='z' | 'A'..='Z' | '0'..='9' | '.' | '-' | '_' => c,
            _ => '-',
        })
        .collect();
    while out.contains("--") {
        out = out.replace("--", "-");
    }
    let out: String = out
        .trim_matches(['-', '.', '_'])
        .chars()
        .take(80)
        .collect();
    if out.is_empty() {
        "untitled".to_string()
    } else {
        out
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    pub data_dir: PathBuf,
    pub recordings_dir: PathBuf,
    pub queue_dir: PathBuf,
    pub state_dir: PathBuf,
    pub control_sock: PathBuf,
    pub log_dir: PathBuf,
    pub port: u16,
    pub notes_dir: PathBuf,
    pub sample_rate: u32,
    pub python_bin: Option<String>,
    pub backend_dir: PathBuf,
    pub name_prompt: Option<String>,
}

impl Config {
    pub fn load() -> Self {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("."));
        let home_s = home.to_string_lossy().into_owned();
        let data_dir = home.join(APP_DIR);

        let mut file = FileConfig::default();
        if let Ok(txt) = std::fs::read_to_string(data_dir.join("config.json")) {
            match serde_json::from_str::<FileConfig>(&txt) {
                Ok(c) => file = c,
                Err(e) => log::warn!("config.json invalid, using defaults: {e}"),
            }
        }

        let notes_dir = expand(
            file.notes_dir.as_deref().unwrap_or(DEFAULT_NOTE_DIR),
            &home_s,
        );
        let backend_dir = file
            .backend_dir
            .filter(|d| !d.trim().is_empty())
            .map(|d| expand(&d, &home_s))
            .unwrap_or_else(default_backend_dir);
        let python_bin = file
            .python
            .filter(|p| !p.trim().is_empty())
            .map(|p| expand(&p, &home_s).to_string_lossy().into_owned());

        Self {
            port: file.port.unwrap_or(DEFAULT_PORT),
            notes_dir,
            sample_rate: file.sample_rate.unwrap_or(DEFAULT_SAMPLE_RATE),
            python_bin,
            backend_dir,
            name_prompt: file.name_prompt.filter(|n| !n.trim().is_empty()),
            data_dir: data_dir.clone(),
            recordings_dir: data_dir.join("recordings"),
            queue_dir: data_dir.join("queue"),
            state_dir: data_dir.join("state"),
            log_dir: data_dir.join("logs"),
            control_sock: data_dir.join("control.sock"),
        }
    }

    pub fn ensure_dirs(&self) {
        for d in [
            &self.data_dir,
            &self.notes_dir,
            &self.recordings_dir,
            &self.queue_dir,
            &self.state_dir,
            &self.log_dir,
        ] {
            if let Err(e) = std::fs::create_dir_all(d) {
                log::warn!("could not create {}: {e}", d.display());
            }
        }
    }
}

/// Backend ("python/") is resolved relative to the crate manifest dir at
/// compile time; also probe next to the running executable.
fn default_backend_dir() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("python");
    if manifest.join("server.py").exists() {
        return manifest;
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            for cand in [parent.join("python"), parent.parent().unwrap_or(parent).join("python")] {
                if cand.join("server.py").exists() {
                    return cand;
                }
            }
        }
    }
    manifest
}