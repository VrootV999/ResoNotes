"""Processing pipeline + offline queue.

- A take is one wav. `kind` says what happens to its note:
    "standalone" -> one recording, one note (never merged with anything else)
    "session"    -> the take joins session `<name>`; the combined session note
                    is re-rendered from every take in that session.
- If providers are unreachable the wav (plus a small `.meta` sidecar carrying
  kind/name) moves to <data>/queue and is retried.
- The worker also drains recordings/ (recursively, so in-session subfolders and
  takes that survived a crash are processed too).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import wave
from pathlib import Path

import config
import llm
import notes
import stt
import tts

log = logging.getLogger("resonote.pipeline")

_inflight: set[str] = set()
_bg_lock = threading.Lock()
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:  # noqa: BLE001
        return 0.0


def _meta_file(path: Path) -> Path:
    return path.with_name(path.name + ".meta")


def _read_meta(path: Path) -> tuple[str, str]:
    """Return (kind, name) from the sidecar next to a wav, or ("", "")."""
    meta = _meta_file(path)
    if meta.exists():
        try:
            blob = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(blob, dict):
                return str(blob.get("kind") or ""), str(blob.get("name") or "")
        except Exception:  # noqa: BLE001
            pass
    return "", ""


def schedule(path: str, take_id: str = "", kind: str = "", name: str = "") -> str:
    """Submit a wav for processing on a background thread (deduped)."""
    try:
        p = Path(path).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return "bad-path"
    if p.suffix.lower() != ".wav" or not p.exists():
        return "missing"
    take_id = take_id or p.stem
    key = str(p)
    with _bg_lock:
        if key in _inflight:
            return "already-processing"
        _inflight.add(key)
    threading.Thread(target=process_audio, args=(p, take_id, kind, name), daemon=True).start()
    return "accepted"


def process_audio(path: Path, take_id: str, kind: str, name: str) -> None:
    try:
        _do(path, take_id, kind, name)
    except Exception:  # noqa: BLE001
        log.exception("processing failed for %s", path)
    finally:
        with _bg_lock:
            _inflight.discard(str(path))


def _resolve_kind_name(path: Path, kind: str, name: str, cfg: config.Config) -> tuple[str, str]:
    """Effective kind/name: request args, then sidecar, then folder naming.

    Sessions now live in `<notes>/<session>/` (recordings + notes together).
    A wav directly in recordings/ or queue/ is a standalone take; anywhere else
    (a subfolder) is a session take named after its folder.
    """
    skeep, sname = _read_meta(path)
    kind = kind or skeep or "standalone"
    name = notes.sanitize_name(name or sname)
    if not name or name == "untitled":
        flat_roots = (cfg.recordings_dir, cfg.queue_dir)
        is_flat = path.parent in flat_roots
        if not is_flat:
            kind = "session"
            name = notes.sanitize_name(path.parent.name)
        else:
            name = notes.stamp_name()
            kind = kind or "standalone"
    if kind not in ("standalone", "session"):
        kind = "standalone"
    return kind, name


def _session_lock(name: str) -> threading.Lock:
    """A stable per-session lock so takes of one session never race the notes."""
    with _session_locks_guard:
        return _session_locks.setdefault(name, threading.Lock())


def _do(path: Path, take_id: str, kind: str, name: str) -> None:
    cfg = config.load()
    marker = cfg.done_dir / (path.stem + ".done")
    if marker.exists():
        return

    # ---- stage 1: transcription (offline here -> queue & retry) ----
    try:
        tr = stt.transcribe(path, cfg)
    except stt.ApiError as e:
        log.warning("offline or provider error; queueing %s: %s", path.name, e)
        _enqueue(path, kind, name, cfg)
        return

    kind, name = _resolve_kind_name(path, kind, name, cfg)
    if kind == "session":
        with _session_lock(name):
            _finish_take(path, take_id, kind, name, tr)
    else:
        _finish_take(path, take_id, kind, name, tr)


def _finish_take(path: Path, take_id: str, kind: str, name: str, tr: dict) -> None:
    """Build + write the notes for one take (session takes hold the lock)."""
    cfg = config.load()
    marker = cfg.done_dir / (path.stem + ".done")
    segments = tr["segments"]
    bookmarks = tr["bookmarks"]
    dur = _duration(path)

    if kind == "session":
        display = name
        acc = notes.SessionAcc(cfg, name, display)
        acc.add_take(take_id, str(path), dur, path.stat().st_mtime)
        acc.append_segments(segments, bookmarks, path.stat().st_mtime)
        snap = acc.snapshot()
        ctx = {
            "mode": "session",
            "safe": name,
            "display": snap.get("display") or name,
            "stamp": snap.get("stamp") or name,
            "takes": snap.get("takes", []),
            "segments": snap.get("segments", []),
            "bookmarks": snap.get("bookmarks", []),
        }
        accumulated = "\n".join(
            f"{_fmt(s.get('start', 0))}: {s.get('text', '')}" for s in ctx["segments"]
        )
        union_books = ctx["bookmarks"]
        base_dir = cfg.notes_dir / name
    else:
        ctx = {
            "mode": "standalone",
            "safe": name,
            "display": name,
            "stamp": name,
            "takes": [{"id": take_id, "dur": round(dur, 1), "when": "", "path": str(path)}],
            "segments": segments,
            "bookmarks": bookmarks,
        }
        accumulated = "\n".join(f"{_fmt(s.get('start', 0))}: {s.get('text', '')}" for s in segments)
        union_books = bookmarks
        base_dir = cfg.notes_dir

    # ---- stage 2: LLM notes (offline here -> save a minimal fallback note) ----
    try:
        result = llm.summarize(accumulated, union_books, cfg)
    except stt.ApiError as e:
        log.warning("LLM unavailable (%s); writing minimal note", e)
        result = _raw_result(ctx.get("display") or name)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM returned unusable output (%s); writing minimal note", e)
        result = _raw_result(ctx.get("display") or name)

    if kind == "session":
        files = notes.render_session(ctx, result)
    else:
        files = {f"{name}.md": notes.render_standalone(ctx, result)}
    notes.write_files(cfg, base_dir, files)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"take": take_id, "kind": kind, "name": name}), encoding="utf-8")

    if path.parent == cfg.queue_dir or (cfg.queue_dir in path.parents):
        try:
            path.unlink()
            _meta_file(path).unlink(missing_ok=True)
            Path(str(path) + ".retry").unlink(missing_ok=True)
        except OSError:  # noqa: S110
            pass

    if cfg.tts_feedback:
        actions = len(result.get("action_items") or [])
        msg = "Notes saved."
        if actions:
            msg += f" {actions} action items."
        tts.say(msg)


def _raw_result(display: str) -> dict:
    return {
        "summary": f"AI note generation is unavailable right now. The recording for '{display}' was saved; run again or check network/API keys.",
        "notes": "",
        "topics": [],
        "action_items": [],
        "speakers": [],
        "bookmarks": [],
    }


def _enqueue(path: Path, kind: str, name: str, cfg: config.Config) -> None:
    eff_kind, eff_name = _resolve_kind_name(path, kind, name, cfg)
    dest_dir = cfg.queue_dir / eff_name if eff_kind == "session" else cfg.queue_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    try:
        path.replace(dest)
    except OSError:
        log.warning("could not move %s to queue", path.name)
        return
    # remember kind/name so the queued wav keeps its identity
    _meta_file(dest).write_text(
        json.dumps({"kind": eff_kind, "name": eff_name}, ensure_ascii=False),
        encoding="utf-8",
    )
    retry_marker = Path(str(dest) + ".retry")
    retry_marker.write_text(str(time.time()), encoding="utf-8")
    log.info("queued %s for retry (%s, %s)", dest, eff_kind, eff_name)
    if cfg.tts_feedback:
        tts.say("Recording is queued offline. It will be processed when the network returns.")


RETRY_BACKOFF_SECS = 90.0


def drain_once() -> int:
    """Process any wavs under recordings/, queue/, and session folders in notes/."""
    cfg = config.load()
    now = time.time()
    handled = 0
    extra = sorted(p for p in cfg.notes_dir.rglob("*.wav") if cfg.notes_dir in p.parents)
    candidates = (
        sorted(cfg.recordings_dir.rglob("*.wav"))
        + sorted(cfg.queue_dir.rglob("*.wav"))
        + extra
    )
    for p in candidates:
        marker = cfg.done_dir / (p.stem + ".done")
        if marker.exists():
            continue
        if cfg.queue_dir in p.parents:
            retry_marker = Path(str(p) + ".retry")
            if retry_marker.exists():
                try:
                    last = float(retry_marker.read_text(encoding="utf-8"))
                except ValueError:
                    last = 0.0
                if now - last < RETRY_BACKOFF_SECS:
                    continue  # backoff; try again later
            age_hours = (now - p.stat().st_mtime) / 3600
            if age_hours > cfg.max_retry_age_hours:
                marker.write_text(json.dumps({"expired": True}), encoding="utf-8")
                log.info("expired queued take %s", p.name)
                handled += 1
                continue
        schedule(str(p), p.stem)
        handled += 1
    return handled


def queue_worker(stop: threading.Event) -> None:
    log.info("queue worker started")
    while not stop.is_set():
        try:
            n = drain_once()
            if n:
                log.debug("drained %s candidate(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("drain error")
        stop.wait(20)


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"