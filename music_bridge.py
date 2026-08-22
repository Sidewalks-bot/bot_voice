"""Music player integration for the Sidewalk slash gateway.

Gives Sidewalk #8120 the ability to join a voice channel and play music —
the same core experience as JMusicBot, but built natively into the one
Python bot (single token, single connection). Backed by ffmpeg + yt-dlp +
Discord voice (Opus), i.e. the standard LavaPlayer-equivalent stack.

Implements a small queue + playback engine sharing a single ffmpeg AudioSource
per track, with play / queue / skip / stop / pause / resume / disconnect and
nowplaying. Engine is guild-scoped so multiple guilds can play independently.

Also supports /music spot — pipes audio from the Spotify noVNC browser
(via PulseAudio null-sink monitor) directly into Discord voice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess as _sp
import time
from typing import Optional

import discord
from discord import app_commands

LOGGER = logging.getLogger("music_bridge")

FFMPEG = os.getenv("MUSIC_FFMPEG", "/agent_home/bin/ffmpeg")
YTDLP = os.getenv("MUSIC_YTDLP", "/agent_home/bin/yt-dlp")
LIBOPUS = os.getenv("MUSIC_LIBOPUS", "/agent_home/lib/libopus.so.0")
# Auto-detect PulseAudio server socket if not in env
def _detect_pulse_server() -> str:
    srv = os.getenv("PULSE_SERVER", "")
    if srv:
        return srv
    import glob
    socks = sorted(glob.glob("/tmp/pulse-*/native"))
    return socks[0] if socks else ""
PULSE_SERVER = _detect_pulse_server()

# Prefer this single OPUS location if present; else rely on system discovery.
_CANDIDATE_OPUS = LIBOPUS

# Regex for a direct YouTube URL / playlist to avoid resolving via search.
_YT_RE = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/.+", re.I)
# Spotify URL regex — we resolve via oEmbed then search YouTube.
_SPOTIFY_RE = re.compile(
    r"(https?://)?(www\.)?open\.spotify\.com/(track|album|playlist)/.+", re.I)


def _ensure_opus() -> None:
    if not hasattr(discord.opus, "_IS_LOADED") or not _loaded():
        try:
            if os.path.exists(_CANDIDATE_OPUS):
                discord.opus.load_opus(_CANDIDATE_OPUS)
                LOGGER.info("opus loaded from %s", _CANDIDATE_OPUS)
        except Exception as exc:
            LOGGER.warning("local opus load failed (%s); trying default", exc)


def _loaded() -> bool:
    try:
        return discord.opus.is_loaded()
    except Exception:
        return False


class _AudioTrack:
    __slots__ = ("title", "url", "duration", "requester", "started_at")

    def __init__(self, title: str, url: str, duration: float, requester: int):
        self.title = title
        self.url = url
        self.duration = duration
        self.requester = requester
        self.started_at: Optional[float] = None


class _PulseSource(discord.AudioSource):
    """AudioSource that captures from a PulseAudio monitor source.

    Spawns ``parecord`` reading raw s16le 48kHz stereo PCM from the
    virtual_speaker.monitor and feeds it to Discord voice in 20ms chunks.
    """

    # Discord expects 20ms frames: 48000 * 2ch * 2bytes * 0.020 = 3840 bytes
    _CHUNK = 3840

    def __init__(self, device: str = "virtual_speaker.monitor"):
        self._device = device
        self._proc: Optional[_sp.Popen] = None
        self._env = dict(os.environ)
        if PULSE_SERVER:
            self._env["PULSE_SERVER"] = PULSE_SERVER

    def _start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._proc = _sp.Popen(
            [
                "parecord",
                "--device=" + self._device,
                "--file-format=raw",
                "--format=s16le",
                "--rate=48000",
                "--channels=2",
            ],
            stdout=_sp.PIPE,
            stderr=_sp.DEVNULL,
            env=self._env,
        )
        LOGGER.info("PulseSource started: parecord pid=%s device=%s",
                     self._proc.pid, self._device)

    def read(self) -> Optional[bytes]:
        if not self._proc or self._proc.poll() is not None:
            self._start()
        buf = b""
        remaining = self._CHUNK
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                # Process died — restart and pad
                LOGGER.warning("parecord stdout EOF, restarting")
                self._start()
                chunk = self._proc.stdout.read(remaining)
                if not chunk:
                    return b"\x00" * self._CHUNK
            buf += chunk
            remaining -= len(chunk)
        return buf

    def cleanup(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
            LOGGER.info("PulseSource cleaned up")

    def is_opus(self) -> bool:
        return False


class _GuildPlayer:
    """Per-guild queue + playback engine backed by an ffmpeg stream."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: list[_AudioTrack] = []
        self.current: Optional[_AudioTrack] = None
        self.voice: Optional[discord.VoiceClient] = None
        self._task: Optional[asyncio.Task] = None
        self._source = None  # FFmpegPCMAudio or _PulseSource
        self.paused = False
        self.volume = 1.0
        self.spot_source: Optional[_PulseSource] = None  # Spotify live capture
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def is_alone(self) -> bool:
        if not self.voice or not self.voice.channel:
            return True
        members = [m for m in self.voice.channel.members
                   if not m.bot and not m.voice.self_deaf]
        return len(members) <= 1

    async def play(self, track: _AudioTrack) -> None:
        self.current = track
        self.current.started_at = time.time()
        if not self.voice:
            return
        low = track.url.lower()
        _ext = (".mp3",".m4a",".ogg",".wav",".flac",".opus",".aac")
        if low.startswith(("http://","https://")) and low.endswith(_ext):
            before_opt = ""
        else:
            before_opt = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        self._source = discord.FFmpegPCMAudio(
            track.url,
            before_options=before_opt,
            options="-vn -af volume=%f" % self.volume,
            executable=FFMPEG,
        )
        self.loop = asyncio.get_running_loop()
        self.voice.play(
            self._source,
            after=lambda e: (self.loop.call_soon_threadsafe(
                asyncio.ensure_future, self._next()) if e is None else None))

    async def _next(self) -> None:
        if self.queue:
            track = self.queue.pop(0)
            await self.play(track)
            LOGGER.info("now playing (next): %s", track.title)
        else:
            self.current = None
            self._source = None
            LOGGER.info("queue empty for guild %s", self.guild_id)

    async def play_spot(self) -> None:
        """Start piping Spotify browser audio (PulseAudio monitor) to voice."""
        if not self.voice:
            return
        # Stop any current track
        if self.voice.is_playing():
            self.voice.stop()
        if self.spot_source:
            self.spot_source.cleanup()
        self.spot_source = _PulseSource()
        self.spot_source._start()
        self.loop = asyncio.get_running_loop()
        self.voice.play(
            self.spot_source,
            after=lambda e: LOGGER.info("spot source ended: %s", e))
        self.current = _AudioTrack(
            "Spotify Browser (live)", "spot://live", 0, 0)
        LOGGER.info("spot playback started for guild %s", self.guild_id)

    def stop_spot(self) -> None:
        if self.spot_source:
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            self.spot_source.cleanup()
            self.spot_source = None
            LOGGER.info("spot playback stopped for guild %s", self.guild_id)
        self.current = None


