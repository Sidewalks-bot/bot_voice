# spotcast — Spotify visual browser over noVNC

A tiny headful-browser stack that serves a live **Spotify web player** you can
see and drive from a normal web browser over noVNC + VNC. This is the visual
face for the `voice_player` `spot` backend (OS-level audio capture for
DRM-protected audio): you log into Spotify here, play a song, and the same
session's audio is captured into the Discord voice channel.

## Stack
- **Xvnc** — X server + VNC RFB server on display `:99`, listening on port `5999`
  (`-localhost`, so it only accepts connections via websockify / tailnet).
- **Chromium** (headed) — opens `https://open.spotify.com/` on that display.
- **websockify** — serves the noVNC web UI and proxies `6080 -> 5999`, so the
  VNC stream runs over a plain HTTPS WebSocket (no raw TCP needed).

## Run
```bash
./spotcast/supervisor.sh
```
It launches all three, watches them, and restarts any that die.

## Access
Point a browser (or an HTTPS/WS tunnel / tailnet address) at websockify on
port `6080`. The noVNC client autoconnects:

```
http://<host>:6080/vnc.html?autoconnect=true&resize=remote&reconnect=true&path=websockify
```

`webroot/index.html` embeds that same noVNC client fullscreen.

## Requirements
- `Xvnc` (TigerVNC), `websockify`, `chromium` installed on the host.
- A writable runtime dir for profiles/logs (env `SPOTCAST_DIR`, default
  `/agent_home/spotcast`).

## Config (env)
| var | default | meaning |
|-----|---------|---------|
| `SPOTCAST_DIR` | `/agent_home/spotcast` | writable dir for chrome profile + logs |

> Networking note: the sandbox cannot hairpin to its own Tailscale IP from
> inside; use `127.0.0.1` for in-container verification and the tailnet IP from
> the user's machine.
