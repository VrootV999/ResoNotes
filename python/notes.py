"""Session-aware markdown note writing to ~/VoiceNotes.

Takes that arrive within `session_merge_minutes` of each other merge into one
note file; longer gaps (or explicit "New Session") start a fresh one.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("resonote.notes")

_session_lock = threading.Lock()


class SessionStore:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.state_file = cfg.state_dir / "session.json"
        self._state: dict | None = None

    def _load(self) -> dict:
        if self._state is not None:
            return self._state
        if self.state_file.exists():
            try:
                self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._state = None
        if not isinstance(self._state, dict) or "stamp" not in self._state:
            self._state = {"stamp": None, "last_take_ts": None, "takes": [], "segments": [], "bookmarks": []}
        return self._state

    def _write(self) -> None:
        """Persist state; callers must hold _session_lock."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_file)

    def reset(self) -> str:
        """Start a brand-new session; returns its stamp."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with _session_lock:
            self._state = {"stamp": stamp, "last_take_ts": None, "takes": [], "segments": [], "bookmarks": []}
            self._write()
        return stamp

    def session_for(self, take_ts: float, take_id: str, dur: float) -> str:
        """Return the session stamp this take belongs to (writes the take in)."""
        with _session_lock:
            st = self._load()
            now = take_ts
            if st["stamp"] and st["last_take_ts"] is not None:
                gap = now - st["last_take_ts"]
                if gap <= self.cfg.session_merge_minutes * 60 and gap >= -5:
                    pass  # merge into current session
                else:
                    self.reset_locked(now)
            else:
                self.reset_locked(now)
            st = self._load()
            stamp = st["stamp"]
            st["last_take_ts"] = now
            st["takes"].append({
                "id": take_id,
                "dur": round(dur, 1),
                "when": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
            })
            self._write()
            return stamp

    def reset_locked(self, ts: float) -> None:
        stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d_%H-%M-%S")
        self._state = {"stamp": stamp, "last_take_ts": ts, "takes": [], "segments": [], "bookmarks": []}

    def append(self, segments: list[dict], bookmarks: list[dict]) -> None:
        with _session_lock:
            st = self._load()
            st["segments"].extend(segments)
            for b in bookmarks:
                if b not in st["bookmarks"]:
                    st["bookmarks"].append(b)
            self._write()

    def snapshot(self) -> dict:
        with _session_lock:
            return json.loads(json.dumps(self._load()))


def render_markdown(cfg: config.Config, session: dict, result: dict) -> str:
    total_secs = sum(t.get("dur", 0) for t in session.get("takes", []))
    mins, secs = divmod(int(total_secs), 60)
    title = (result.get("title") or "Voice Notes").strip()
    meta = (
        f"- **Session:** {session.get('stamp')}\n"
        f"- **Recorded:** {len(session.get('takes', []))} take(s) · {mins}m {secs}s\n"
    )

    out = [f"# {title}", "", f"> {result.get('summary') or ''}".strip(), "", meta.strip(), ""]

    actions = result.get("action_items") or []
    if actions:
        out += ["## Action Items", ""]
        out += [f"- [ ] {a}" for a in actions]
        out += [""]

    bookmarks = result.get("bookmarks") or session.get("bookmarks") or []
    if bookmarks and cfg.keyword_flag:
        out += ["## Bookmarks", ""]
        for b in bookmarks:
            if isinstance(b, dict):
                out.append(f"- **{b.get('time', '')}** {b.get('text', '')}".rstrip())
            else:
                out.append(f"- {b}")
        out += [""]

    notes = result.get("notes") or ""
    if notes:
        out += ["## Notes", "", notes.strip(), ""]

    speakers = result.get("speakers") or []
    if speakers:
        out += ["## Speakers", ""]
        out += [f"- {s}" for s in speakers]
        out += [""]

    segments = session.get("segments") or []
    if segments:
        out += ["## Transcript (raw)", ""]
        for s in segments:
            t = _fmt(s.get("start", 0))
            out.append(f"**[{t}]** {s.get('text', '')}")

    return "\n".join(out).rstrip() + "\n"


def write_note(cfg: config.Config, session: dict, result: dict) -> Path:
    md = render_markdown(cfg, session, result)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.notes_dir / f"{session['stamp']}_session.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(md, encoding="utf-8")
    tmp.replace(path)
    log.info("wrote note -> %s", path)
    return path


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"