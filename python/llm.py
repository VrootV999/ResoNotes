"""Note generation for ResoNote.

Primary provider is Groq (free tier, same key as STT) whose chat models stay
reachable; Gemini is used as a fallback when configured/available.

Output shape (JSON) expected from the model:
{
  "summary": "2-3 sentence session summary",          // -> summary.md
  "notes":  "detailed structured markdown notes",      // -> notes.md
  "topics": [{"file": "Unit 1", "content": "..."}],    // optional -> <file>.md
  "action_items": ["Owner — task by deadline"],
  "speakers": ["Speaker A"],
  "bookmarks": [{"time": "M:SS", "text": "..."}]
}
Raw transcripts are never written to the final notes.
"""

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
- Write the detailed notes as rich markdown: use #/##/### headings for topics and
  subtopics, bullet lists, bold, and code fences/tables where they help. Fully use
  markdown formatting.
- "summary" is a short 2-4 sentence overview of the session.
- If the session clearly covers several LARGE, independent topics, split each big
  topic into its own file via "topics" (e.g. Unit 1, SQLI, XSS, SSRF, Budget). Use
  short file names without extension. Otherwise leave "topics": [].
- Extract ACTION ITEMS as a flat list. Each item must include, when present, what is
  to be done, who owns it, and its deadline (e.g. "Owner or Company — Ship the mockups by Friday").
  Only include actions actually stated in the transcript.
- If the transcript clearly contains multiple speakers and the input uses
  "[A]"/"[B]" markers, keep that attribution in the notes. Do not invent labels.
- Detect "note to self", "bookmark", "flag this" or similar and surface the context
  in "bookmarks".
- Respond with ONLY a single JSON object, no markdown fences, no commentary.
"""

SHAPE = """\
Reply with JSON of exactly this shape:
{
  "summary": "short overview",
  "notes": "detailed markdown notes with headings and bullets",
  "topics": [{"file": "Unit 1", "content": "markdown for this topic"}],
  "action_items": ["Owner — task by deadline"],
  "speakers": ["Speaker A", "Speaker B"],
  "bookmarks": [{"time": "M:SS", "text": "flagged context snippet"}]
}"""


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


def _base_prompt(transcript: str, bookmarks: list[dict], cfg: config.Config) -> str:
    bookmarks_json = json.dumps(bookmarks, ensure_ascii=False)
    diarize = "yes" if cfg.speaker_diarization else "no"
    return f"""\
{SYSTEM}

Speaker-labelling enabled: {diarize}

Transcript:
-----
{transcript}
-----

Known flagged bookmarks from audio timestamps:
{bookmarks_json if bookmarks else "[]"}

{SHAPE}"""


def _summarize_groq(prompt: str, cfg: config.Config) -> dict:
    if not cfg.groq_api_key:
        raise ApiError("GROQ_API_KEY not configured")
    try:
        import httpx
    except ImportError as e:  # noqa: F841
        raise ApiError("httpx not installed (run install.sh)") from e

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.groq_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.llm_model or "qwen/qwen3.8-27b",
        "messages": [
            {"role": "system", "content": SYSTEM + "\nRespond with ONLY a single JSON object."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
    }
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        parsed = _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"Groq LLM failed: {e}") from e
    return parsed


def _summarize_gemini(prompt: str, cfg: config.Config) -> dict:
    if not cfg.gemini_api_key:
        raise ApiError("GEMINI_API_KEY not configured")
    try:
        from google import genai
    except ImportError as e:  # noqa: F841
        raise ApiError("google-genai not installed (run install.sh)") from e
    try:
        client = genai.Client(api_key=cfg.gemini_api_key)
        resp = client.models.generate_content(
            model=cfg.gemini_model,
            contents=prompt,
        )
        raw = resp.text or ""
        parsed = _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"Gemini generation failed: {e}") from e
    return parsed


def summarize(transcript: str, bookmarks: list[dict], cfg: config.Config) -> dict:
    """Returns {summary, notes, topics, action_items, speakers, bookmarks}."""
    prompt = _base_prompt(transcript, bookmarks, cfg)
    order = {"groq": ["groq", "gemini"], "gemini": ["gemini", "groq"]}.get(
        (cfg.llm_provider or "auto").lower(), ["groq", "gemini"]
    )
    errors: list[str] = []
    for provider in order:
        try:
            if provider == "gemini":
                parsed = _summarize_gemini(prompt, cfg)
            else:
                parsed = _summarize_groq(prompt, cfg)
            break
        except ApiError as e:
            log.warning("LLM provider %s failed: %s", provider, e)
            errors.append(f"{provider}: {e}")
            parsed = None
    if parsed is None:
        raise ApiError("; ".join(errors))

    if not isinstance(parsed, dict):
        raise ApiError("LLM returned non-JSON output")

    topics = parsed.get("topics")
    if not isinstance(topics, list):
        topics = []
    topics = [t for t in topics if isinstance(t, dict) and str(t.get("content") or "").strip()]
    for key in ("summary", "notes", "action_items", "speakers", "bookmarks"):
        parsed.setdefault(key, [] if key in ("action_items", "speakers", "bookmarks") else "")
    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "notes": str(parsed.get("notes", "")).strip(),
        "topics": topics,
        "action_items": [str(i).strip() for i in parsed.get("action_items", []) if str(i).strip()],
        "speakers": [str(s).strip() for s in parsed.get("speakers", []) if str(s).strip()],
        "bookmarks": parsed.get("bookmarks", []),
    }