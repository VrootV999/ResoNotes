"""Groq whisper transcription with automatic chunking for long takes.

Groq's free tier allows uploads up to ~25 MB, so anything longer is split into
`chunk_minutes` pieces (8 min at 16 kHz mono i16 is ~15 MB) and re-assembled
with corrected timestamps.
"""

from __future__ import annotations

import io
import logging
import re
import wave
from pathlib import Path

import config

log = logging.getLogger("resonote.stt")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

KEYWORD_PATTERNS = [
    re.compile(r"\bnote to self\b", re.IGNORECASE),
    re.compile(r"\bbookmark this\b", re.IGNORECASE),
    re.compile(r"\bflag this\b", re.IGNORECASE),
    re.compile(r"\bremind me to\b", re.IGNORECASE),
]


class ApiError(Exception):
    """Provider unreachable; take should be queued and retried."""


def _client(cfg: config.Config):
    if not cfg.groq_api_key:
        raise ApiError("GROQ_API_KEY not configured")
    import groq

    return groq.Groq(api_key=cfg.groq_api_key)


def _temp_chunks(path: Path, seconds: int):
    """Yield (start_offset, BytesIO) chunks of a 16-bit PCM wav."""
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        rate = params.framerate
        nframes = w.getnframes()
        total = nframes / rate
        if params.sampwidth != 2:
            raise ApiError("audio is not 16-bit PCM, cannot chunk")
        byte_rate = rate * params.nchannels * 2
        max_chunk_secs = int((18 * 1024 * 1024) // byte_rate)  # stay under Groq's upload limit
        chunk_secs = min(seconds, max_chunk_secs) or 1
        chunk_frames = rate * chunk_secs
        start = 0
        while start < nframes:
            data = w.readframes(min(chunk_frames, nframes - start))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setparams((params.nchannels, 2, rate, len(data), "NONE", "not compressed"))
                out.writeframes(data)
            yield start / rate, buf
            start += chunk_frames


def transcribe(path: Path, cfg: config.Config) -> dict:
    """Transcribe wav; returns {text, segments:[{start,end,text}], bookmarks:[...]}."""
    client = _client(cfg)
    segmented = path.stat().st_size > MAX_UPLOAD_BYTES
    chunk_secs = cfg.chunk_minutes * 60
    segments: list[dict] = []
    bookmarks: list[dict] = []

    def add(seg, offset: float) -> None:
        start = round(seg.get("start", 0) + offset, 2)
        end = round(seg.get("end", start) + offset, 2)
        text = (seg.get("text") or "").strip()
        if not text:
            return
        segments.append({"start": start, "end": end, "text": text})
        for pat in KEYWORD_PATTERNS:
            if pat.search(text):
                bookmarks.append({"time": _fmt(start), "text": text})

    try:
        if not segmented:
            with open(path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model=cfg.groq_model,
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    language=cfg.language or None,
                )
            for seg in getattr(resp, "segments", None) or []:
                add(seg, 0.0)
        else:
            log.info("splitting %s into %ss chunks", path.name, chunk_secs)
            for offset, buf in _temp_chunks(path, chunk_secs):
                resp = client.audio.transcriptions.create(
                    model=cfg.groq_model,
                    file=buf,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    language=cfg.language or None,
                )
                for seg in getattr(resp, "segments", None) or []:
                    add(seg, offset)
    except Exception as e:  # noqa: BLE001 — network/provider errors are retryable
        raise ApiError(f"Groq transcription failed: {e}") from e

    text = " ".join(s["text"] for s in segments).strip()
    if not segments:
        raise ApiError("transcription returned no segments (silence?)")
    return {"text": text, "segments": segments, "bookmarks": bookmarks}


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"