"""
Spotify playback via OS-level audio capture.

Rationale: Spotify's web player is DRM-encrypted (EME/Widevine), so Chromium
cannot capture its audio *inside the page*. Workaround: play the Spotify web
player to a virtual sound device (PulseAudio "null sink") and capture that
device at the OS level — DRM is not applied to the sink output. The captured
stream is fed to ffmpeg -> PCM -> Discord voice, exactly like the "yt" backend.

No real soundcard is required: a PulseAudio null sink is a userspace virtual
device. If PulseAudio is not present we look for other capture hooks and report
a clear error.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from .sources import FFMPEG, ResolvedSource, SourceError

LOGGER = logging.getLogger(__name__)

SINK = "BotVoiceSink"
SPOTIFY_URL = "https://open.spotify.com/"

# Chromium binary candidates (playwright's own install, system, sandbox bins)
_CHROMIUM_CANDIDATES = [
    "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    "/agent_home/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
]


def _find_chromium() -> str | None:
    for cand in _CHROMIUM_CANDIDATES:
        if "*" in cand:
            import glob
            hits = sorted(glob.glob(cand))
            if hits:
                return hits[0]
            continue
        p = shutil.which(cand)
        if p:
            return p
    return None


class SoundDevice:
    """Wraps a pulse null-sink (virtual device) and its capture, or fails."""

    def __init__(self) -> None:
        self.pulse = shutil.which("pulseaudio")
        self.pactl = shutil.which("pactl")
        self.title = "Spotify (OS audio capture)"

    def available(self) -> bool:
        return bool(self.pulse and self.pactl)

    def start(self) -> None:
        if not self.available():
            raise SourceError(
                "PulseAudio is not installed in this environment. Install "
                "`pulseaudio` + `pulseaudio-utils`, then set up a null sink, "
                "or run this feature app on a host that has an audio system."
            )
        # Load the null sink module and monitor source for capture.
        self._run(["pulseaudio", "--start", "--exit-idle-time=-1"], check=False)
        self._run(["pactl", "load-module", "module-null-sink",
                   f"sink_name={SINK}", f"sink_properties=device.description={SINK}"],
                  check=False)
        # Ensure the web player routes audio to the sink below.

    def start_web_player(self) -> None:
        """Launch the Spotify web player in a headed Chromium pointed at the sink.

        We start pulse with the default sink = our null sink so everything
        Chromium plays routes to the virtual device we capture.
        """
        if not self.available():
            raise SourceError("PulseAudio not available")
        # Make our sink the default so Chromium routes audio into it.
        self._run(["pactl", "set-default-sink", SINK], check=False)
        chrome = _find_chromium()
        if not chrome:
            raise SourceError("no chromium found — pip install playwright and run `playwright install chromium`")
        adev = self._find_monitor()
        env = dict(os.environ)
        env["PULSE_SINK"] = SINK
        LOGGER.info("launching Spotify web player on chromium=%s", chrome)
        subprocess.Popen(
            [chrome, "--user-data-dir=/tmp/botvoice-chrome",
             "--no-first-run", "--autoplay-policy=no-user-gesture-required",
             SPOTIFY_URL],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        LOGGER.info("web player open — log in / start playback; capture sink %s", adev)

    def _find_monitor(self) -> str:
        """Return pulse monitor source name for the null sink."""
        out = self._run(["pactl", "list", "short", "sources"], check=False)
        for line in (out or "").splitlines():
            if SINK and ".monitor" in line:
                parts = line.split()
                if parts:
                    return parts[1]
        raise SourceError("could not find the sink monitor — is PulseAudio running?")

    def build_source(self) -> ResolvedSource:
        monitor = self._find_monitor()
        # Capture the monitor via pulse -> ffmpeg pipes PCM (same as yt path).
        src = ResolvedSource(
            title=self.title,
            url="pulse:" + monitor,
            ffmpeg_args=["-f", "pulse", "-i", monitor],
        )
        return src

    def _run(self, cmd, check=True) -> str | None:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if check and r.returncode != 0:
                LOGGER.warning("cmd %s rc=%s err=%s", cmd, r.returncode, r.stderr.strip())
            return r.stdout
        except Exception as exc:
            if check:
                LOGGER.warning("cmd %s failed: %s", cmd, exc)
            return None
