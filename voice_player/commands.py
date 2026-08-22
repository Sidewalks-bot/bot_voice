"""/voice command handlers — bridge between the gateway command path and VoicePlayer."""
from __future__ import annotations

import logging

from . import sources
from .player import PlayerError, VoicePlayer
from .sources import SourceError

LOGGER = logging.getLogger(__name__)

# Seeded lazily by the app once the client exists.
PLAYER: VoicePlayer | None = None


def set_player(player: VoicePlayer) -> None:
    global PLAYER
    PLAYER = player


def handle(command: str, args: dict, _author_id: int | None) -> dict:
    """Return {"ok": bool, "text": str} mirroring the todo_service contract."""
    player = PLAYER
    if player is None:
        return {"ok": False, "text": "voice player not initialised"}
    text = str(args.get("text", "")).strip()

    try:
        if command == "join":
            cid = _chan(args)
            return {"ok": True, "text": _run(player.join(cid))}
        if command == "leave":
            return {"ok": True, "text": _run(player.leave())}
        if command == "play":
            if not text:
                return {"ok": False, "text": "usage: /voice play <song or URL>"}
            src = sources.resolve(text, player.backend)
            return {"ok": True, "text": _run(player.play(src))}
        if command == "pause":
            return {"ok": True, "text": _run(player.pause())}
        if command == "resume":
            return {"ok": True, "text": _run(player.resume())}
        if command == "skip":
            return {"ok": True, "text": _run(player.skip())}
        if command == "queue":
            return {"ok": True, "text": player.queue_str()}
        if command == "now":
            return {"ok": True, "text": player.queue_str()}
        if command == "source":
            if not text:
                return {"ok": False, "text": "usage: /voice source <yt|web>"}
            return {"ok": True, "text": player.set_backend(text)}
        if command == "spotify":
            # open the Spotify web player routed to the capture sink
            from . import spotify
            dev = spotify.SoundDevice()
            if not dev.available():
                return {"ok": False, "text": "Spotify needs PulseAudio (pulseaudio + pulseaudio-utils). Not detected."}
            try:
                dev.start()
                cursor = dev.start_web_player()
                return {"ok": True, "text": "Spotify web player opening — log in & play, then `/voice spot` + `/voice play`."}
            except SourceError as exc:
                return {"ok": False, "text": str(exc)}
        if command == "spot":
            return {"ok": True, "text": _run(player.set_backend("spot")) + " -> now `/voice play` captures the Spotify sink."}
        if command in ("help", ""):
            return {"ok": True, "text": _HELP}
    except (PlayerError, SourceError) as exc:
        return {"ok": False, "text": str(exc)}
    except Exception as exc:  # pragma: no cover
        LOGGER.exception("voice %s failed", command)
        return {"ok": False, "text": f"error: {exc}"}
    return {"ok": False, "text": f"unknown /voice {command}"}


def _chan(args: dict) -> int | None:
    text = str(args.get("text", "")).strip()
    for tok in text.split():
        if tok.isdigit():
            return int(tok)
    return None


def _run(coro) -> str:
    """Run a coroutine on the player's loop and return its result text."""
    import asyncio
    try:
        loop = None
        # player exposes loop
        loop = PLAYER.loop
    except Exception:
        pass
    if loop is None:
        raise RuntimeError("no event loop")
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=15)


_HELP = (
    "**/voice** — voice DJ\n"
    "- `/voice join [channel]`\n"
    "- `/voice play <song/url>`\n"
    "- `/voice pause | resume | skip`\n"
    "- `/voice queue | now`\n"
    "- `/voice source <yt|web|spot>`\n"
    "- `/voice spotify` — open Spotify web player + OS capture\n"
    "- `/voice leave`"
)
