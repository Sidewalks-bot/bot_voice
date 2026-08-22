"""Audio source resolution.

Two backends:
  * "yt"  — yt-dlp resolves a query/URL to a direct stream; ffmpeg decodes.
  * "web" — Playwright opens a web-player page and captures its audio tab,
            then ffmpeg reads the captured stream. The "virtual audio device".

Both produce a raw stream that the player feeds to discord.py as PCM.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "/agent_home/bin/ffmpeg"


@dataclass
class ResolvedSource:
    title: str
    url: str
    ffmpeg_args: list[str] = field(default_factory=list)


class SourceError(Exception):
    pass


def _looks_like_url(text: str) -> bool:
    return "://" in text or text.startswith("www.") or ".youtube." in text or ".be/" in text


def _query(q: str) -> str:
    """Return a yt-dlp-usable query (search terms become a ytsearch1)."""
    q = q.strip()
    if _looks_like_url(q):
        return q
    return "ytsearch1:" + q


def _pick_url(info: dict) -> str | None:
    url = info.get("url")
    if url:
        return url
    reqs = info.get("requested_formats") or []
    if reqs:
        return reqs[0].get("url")
    fmts = info.get("formats") or []
    for f in fmts:
        if f.get("url"):
            return f["url"]
    return None


def _resolve_yt(query_or_url: str) -> ResolvedSource:
    try:
        import yt_dlp
    except ImportError as exc:
        raise SourceError("yt-dlp not installed; pip install -r requirements.txt") from exc

    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    query = _query(query_or_url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if info is None:
            raise SourceError("could not resolve that query")
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        url = _pick_url(info)
        if not url:
            raise SourceError("no playable stream found")
        title = info.get("title") or info.get("id") or query_or_url
    return ResolvedSource(title=title, url=url)


def _resolve_web(page_url: str) -> ResolvedSource:
    """Prepare a web-player capture backend. See README re: DRM limits."""
    if not shutil.which("chromium") and not shutil.which("google-chrome"):
        # playwright ships its own chromium via `playwright install`
        pass
    return ResolvedSource(title=f"Web player: {page_url}", url=page_url)


def resolve(query: str, backend: str = "yt") -> ResolvedSource:
    backend = (backend or "yt").lower()
    if backend in ("yt", "youtube"):
        return _resolve_yt(query)
    if backend in ("web", "browser"):
        return _resolve_web(query)
    raise SourceError(f"unknown backend: {backend}")
