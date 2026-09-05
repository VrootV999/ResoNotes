"""Gemini note generation: structured markdown + action items + speaker labels."""

from __future__ import annotations

import json
import logging
import re

import config
from stt import ApiError

log = logging.getLogger("resonote.llm")

SYSTEM = """\
You are ResoNote, a precise voice-note assistant. You convert a raw meeting/voice
transcript into clean, structured, factual markdown notes. Never invent quotes,
names, deadlines, or facts that are not in the transcript. If something is unclear,
leave it out.

Rules:
- Remove filler words ("um", "uh", "you know"), false starts, and repetition.
- Preserve concrete details: names, numbers, dates, deadlines, owners.
- Organize the notes with headings and bullet points; keep language identical to the
  transcript's language.
- Extract ACTION ITEMS as a flat list. Each item must include, when present: what is
  to be done, who owns it, and its deadline (e.g. "Owner or Company — Ship the mockups by Friday").
  Only include actions actually stated in the transcript.
- If the transcript clearly contains multiple speakers and the input transcript uses
  "[A]"/"[B]" markers, keep that attribution in the notes. If it does not, do not invent
  speaker labels.
- Detect if anyone says "note to self", "bookmark", "flag this" or similar and surface the
  context in the bookmarks array.
- Respond with ONLY a single JSON object, no markdown fences, no commentary.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def summarize(transcript: str, bookmarks: list[dict], cfg: config.Config) -> dict:
    """Returns {title, summary, notes, action_items, speakers, bookmarks}."""
    if not cfg.gemini_api_key:
        raise ApiError("GEMINI_API_KEY not configured")
    try:
        from google import genai
    except ImportError as e:  # noqa: F841
        raise ApiError("google-genai not installed (run install.sh)") from e

    bookmarks_json = json.dumps(bookmarks, ensure_ascii=False)
    diarize = "yes" if cfg.speaker_diarization else "no"
    prompt = f"""\
{SYSTEM}

Speaker-labelling enabled: {diarize}

Transcript:
-----
{transcript}
-----

Known flagged bookmarks from audio timestamps:
{bookmarks_json if bookmarks else "[]"}

Reply with JSON of the shape:
{{
  "title": "short descriptive title",
  "summary": "2-3 sentence summary",
  "notes": "full structured markdown notes (headings + bullets, no JSON)",
  "action_items": ["owner-less action item text", "Owner — task by deadline"],
  "speakers": ["Speaker A", "Speaker B"] or [],
  "bookmarks": [{{"time": "M:SS", "text": "flagged context snippet"}}]
}}"""

    try:
        client = genai.Client(api_key=cfg.gemini_api_key)
        resp = client.models.generate_content(
            model=cfg.gemini_model,
            contents=prompt,
        )
        raw = resp.text or ""
        parsed = _extract_json(raw)
    except Exception as e:  # noqa: BLE001 — retryable network/provider issues
        raise ApiError(f"Gemini generation failed: {e}") from e

    for key in ("title", "summary", "notes", "action_items", "speakers", "bookmarks"):
        parsed.setdefault(key, [] if key in ("action_items", "speakers", "bookmarks") else "")
    return {
        "title": str(parsed.get("title", "")).strip(),
        "summary": str(parsed.get("summary", "")).strip(),
        "notes": str(parsed.get("notes", "")).strip(),
        "action_items": [str(i).strip() for i in parsed.get("action_items", []) if str(i).strip()],
        "speakers": [str(s).strip() for s in parsed.get("speakers", []) if str(s).strip()],
        "bookmarks": parsed.get("bookmarks", []),
    }