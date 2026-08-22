"""/voice feature app — Discord voice client + gateway registration.

Runs two things together:
  1. A discord.Client (owns the bot token) so the bot can join voice channels.
  2. A FastAPI app registered with the HTTP gateway under the "voice" namespace,
     so inbound /voice commands from the gateway are handled here.

The discord client runs in a background asyncio loop; FastAPI runs via uvicorn
in the foreground. The gateway forwards command payloads to this app's /command
endpoint, which dispatches to the player.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

import discord

from . import commands
from .player import VoicePlayer

load_dotenv("/agent_home/discord_gateway/.env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("bot_voice")

TOKEN = os.getenv("DISCORD_TOKEN", "")
PROXY = os.getenv("DISCORD_PROXY", "") or None
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")
APP_ID = os.getenv("APP_ID", "voice")
APP_PORT = int(os.getenv("APP_PORT", "9002"))
API_KEY = os.getenv("API_KEY", "dev-key")
NAMESPACES = [n for n in os.getenv("NAMESPACES", "voice").split(",") if n]
DEFAULT_VC = int(os.getenv("VOICE_CHANNEL", "0") or 0)

# --- Discord client ------------------------------------------------------
client: discord.Client | None = None
player: VoicePlayer | None = None


class Bot(discord.Client):
    def __init__(self, proxy=None) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        self._proxy = proxy
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        global player
        LOGGER.info("bot_voice online as %s (user id %s)", self.user, self.user.id)
        player = VoicePlayer(self, self.loop)
        commands.set_player(player)
        if DEFAULT_VC:
            try:
                txt = await player.join(DEFAULT_VC)
                LOGGER.info("joined default voice: %s", txt)
            except Exception as exc:
                LOGGER.warning("default voice join failed: %s", exc)


def start_discord() -> None:
    """Run the discord client in a background thread with its own loop."""
    global client
    if not TOKEN:
        LOGGER.error("DISCORD_TOKEN not set; voice bot disabled")
        return
    loop = asyncio.new_event_loop()
    client = Bot(proxy=PROXY)
    threading.Thread(target=_run_loop, args=(loop,), daemon=True, name="discord-voice").start()


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.start(TOKEN))
    except Exception as exc:  # pragma: no cover
        LOGGER.exception("discord voice client stopped: %s", exc)


# --- FastAPI gateway integration -----------------------------------------
app = FastAPI(title="bot_voice")


class CommandReq(BaseModel):
    op: str = "command"
    namespace: str
    command: str
    args: dict = {}
    author_id: int | None = None
    channel_id: int | None = None
    guild_id: int | None = None


@app.post("/command")
async def command(req: CommandReq) -> dict:
    return commands.handle(req.command, req.args, req.author_id)


@app.get("/health")
async def health() -> dict:
    online = bool(player)
    return {"status": "ok" if online else "degraded", "app": "bot_voice",
            "conn": bool(player and player.vc and player.vc.is_connected())}


def register() -> None:
    try:
        httpx.post(f"{GATEWAY_URL}/register", json={
            "app_id": APP_ID,
            "api_key": API_KEY,
            "namespaces": NAMESPACES,
            "endpoint": os.getenv("APP_PUBLIC_URL", f"http://localhost:{APP_PORT}"),
        }, timeout=5)
    except Exception as exc:
        print("register failed:", exc)


def main() -> None:
    register()
    start_discord()
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT, log_level="info")


if __name__ == "__main__":
    main()
