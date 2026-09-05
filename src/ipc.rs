use std::path::Path;
use std::time::Duration;
use ureq::{Agent, AgentBuilder};

/// Minimal HTTP client for talking to the local Python backend.
pub struct Backend {
    agent: Agent,
    base: String,
}

impl Backend {
    pub fn new(port: u16) -> Self {
        let agent = AgentBuilder::new()
            .timeout_connect(Duration::from_secs(3))
            .timeout_read(Duration::from_secs(30))
            .timeout_write(Duration::from_secs(30))
            .build();
        Self {
            agent,
            base: format!("http://127.0.0.1:{port}"),
        }
    }

    pub fn health(&self) -> bool {
        match self.agent.get(&format!("{}/api/health", self.base)).call() {
            Ok(r) => r.status() == 200,
            Err(_) => false,
        }
    }

    pub fn say(&self, text: &str) -> bool {
        self.agent
            .post(&format!("{}/api/say", self.base))
            .send_json(serde_json::json!({ "text": text }))
            .is_ok()
    }

    pub fn new_session(&self) -> bool {
        self.agent
            .post(&format!("{}/api/new_session", self.base))
            .send_json(serde_json::json!({}))
            .is_ok()
    }

    pub fn process_file(&self, path: &Path, take_id: &str) -> bool {
        log::info!("submit {} as {take_id}", path.display());
        match self.agent.post(&format!("{}/api/process_file", self.base)).send_json(
            serde_json::json!({
                "path": path.to_string_lossy().to_string(),
                "take_id": take_id,
            }),
        ) {
            Ok(r) => {
                if r.status() == 200 {
                    log::info!("backend accepted {take_id}");
                    true
                } else {
                    log::warn!("backend rejected {take_id}: status {}", r.status());
                    false
                }
            }
            Err(e) => {
                log::warn!("could not reach backend to submit {take_id}: {e}");
                false
            }
        }
    }

    pub fn quit(&self) {
        let _ = self
            .agent
            .post(&format!("{}/api/quit", self.base))
            .send_json(serde_json::json!({}));
    }
}