"""Voice DJ state machine: owns the discord voice client connection and queue."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Optional

import discord

from . import pcm
from .sources import ResolvedSource, SourceError

LOGGER = logging.getLogger(__name__)


class PlayerError(Exception):
    pass


class VoicePlayer:
    """One voice connection with a queue, running its own event loop.

    The feature app owns a single VoicePlayer shared across the bot lifetime.
    discord.py's voice client does its own asyncio, so we require an event loop
    reference and call into it via asyncio.run_coroutine_threadsafe when needed.
    """

    def __init__(self, client: discord.Client, loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop
        self.queue: deque[ResolvedSource] = deque()
        self.current: Optional[ResolvedSource] = None
        self.vc: Optional[discord.VoiceClient] = None
        self.voice_channel_id: Optional[int] = None
        self.backend = "yt"
        self._playing = False

    # --- connection ---------------------------------------------------------
    async def join(self, channel_id: Optional[int] = None) -> str:
        target = channel_id or self.voice_channel_id
        vc = self.vc
        if vc and vc.is_connected():
            if target and vc.channel.id != target:
                await vc.move_to(self._get_channel(target))
            return f"already in {vc.channel.name} (id {vc.channel.id})"
        if target is None:
            raise PlayerError("no voice channel given — join one first or set VOICE_CHANNEL")
        ch = self._get_channel(target)
        if ch is None:
            raise PlayerError("could not find that voice channel")
        self.vc = await ch.connect()
        self.voice_channel_id = ch.id
        return f"joined {ch.name} (id {ch.id})"

    async def leave(self) -> str:
        if self.vc and self.vc.is_connected():
            name = self.vc.channel.name
            await self.vc.disconnect()
            self.vc = None
            self.voice_channel_id = None
            self.queue.clear()
            self.current = None
            self._playing = False
            return f"left {name}"
        return "not in a voice channel"

    def _get_channel(self, channel_id: int) -> Optional[discord.VoiceChannel]:
        for guild in self.client.guilds:
            for ch in guild.channels:
                if ch.id == channel_id and isinstance(ch, discord.VoiceChannel):
                    return ch
        return None

    # --- playback -----------------------------------------------------------
    async def play(self, source: ResolvedSource) -> str:
        self.queue.append(source)
        if not self._playing:
            await self._start()
        return f"queued **{source.title}**"

    async def _start(self) -> None:
        if not self.vc or not self.vc.is_connected():
            raise PlayerError("not in a voice channel — /voice join first")
        while self.queue:
            self.current = self.queue.popleft()
            self._playing = True
            LOGGER.info("playing: %s", self.current.title)
            audio = pcm.StreamAudio(self.current)
            self.vc.play(audio, after=lambda e: self.loop.call_soon_threadsafe(self._next, e))
            # wait for this one to finish
            while self.vc.is_playing():
                await asyncio.sleep(0.2)
            audio.cleanup()
            self.current = None
        self._playing = False

    def _next(self, err: Optional[Exception]) -> None:
        # discord's after callback — nothing to do, _start's loop advances.
        if err:
            LOGGER.warning("playback error: %s", err)

    # --- controls -----------------------------------------------------------
    async def pause(self) -> str:
        if self.vc and self.vc.is_playing():
            self.vc.pause()
            return "paused"
        return "nothing playing"

    async def resume(self) -> str:
        if self.vc and self.vc.is_paused():
            self.vc.resume()
            return "resumed"
        if self.vc and self.vc.is_playing():
            return "already playing"
        raise PlayerError("nothing paused to resume")

    async def skip(self) -> str:
        if self.vc and self.vc.is_playing():
            self.vc.stop()
            return "skipped"
        if self.queue:
            return f"skipped (still {len(self.queue)} queued)"
        return "nothing to skip"

    def queue_str(self) -> str:
        lines = [f"{i+1}. **{q.title}**" for i, q in enumerate(self.queue)]
        now = f"now playing: **{self.current.title}**" if self.current else "nothing playing"
        if lines:
            return now + "\n" + "\n".join(lines)
        return now

    def set_backend(self, backend: str) -> str:
        self.backend = backend
        return f"audio backend -> {backend}"
