# Spotify → Discord Voice — Full Setup Guide

This guide documents everything needed to rebuild the Spotify audio piping stack from scratch after a full restart.

## Architecture Overview

```
Chrome (Spotify noVNC) → PulseAudio virtual_speaker → parecord (monitor) → _PulseSource → Discord Voice (Opus)
```

**Two repos involved:**
- `Sidewalks-bot/discord_gateway` — the Discord bot (gateway_slash.py), HTTP router, bridges, startup.sh
- `Sidewalks-bot/bot_voice` — music_bridge.py, spotcast supervisor, noVNC webroot

## What's on GitHub

### discord_gateway repo (main branch)
| File | Purpose |
|------|---------|
| `gateway_slash.py` | Discord bot entry point. Loads all bridges (terminal, menu, music) |
| `gateway_http.py` | FastAPI :8080 HTTP router for namespace routing |
| `startup.sh` | **Idempotent restart script** — brings up the entire stack in order |
| `cheeky_bridge.py` | Cheeky AI chat bridge |
| `menu_bridge.py` | Button-based menu navigation |
| `terminal_bridge.py` | Terminal viewer integration |
| `discord_gateway/` | Core gateway library (api, router, models, gateway) |

### bot_voice repo (main branch)
| File | Purpose |
|------|---------|
| `music_bridge.py` | `/music` slash commands (play/spot/q/skip/stop/pause/resume/leave) |
| `spotcast/supervisor.sh` | noVNC browser stack supervisor (Xvfb + x11vnc + Chrome + websockify + PulseAudio) |
| `spotcast/webroot/` | Full noVNC client files (app, core, utils, vendor, vnc.html) |
| `voice_player/` | Standalone voice player (older, pre-bridge approach) |

## What's NOT on GitHub (lives in /agent_home persistent storage)

These survive sandbox restarts but are NOT in git — they're either too large, secrets, or installed packages:

### Secrets (NEVER commit)
- `/agent_home/discord_gateway/.env` — DISCORD_TOKEN, DISCORD_PROXY, PERMITTED_USERS, etc.
- `/agent_home/.git-credentials` — GitHub PAT
- `/agent_home/data/tskey.txt` — Tailscale auth key

### Installed packages (apt-get, as root)
```bash
apt-get update && apt-get install -y pulseaudio pulseaudio-utils
```

### Python venv at /agent_home/discord_gateway/.venv/
```bash
pip install discord.py PyNaCl yt-dlp aiohttp httpx fastapi uvicorn websockify python-dotenv
```

### Static binaries in /agent_home/.local/ (307MB — X11 + VNC stack)
- `/agent_home/.local/usr/bin/Xvfb` — virtual framebuffer
- `/agent_home/.local/usr/bin/x11vnc` — VNC server
- `/agent_home/.local/usr/share/X11/xkb/` — X11 keyboard data (6MB)
- These were extracted from Debian packages manually (not in dpkg database)

### Playwright Chromium (656MB)
- `/agent_home/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`
- Installed via `pip install playwright && playwright install chromium`

### Other binaries
- `/agent_home/bin/ffmpeg` — symlink to imageio-ffmpeg's bundled static ffmpeg v7.0.2
- `/agent_home/bin/yt-dlp` — symlink to venv's yt-dlp
- `/agent_home/lib/libopus.so.0` — Opus codec for Discord voice encoding
- `/agent_home/tailscale_1.102.3_amd64/` — Tailscale daemon (userspace networking mode)

## Startup Sequence (after restart)

Just run:
```bash
/agent_home/startup.sh
```

This idempotent script brings up everything in order:
1. **Tailscale** — userspace networking, SOCKS5 :1055, HTTP proxy :1056, exit node = sideways-desktop (100.67.18.0)
2. **gateway_http** — FastAPI :8080 namespace router
3. **todo_service** — FastAPI :9001 todo app
4. **sidewalk_site** — web server :8083
5. **PulseAudio** — audio daemon + `virtual_speaker` null sink + monitor source
6. **gateway_slash** — Discord bot (Sidewalk#8120), launched with `PULSE_SERVER` env so music_bridge can find PulseAudio
7. **spotcast** — noVNC Spotify browser supervisor (Xvfb :99 → x11vnc :5999 → Chrome → websockify :6080)

### Port map
| Port | Service |
|------|---------|
| 1055 | Tailscale SOCKS5 proxy |
| 1056 | Tailscale HTTP CONNECT proxy |
| 8080 | gateway_http (FastAPI router) |
| 9001 | todo_service |
| 8083 | sidewalk_site |
| 6080 | spotcast noVNC web (HTTP → WebSocket) |
| 5999 | x11vnc raw VNC (localhost only) |

### Verification
```bash
# Check all ports
for p in 1055 1056 8080 9001 8083 6080 5999; do
  (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo ":$p LISTEN" || echo ":$p DOWN"
done

# Check bot online
tail -5 /agent_home/gateway_slash.log  # should show "Gateway online: Sidewalk#8120"

# Check PulseAudio
pactl list short sinks  # should show virtual_speaker
pactl list short clients  # should show chrome

# Check PID budget (sandbox limit: 256)
cat /sys/fs/cgroup/pids.current  # should be < 100
```

## Using /music spot

1. Open the noVNC Spotify browser: `http://<tailscale-ip>:6080/vnc.html?autoconnect=true&path=websockify&resize=remote&reconnect=true`
2. Log into Spotify and play a song
3. In Discord, join a voice channel
4. Run `/music spot` — the bot joins and pipes the live Spotify audio into voice
5. `/music stop` to stop

## Key Technical Decisions

- **Chrome `--single-process`**: Sandbox PID limit is 256. Normal Chrome spawns ~200 processes. `--single-process --disable-extensions --disable-plugins` cuts it to ~15 PIDs.
- **PulseAudio null sink**: Chrome outputs to `virtual_speaker` (a null sink), we capture from `virtual_speaker.monitor` via `parecord`. This is standard audio routing, no DRM circumvention.
- **Tailscale proxy**: Discord blocks the sandbox IP (Cloudflare 1010). Traffic routes through the user's home PC via Tailscale exit node.
- **Daemons as `setsid nohup`**: Processes must be reparented to init (PID 1) to survive across tool calls. Direct subprocess children get killed when the tool call ends.

## If /agent_home/.local/ is lost (X11/VNC stack)

The .local directory is 307MB and lives in persistent storage. If lost, it needs to be reinstalled:
```bash
# Download and extract the Debian packages for Xvfb and x11vnc
apt-get download xvfb x11vnc xserver-xorg-core xserver-common libxfont2
# Extract to /agent_home/.local/
for deb in *.deb; do dpkg-deb -x "$deb" /agent_home/.local/; done
# Also need X11 xkb data
apt-get download xkb-data && dpkg-deb -x xkb-data*.deb /agent_home/.local/
```

## If Playwright Chromium is lost

```bash
pip install playwright
playwright install chromium
# Verify path:
ls /agent_home/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
```
