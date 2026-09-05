use crate::config::Config;
use crate::ipc::Backend;
use std::fs::File;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

/// Manages the Python backend subprocess lifecycle.
pub struct PythonSupervisor {
    child: Option<Child>,
    backend: Backend,
    config: Config,
}

impl PythonSupervisor {
    pub fn new(config: &Config) -> Self {
        Self {
            child: None,
            backend: Backend::new(config.port),
            config: config.clone(),
        }
    }

    pub fn backend(&self) -> &Backend {
        &self.backend
    }

    /// Returns true once the backend answers on /api/health, launching it if needed.
    pub fn ensure_ready(&mut self) -> bool {
        if self.backend.health() {
            return true;
        }
        if self.child.is_none() {
            self.launch();
        }
        let started = Instant::now();
        while started.elapsed() < Duration::from_secs(20) {
            if self.backend.health() {
                return true;
            }
            // backend exited early?
            if let Some(child) = &mut self.child {
                if let Some(status) = child.try_wait().ok().flatten() {
                    log::error!("python backend exited with {status}");
                    self.child = None;
                    return false;
                }
            }
            thread::sleep(Duration::from_millis(300));
        }
        log::error!("python backend did not become healthy in time");
        false
    }

    fn launch(&mut self) {
        let py = self
            .config
            .python_bin
            .clone()
            .unwrap_or_else(|| "python3".to_string());
        let py_dir = self.config.backend_dir.clone();
        std::fs::create_dir_all(&self.config.log_dir).ok();
        let log_file = self
            .config
            .log_dir
            .join("python.log")
            .to_string_lossy()
            .into_owned();
        let stdout = File::create(&log_file)
            .map(Stdio::from)
            .unwrap_or_else(|_| Stdio::null());
        let stderr = File::create(&log_file)
            .map(Stdio::from)
            .unwrap_or_else(|_| Stdio::null());

        log::info!("launching python backend: {py} server.py (cwd {})", py_dir.display());
        match Command::new(&py)
            .arg("server.py")
            .current_dir(&py_dir)
            .env("RESONOTE_PORT", self.config.port.to_string())
            .env("RESONOTE_HOME", &self.config.data_dir)
            .env("RESONOTE_LOG", log_file)
            .env("PYTHONUNBUFFERED", "1")
            .stdout(stdout)
            .stderr(stderr)
            .spawn()
        {
            Ok(child) => {
                self.child = Some(child);
            }
            Err(e) => {
                log::error!("failed to spawn python backend ({py}): {e}");
                log::error!("  did you run install.sh? backend dir: {}", py_dir.display());
            }
        }
    }

    pub fn shutdown(&mut self) {
        self.backend.quit();
        thread::sleep(Duration::from_millis(500));
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}