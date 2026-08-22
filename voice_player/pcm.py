"""ffmpeg -> PCM streaming for discord.py voice.

We own the ffmpeg subprocess and expose read() bytes satisfying discord.AudioSource.
Output is 48kHz, stereo, signed-16-bit PCM (what discord voice channels need).
"""
from __future__ import annotations

import array
import shutil
import subprocess
from typing import Optional

import discord

from .sources import FFMPEG, ResolvedSource

CHANNELS = 2
SAMPLE_RATE = 48000
FRAME_MS = 20


def build_command(source: ResolvedSource) -> list[str]:
    cmd = [FFMPEG]
    cmd += source.ffmpeg_args
    cmd += ["-vn", "-i", source.url]
    cmd += ["-f", "s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
            "-acodec", "pcm_s16le", "-"]
    return cmd


class StreamAudio(discord.PCMVolumeTransformer):
    """PCM source that owns an ffmpeg process streaming 48k stereo PCM."""

    def __init__(self, source: ResolvedSource, volume: float = 0.6) -> None:
        # We implement read() ourselves, so pass a dummy inner source.
        self.source = source
        self.volume = max(0.0, min(2.0, volume))
        self.proc = subprocess.Popen(
            args=build_command(source),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._samples = int(SAMPLE_RATE * FRAME_MS / 1000.0) * CHANNELS

    def read(self) -> bytes:
        if self.proc.stdout is None or self.proc.poll() is not None:
            return b""
        data = self.proc.stdout.read(self._samples * 2)
        if not data:
            return b""
        if abs(self.volume - 1.0) < 1e-6:
            return data
        vals = array.array("h")
        vals.frombytes(data[:len(data) - len(data) % 2])
        for i in range(len(vals)):
            vals[i] = max(-32768, min(32767, int(vals[i] * self.volume)))
        return vals.tobytes()

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass
