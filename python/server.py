"""ResoNote Python backend (FastAPI).

Serves 127.0.0.1 only. Rust (the tray app) launches this process, then:
    POST /api/process_file  submit a recorded take for processing
    POST /api/say           fire-and-forget TTS feedback
    GET  /api/health        liveness
    POST /api/quit          graceful shutdown

`kind` is "standalone" (one take -> one note) or "session" (takes grouped
under `name` into one combined note). Rust asks the user for `name`.
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
import pipeline
import tts

log = logging.getLogger("resonote")

app = FastAPI(title="ResoNote backend", version="0.2.0")


class ProcessRequest(BaseModel):
    path: str
    take_id: Optional[str] = ""
    kind: Optional[str] = "standalone"
    name: Optional[str] = ""


class SayRequest(BaseModel):
    text: Optional[str] = ""


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/process_file")
def process_file(body: ProcessRequest) -> dict:
    cfg = config.load()
    p = Path(body.path).expanduser().resolve()
    rec_root = cfg.recordings_dir.resolve()
    que_root = cfg.queue_dir.resolve()
    notes_root = cfg.notes_dir.resolve()
    allowed = p.is_relative_to(rec_root) or p.is_relative_to(que_root)
    if not allowed and notes_root in p.parents:
        allowed = True  # session recordings live in <notes>/<session>/
    if not allowed:
        log.warning("rejecting path outside recorded dirs: %s", p)
        raise HTTPException(status_code=403, detail="path not allowed")
    take = (body.take_id or "").strip()
    if not take or not take.isascii() or any(c in take for c in "/\\ "):
        take = p.stem
    kind = body.kind or "standalone"
    if kind not in ("standalone", "session"):
        kind = "standalone"
    name = (body.name or "").strip()
    status = pipeline.schedule(str(p), take, kind, name)
    return {"status": status}


@app.post("/api/say")
def say(body: SayRequest) -> dict:
    tts.say(body.text or "")
    return {"ok": True}


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