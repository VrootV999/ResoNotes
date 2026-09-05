"""Free TTS via edge-tts (Microsoft neural voices; no key) with local playback."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import config

log = logging.getLogger("resonote.tts")

_lock = threading.Lock()
_proc: list[subprocess.Popen | None] = [None]
_voice = config.load().voice

MAX_TEXT = 180


def pick_player(cfg: config.Config) -> str | None:
    if cfg.player:
        if shutil.which(cfg.player):
            return cfg.player
        log.warning("configured player %r not found", cfg.player)
    for name in ("mpv", "ffplay", "mpg123"):
        if shutil.which(name):
            return name
    return None


def stop() -> None:
    with _lock:
        p = _proc[0]
        if p is not None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
            _proc[0] = None


def play_mp3(path: Path) -> bool:
    player = pick_player(config.load())
    if player is None:
        log.debug("no player available, skipping TTS playback")
        return False
    stop()
    cmd: list[str]
    if player == "mpv":
        cmd = ["mpv", "--really-quiet", "--no-terminal", str(path)]
    elif player == "ffplay":
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
    else:
        cmd = [player, str(path)]
    with _lock:
        try:
            _proc[0] = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("playback failed: %s", e)
            _proc[0] = None
            return False


def _synthesize(text: str, out: Path) -> None:
    try:
        asyncio.run(_save(text, out))
    except Exception as e:  # noqa: BLE001
        log.warning("edge-tts failed: %s", e)
        if out.exists():
            out.unlink()


async def _save(text: str, out: Path) -> None:
    import edge_tts

    voice = config.load().voice
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out))


def say(text: str | None) -> None:
    """Speak `text` asynchronously (fire and forget)."""
    if not text:
        return
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "..."
    if config.load().player == "none":
        return

    def run() -> None:
        cfg = config.load()
        if pick_player(cfg) is None:
            return
        tmp = Path(tempfile.gettempdir()) / f"resonote_tts_{os.getpid()}.mp3"
        try:
            _synthesize(text, tmp)
            if tmp.exists():
                play_mp3(tmp)
        except Exception as e:  # noqa: BLE001
            log.warning("TTS error: %s", e)

    threading.Thread(target=run, daemon=True).start()


def shutdown() -> None:
    stop()