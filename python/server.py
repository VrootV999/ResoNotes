"""ResoNote Python backend (FastAPI).

Serves 127.0.0.1 only. Rust (the tray app) launches this process, then:
    POST /api/process_file  submit a recorded take for processing
    POST /api/say           fire-and-forget TTS feedback
    POST /api/new_session   force a fresh note session
    GET  /api/health        liveness
    POST /api/quit          graceful shutdown
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
import notes
import pipeline
import tts

log = logging.getLogger("resonote")

app = FastAPI(title="ResoNote backend", version="0.1.0")

ALLOWED_ROOT = "auto"


class ProcessRequest(BaseModel):
    path: str
    take_id: Optional[str] = ""


class SayRequest(BaseModel):
    text: Optional[str] = ""


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/process_file")
def process_file(body: ProcessRequest) -> dict:
    cfg = config.load()
    p = Path(body.path).expanduser().resolve()
    root = cfg.recordings_dir.resolve()
    if root != p.parent and p.parent != cfg.queue_dir.resolve():
        log.warning("rejecting path outside recorded dirs: %s", p)
        raise HTTPException(status_code=403, detail="path not allowed")
    take = (body.take_id or "").strip()
    if not take or not take.isascii() or any(c in take for c in "/\\ "):
        take = p.stem
    status = pipeline.schedule(str(p), take)
    return {"status": status}


@app.post("/api/say")
def say(body: SayRequest) -> dict:
    tts.say(body.text or "")
    return {"ok": True}


@app.post("/api/new_session")
def new_session() -> dict:
    stamp = notes.SessionStore(config.load()).reset()
    return {"ok": True, "session": stamp}


@app.post("/api/quit")
def quit_app() -> dict:
    server = app.state.server
    if server is not None:
        server.should_exit = True
    return {"ok": True}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load()

    stop = threading.Event()
    worker = threading.Thread(target=pipeline.queue_worker, args=(stop,), daemon=True)
    worker.start()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_level="warning")
    )
    app.state.server = server
    log.info("ResoNote backend listening on 127.0.0.1:%s", cfg.port)
    try:
        server.run()
    finally:
        stop.set()
        tts.shutdown()


if __name__ == "__main__":
    main()