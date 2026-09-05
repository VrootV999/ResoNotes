"""Markdown note writing for standalone takes and named sessions.

- `standalone`: exactly one recording -> one note (`<notes>/<name>.md`).
  Never includes any other take.
- `session`: recordings land in `<notes>/<session>/` next to the notes; the
  session keeps a per-name accumulator (`<state>/sessions/<session>.json`) and
  every new take re-renders the session files so they keep updating live:
      <notes>/<session>/summary.md   overview + meta + action items
      <notes>/<session>/notes.md     detailed markdown notes
      <notes>/<session>/<topic>.md   one file per big topic (Unit 1, SQLI, ...)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("resonote.notes")

_session_lock = threading.Lock()


def sanitize_name(name: str) -> str:
    """Filesystem-safe version of a user-chosen name, 'untitled' if empty."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip())
    while "--" in name:
        name = name.replace("--", "-")
    name = name.strip("-. _")
    if not name:
        return "untitled"
    return name[:80]


def stamp_name() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def topic_filename(file: str) -> str:
    """Safe '<name>.md' for a topic ('Unit 1' -> 'Unit-1.md')."""
    safe = sanitize_name(str(file or "topic"))
    return f"{safe}.md" if not safe.endswith(".md") else safe


class SessionAcc:
    """Per-session accumulator: takes list + transcripts, persisted atomically."""

    def __init__(self, cfg: config.Config, safe: str, display: str = "") -> None:
        self.cfg = cfg
        self.safe = sanitize_name(safe) or "untitled"
        self.display = (display or self.safe).strip()
        self.state_file = cfg.state_dir / "sessions" / f"{self.safe}.json"
        self._state: dict | None = None

    def _load(self) -> dict:
        if self._state is not None:
            return self._state
        default = {
            "safe": self.safe,
            "display": self.display,
            "stamp": None,
            "created_ts": None,
            "last_take_ts": None,
            "takes": [],
            "segments": [],
            "bookmarks": [],
        }
        if self.state_file.exists():
            try:
                loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                loaded = None
            if isinstance(loaded, dict):
                loaded = {**default, **loaded, "safe": self.safe}
                self._state = loaded
                return self._state
        self._state = default
        return self._state

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_file)

    def add_take(self, take_id: str, path: str, dur: float, when_ts: float) -> None:
        with _session_lock:
            st = self._load()
            if st["created_ts"] is None:
                st["created_ts"] = when_ts
                st["stamp"] = datetime.fromtimestamp(when_ts).strftime("%Y-%m-%d_%H-%M-%S")
            st["last_take_ts"] = max(when_ts, st["last_take_ts"] or 0)
            st["takes"].append({
                "id": take_id,
                "dur": round(dur, 1),
                "when": datetime.fromtimestamp(when_ts).strftime("%Y-%m-%d %H:%M"),
                "when_ts": when_ts,
                "path": path,
            })
            self._save()

    def append_segments(self, segments: list[dict], bookmarks: list[dict], when_ts: float) -> None:
        with _session_lock:
            st = self._load()
            for s in segments:
                st["segments"].append({**s, "ts": when_ts})
            for b in bookmarks:
                if b not in st["bookmarks"]:
                    st["bookmarks"].append(b)
            self._save()

    def snapshot(self) -> dict:
        with _session_lock:
            st = json.loads(json.dumps(self._load()))
            st["takes"].sort(key=lambda t: t.get("when_ts", 0))
            st["segments"].sort(key=lambda s: (s.get("ts", 0), s.get("start", 0)))
            return st


def render_standalone(ctx: dict, result: dict) -> str:
    """Single note for a standalone take (its own content only, no transcript)."""
    total_secs = sum(t.get("dur", 0) for t in ctx.get("takes", []))
    mins, secs = divmod(int(total_secs), 60)
    title = (result.get("title") or ctx.get("display") or ctx.get("safe") or "Voice Notes").strip()
    meta = f"- **Take:** {ctx.get('stamp') or ctx.get('safe')}\n- **Duration:** {mins}m {secs}s"

    out = [f"# {title}", "", f"> {result.get('summary') or ''}".strip(), "", meta.strip(), ""]

    notes = result.get("notes") or ""
    if notes:
        out += ["## Notes", "", notes.strip(), ""]

    actions = result.get("action_items") or []
    if actions:
        out += ["## Action Items", ""]
        out += [f"- [ ] {a}" for a in actions]
        out += [""]

    bookmarks = result.get("bookmarks") or ctx.get("bookmarks") or []
    if bookmarks:
        out += ["## Bookmarks", ""]
        for b in bookmarks:
            if isinstance(b, dict):
                out.append(f"- **{b.get('time', '')}** {b.get('text', '')}".rstrip())
            else:
                out.append(f"- {b}")
        out += [""]

    speakers = result.get("speakers") or []
    if speakers:
        out += ["## Speakers", ""]
        out += [f"- {s}" for s in speakers]
        out += [""]

    return "\n".join(out).rstrip() + "\n"


def render_session(ctx: dict, result: dict) -> dict[str, str]:
    """Files for a session: summary.md + notes.md + one .md per big topic."""
    total_secs = sum(t.get("dur", 0) for t in ctx.get("takes", []))
    mins, secs = divmod(int(total_secs), 60)
    display = ctx.get("display") or ctx.get("safe") or "Session"
    summary = (result.get("summary") or "").strip()
    meta = (
        f"- **Session:** {display}\n"
        f"- **Recorded:** {len(ctx.get('takes', []))} take(s) · {mins}m {secs}s\n"
    )

    summary_lines = [f"# {display}", ""]
    if summary:
        summary_lines += [f"> {summary}", ""]
    summary_lines += [meta.strip(), ""]

    actions = result.get("action_items") or []
    if actions:
        summary_lines += ["## Action Items", ""]
        summary_lines += [f"- [ ] {a}" for a in actions]
        summary_lines += [""]

    notes = (result.get("notes") or "").strip()
    notes_md = []
    if notes:
        notes_md += [f"# {display} — Notes", "", notes, ""]

    bookmarks = result.get("bookmarks") or ctx.get("bookmarks") or []
    if bookmarks:
        notes_md += ["## Bookmarks", ""]
        for b in bookmarks:
            if isinstance(b, dict):
                notes_md.append(f"- **{b.get('time', '')}** {b.get('text', '')}".rstrip())
            else:
                notes_md.append(f"- {b}")
        notes_md += [""]

    speakers = result.get("speakers") or []
    if speakers:
        notes_md += ["## Speakers", ""]
        notes_md += [f"- {s}" for s in speakers]
        notes_md += [""]

    files = {
        "summary.md": "\n".join(summary_lines).rstrip() + "\n",
        "notes.md": "\n".join(notes_md).rstrip() + "\n",
    }
    for topic in result.get("topics") or []:
        content = str(topic.get("content") or "").strip()
        if not content:
            continue
        name = topic_filename(str(topic.get("file") or "topic"))
        files[name] = f"# {name[:-3]}\n\n{content}\n"
    return files


def write_files(cfg: config.Config, target_dir: Path, files: dict[str, str]) -> list[Path]:
    """Write {filename: markdown} atomically into target_dir, returns written paths.

    Concurrent writers (several takes of one session) may target the same file,
    so the temp name is unique; replace() keeps the write atomic.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fname, md in files.items():
        path = target_dir / fname
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(path)
        written.append(path)
    log.info("wrote %d note(s) -> %s", len(written), target_dir)
    return written