class MusicCommands(app_commands.Group):
    """``/music`` — play and control music in a voice channel (JMusicBot-style)."""

    def __init__(self) -> None:
        super().__init__(name="music", description="Play music in a voice channel")
        self._players: dict[int, _GuildPlayer] = {}

    # ---- helpers ----
    def _player(self, guild_id: int) -> _GuildPlayer:
        return self._players.setdefault(guild_id, _GuildPlayer(guild_id))

    _AUDIO_EXT = (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".opus", ".aac")

    async def _resolve(self, query: str):
        """Return (title, url, duration) for a query/link using yt-dlp."""
        low = query.lower()
        if low.startswith(("http://", "https://")) and low.endswith(self._AUDIO_EXT):
            title = low.rsplit("/", 1)[-1] or "audio"
            return title, query, 0.0
        if _SPOTIFY_RE.match(query):
            import json as _json, urllib.request as _url
            try:
                req = _url.Request(
                    f"https://open.spotify.com/oembed?url={query}",
                    headers={"User-Agent": "Mozilla/5.0"})
                with _url.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read().decode())
                title = data.get("title", "")
                if title:
                    LOGGER.info("spotify -> youtube search: %s", title)
                    target = f"ytsearch1:{title}"
                else:
                    return None
            except Exception as exc:
                LOGGER.warning("spotify oembed failed: %s", exc)
                return None
        elif _YT_RE.match(query):
            target = query
        else:
            target = f"ytsearch1:{query}"
        proc = await asyncio.create_subprocess_exec(
            YTDLP, "--no-warnings", "-j", "--no-playlist", "--default-search", "auto",
            target, stdout=_sp.PIPE, stderr=_sp.DEVNULL)
        out, _ = await proc.communicate()
        line = (out or b"").decode("utf-8", "ignore").strip()
        if not line:
            return None
        import json
        try:
            data = json.loads(line)
        except Exception:
            return None
        title = data.get("title")
        url = data.get("webpage_url") or data.get("url")
        duration = float(data.get("duration") or 0)
        if not title or not url:
            return None
        return title, url, duration

    async def _connect(self, inter: discord.Interaction) -> bool:
        """Join the user's voice channel when possible."""
        if not inter.user.voice or not inter.user.voice.channel:
            await inter.followup.send("You must be in a voice channel first.")
            return False
        channel = inter.user.voice.channel
        player = self._player(inter.guild_id)
        if player.voice and player.voice.is_connected():
            return True
        try:
            player.voice = await channel.connect(timeout=20, self_deaf=False)
        except Exception as exc:
            LOGGER.exception("voice connect failed")
            await inter.followup.send(f"Could not join voice: {exc}")
            return False
        return True

    # ---- commands ----
    @app_commands.command(name="play", description="Play a song or add it to the queue")
    @app_commands.describe(query="Song name or YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()
        _ensure_opus()
        if not await self._connect(interaction):
            return
        player = self._player(interaction.guild_id)
        # Stop spot if running
        if player.spot_source:
            player.stop_spot()
        found = await self._resolve(query)
        if not found:
            await interaction.followup.send("I couldn't find that song.")
            return
        title, url, duration = found
        track = _AudioTrack(title, url, duration, interaction.user.id)
        if player.current is None:
            await player.play(track)
            await interaction.followup.send(f"▶ Now playing: **{title}**")
        else:
            player.queue.append(track)
            await interaction.followup.send(
                f"➕ Queued **{title}** (position {len(player.queue)})")

    @app_commands.command(name="spot", description="Pipe Spotify browser audio into voice (live capture)")
    async def spot(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        _ensure_opus()
        if not await self._connect(interaction):
            return
        player = self._player(interaction.guild_id)
        await player.play_spot()
        await interaction.followup.send(
            "🔴 Streaming from Spotify browser (PulseAudio monitor).\n"
            "Play something in the noVNC Spotify window and it'll come through here.\n"
            "Use `/music stop` to end.")

    @app_commands.command(name="q", description="Show the current queue")
    async def q(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        if not player.current:
            await interaction.followup.send("Nothing is playing.")
            return
        lines = [f"▶ **{player.current.title}**"]
        for i, t in enumerate(player.queue, 1):
            lines.append(f"{i}. {t.title}")
        await interaction.followup.send("\n".join(lines) if lines else "(empty)")

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        if player.spot_source:
            player.stop_spot()
            await interaction.followup.send("⏭ Stopped Spotify live capture.")
            return
        if not player.voice or not player.voice.is_playing():
            await interaction.followup.send("Nothing playing to skip.")
            return
        player.voice.stop()
        await interaction.followup.send("⏭ Skipped.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        player.stop_spot()
        if player.voice:
            player.voice.stop()
        player.queue.clear()
        player.current = None
        await interaction.followup.send("⏹ Stopped and cleared the queue.")

    @app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        if player.voice and player.voice.is_playing():
            player.voice.pause()
            player.paused = True
            await interaction.followup.send("⏸ Paused.")
        else:
            await interaction.followup.send("Nothing to pause.")

    @app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        if player.voice and player.voice.is_paused():
            player.voice.resume()
            player.paused = False
            await interaction.followup.send("▶ Resumed.")
        else:
            await interaction.followup.send("Nothing to resume.")

    @app_commands.command(name="leave", description="Leave the voice channel and clear queue")
    async def leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        player = self._player(interaction.guild_id)
        player.stop_spot()
        if player.voice:
            await player.voice.disconnect()
        player.voice = None
        player.queue.clear()
        player.current = None
        await interaction.followup.send("👋 Left the voice channel.")


def install_music(client) -> None:
    """Attach the /music command group to the slash client."""
    _ensure_opus()
    group = MusicCommands()
    client.music = group
    client.tree.add_command(group)
    LOGGER.info("music installed: /music play|spot|q|skip|stop|pause|resume|leave")
