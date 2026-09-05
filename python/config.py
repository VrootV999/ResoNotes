"""Configuration loader shared by the ResoNote Python backend.

Reads ~/.config/resonote/config.json (if present) and lets environment
variables override the two required API keys. Everything ships free / $0:
    - STT :  Groq  (whisper-large-v3-turbo, free tier)
    - LLM :  Google Gemini (gemini-3.6-flash, free tier)
    - TTS :  edge-tts (Microsoft neural voices, no key required)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("resonote")

HOME = Path.home()
APP_DIR = Path(os.environ.get("RESONOTE_HOME", str(HOME / ".config" / "resonote")))

DEFAULT_PORT = 42123
DEFAULT_NOTE_DIR = "~/VoiceNotes"


def _expand(p: str) -> Path:
    if p.startswith("~/"):
        return Path(str(HOME / p[2:]))
    if p == "~":
        return HOME
    return Path(p)


class Config:
    def __init__(self) -> None:
        self.data_dir = APP_DIR
        self.config_file = self.data_dir / "config.json"

        blob: dict = {}
        if self.config_file.exists():
            try:
                blob = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("config.json unreadable, using defaults: %s", e)
        if not isinstance(blob, dict):
            blob = {}

        self.port = int(os.environ.get("RESONOTE_PORT", blob.get("port", DEFAULT_PORT)))
        self.groq_api_key = os.environ.get("GROQ_API_KEY") or blob.get("groq_api_key") or ""
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY") or blob.get("gemini_api_key") or ""

        self.notes_dir = _expand(str(blob.get("notes_dir", DEFAULT_NOTE_DIR)))
        self.recordings_dir = self.data_dir / "recordings"
        self.queue_dir = self.data_dir / "queue"
        self.state_dir = self.data_dir / "state"
        self.done_dir = self.state_dir / "done"

        self.voice = str(blob.get("voice", "en-US-AriaNeural"))
        self.language = str(blob.get("language", "en"))
        self.groq_model = str(blob.get("groq_stt_model", "whisper-large-v3-turbo"))
        self.llm_provider = str(blob.get("llm_provider", "groq")).lower()
        self.llm_model = str(blob.get("llm_model", "qwen/qwen3.8-27b"))
        self.gemini_model = str(blob.get("gemini_model", "gemini-3.6-flash"))
        self.chunk_minutes = int(blob.get("chunk_minutes", 8))
        self.session_merge_minutes = int(blob.get("session_merge_minutes", 10))
        self.speaker_diarization = bool(blob.get("speaker_diarization", True))
        self.keyword_flag = bool(blob.get("keyword_flag", True))
        self.tts_feedback = bool(blob.get("tts_feedback", True))
        self.player = blob.get("player")  # "mpv" | "ffplay" | "mpg123" (auto if unset)
        self.max_retry_age_hours = float(blob.get("max_retry_age_hours", 72))

        for d in (self.notes_dir, self.recordings_dir, self.queue_dir,
                  self.state_dir, self.done_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def online(self) -> bool:
        """Best-effort 'are our providers configured' check."""
        return bool(self.groq_api_key and self.gemini_api_key)


def load() -> Config:
    return Config()