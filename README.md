# bot_voice

A **voice DJ** feature app for the Sidewalk Discord bot. It turns "a webpage with
music on it" into live audio in a Discord voice channel — no OS virtual audio
device needed: discord.py voices streams PCM frames straight into the channel,
and we feed that PCM from ffmpeg-decoded audio (browser-captured or yt-dlp).

## What it does
- `/voice join [channel]` — bot joins your voice channel
- `/voice play <song or URL>` — resolve + queue + play audio into the channel
- `/voice pause` / `/voice resume` — control playback
- `/voice skip` — next in queue
- `/voice queue` — list the queue
- `/voice now` — what's currently playing
- `/voice leave` — disconnect
- `/voice source <web|yt>` — choose the audio backend

## Two audio backends
1. **`yt` (default, most reliable)** — `yt-dlp` resolves a YouTube / generic
   URL to a direct stream, `ffmpeg` decodes it to 48kHz stereo PCM, and
   discord.py plays it. Works without any login.
2. **`web` (browser capture — the "virtual audio device")** — Playwright opens
   a <em>web player</em> page (e.g. a Spotify/YouTube web player URL) in a real
   headed Chromium, captures the tab's audio, and pipes it through ffmpeg into
   the voice channel. Use this when you want to play from a specific website
   that requires login/cookies / a web session.

> Spotify's web player is DRM-encrypted (EME) so Chromium *cannot* capture its
> audio directly for playback outside the browser. The `web` backend works
> great for sites whose audio is not EME-protected (e.g. many radio/YT
> embedded players). For Spotify specifically, the practical path is either the
> `yt` backend (YouTube equivalent) or Spotify Connect device control.

## Install
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# only if you use the web backend:
.venv/bin/python -m playwright install chromium
```

## Configuration (env)
| var | default | meaning |
|-----|---------|---------|
| `DISCORD_TOKEN` | *(required)* | the bot token (from `discord_connect`) |
| `DISCORD_PROXY` | *(optional)* | `http://127.0.0.1:1055` when egressing through the tailnet |
| `VOICE_CHANNEL` | 0 | default channel id to join |
| `GATEWAY_URL` | `http://localhost:8080` | gateway for namespace registration |
| `APP_ID` | `voice` | feature app id |
| `APP_PORT` | `9002` | this app's port |
| `API_KEY` | `dev-key` | registration key |
| `NAMESPACES` | `voice` | namespaces claimed |

## Run
```bash
.venv/bin/python -m voice_player.app
```

## Notes
- Discord voice runs over **UDP**; behind a tailnet exit node this works fine
  because the tailnet tunnels UDP too (verified).
- `PyNaCl` + `ffmpeg` are required for voice. `ffmpeg` can be a static build.
