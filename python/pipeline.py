"""Processing pipeline + offline queue.

- Takes land in <data>/recordings  (Rust writes them there, then asks us to process).
- If providers are unreachable the wav is moved to <data>/queue and retried.
- A background worker also drains the recordings dir, so takes survive backend
  restarts or missed IPC calls.
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
_store: notes.SessionStore | None = None


def _store_get() -> notes.SessionStore:
    global _store
    if _store is None:
        _store = notes.SessionStore(config.load())
    return _store


def _duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:  # noqa: BLE001
        return 0.0


def schedule(path: str, take_id: str = "") -> str:
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
    threading.Thread(target=process_audio, args=(p, take_id), daemon=True).start()
    return "accepted"


def process_audio(path: Path, take_id: str) -> None:
    try:
        _do(path, take_id)
    except Exception:  # noqa: BLE001
        log.exception("processing failed for %s", path)
    finally:
        with _bg_lock:
            _inflight.discard(str(path))


def _do(path: Path, take_id: str) -> None:
    cfg = config.load()
    marker = cfg.done_dir / (path.stem + ".done")
    if marker.exists():
        return

    # ---- stage 1: transcription (offline here -> queue & retry) ----
    try:
        tr = stt.transcribe(path, cfg)
    except stt.ApiError as e:
        log.warning("offline or provider error; queueing %s: %s", path.name, e)
        _enqueue(path)
        return

    dur = _duration(path)
    store = _store_get()
    stamp = store.session_for(path.stat().st_mtime, take_id, dur)
    store.append(tr["segments"], tr["bookmarks"])
    session = store.snapshot()
    accumulated = "\n".join(
        f"{_fmt(s.get('start', 0))}: {s.get('text', '')}" for s in session["segments"]
    )

    # ---- stage 2: LLM notes (offline here -> save raw transcript instead) ----
    try:
        result = llm.summarize(accumulated, session["bookmarks"], cfg)
    except stt.ApiError as e:
        log.warning("LLM unavailable (%s); writing raw transcript note", e)
        result = {
            "title": f"Voice notes — {stamp}",
            "summary": "AI note generation was unavailable; raw transcript preserved.",
            "notes": "",
            "action_items": [],
            "speakers": [],
            "bookmarks": [],
        }
    except Exception as e:  # noqa: BLE001
        log.warning("LLM returned unusable output (%s); writing raw transcript note", e)
        result = {
            "title": f"Voice notes — {stamp}",
            "summary": "AI note generation had an error; raw transcript preserved.",
            "notes": "",
            "action_items": [],
            "speakers": [],
            "bookmarks": [],
        }

    out = notes.write_note(cfg, session, result)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"take": take_id, "note": str(out)}), encoding="utf-8")

    if path.parent == cfg.queue_dir:
        try:
            path.unlink()
            (cfg.queue_dir / (path.name + ".retry")).unlink(missing_ok=True)
        except OSError:  # noqa: S110
            pass

    if cfg.tts_feedback:
        actions = len(result.get("action_items") or [])
        msg = "Notes saved."
        if actions:
            msg += f" {actions} action items."
        tts.say(msg)


def _enqueue(path: Path) -> None:
    cfg = config.load()
    dest = cfg.queue_dir / path.name
    try:
        path.replace(dest)
    except OSError:
        log.warning("could not move %s to queue", path.name)
    retry_marker = cfg.queue_dir / (path.name + ".retry")
    retry_marker.write_text(str(time.time()), encoding="utf-8")
    log.info("queued %s for retry", dest.name)
    if cfg.tts_feedback:
        tts.say("Recording is queued offline. It will be processed when the network returns.")


RETRY_BACKOFF_SECS = 90.0


def drain_once() -> int:
    """Process any wavs in recordings/ and queue/ that have no done marker."""
    cfg = config.load()
    now = time.time()
    handled = 0
    recordings = sorted(p for p in cfg.recordings_dir.glob("*.wav"))
    queued = []
    for p in sorted(cfg.queue_dir.glob("*.wav")):
        retry_marker = cfg.queue_dir / (p.name + ".retry")
        if retry_marker.exists():
            try:
                last = float(retry_marker.read_text(encoding="utf-8"))
            except ValueError:
                last = 0.0
            if now - last < RETRY_BACKOFF_SECS:
                continue  # backoff; try again later
        queued.append(p)
    candidates = recordings + queued
    for p in candidates:
        marker = cfg.done_dir / (p.stem + ".done")
        if marker.exists():
            continue
        age_hours = (now - p.stat().st_mtime) / 3600
        if age_hours > cfg.max_retry_age_hours and p.parent == cfg.queue_dir:
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