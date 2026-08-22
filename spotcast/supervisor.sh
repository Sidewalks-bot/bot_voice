#!/bin/bash
# Robust noVNC browser-stack supervisor for the Spotify visual browser.
# Runs: Xvnc (X+VNC on :99/5999) + Chromium (Spotify) + websockify (6080, noVNC web + proxy to 5999).
# Usage: ./spotcast/supervisor.sh   (change L to a writable runtime dir)
set -uo pipefail
L=${SPOTCAST_DIR:-/agent_home/spotcast}   # writable runtime dir (profiles, logs)
export DISPLAY=:99
export HOME=/root
CHROME_BIN=/usr/bin/chromium
mkdir -p "$L"

echo "[supervisor] start $(date)" > "$L/supervisor.log"
# Xvnc: X server + VNC RFB server on display :99 listening on 5999
Xvnc :99 -geometry 1400x900 -depth 24 -rfbport 5999 -localhost -SecurityTypes none -AlwaysShared > "$L/xvnc.log" 2>&1 &
XVNC=$!

sleep 2
echo "[supervisor] xvnc=$XVNC" >> "$L/supervisor.log"

# one chromium session on :99 (Spotify web player)
"$CHROME_BIN" --no-sandbox --disable-gpu --disable-gpu-compositing \
  --disable-dev-shm-usage --renderer-process-limit=1 --no-first-run \
  --no-default-browser-check --disable-session-crashed-bubble --disable-infobars \
  --disable-component-update --disable-background-networking --disable-features=Vulkan \
  --use-gl=swiftshader --window-size=1400,900 --user-data-dir="$L/chrome-profile" \
  https://open.spotify.com/ > "$L/chromium.log" 2>&1 &
CHROME=$!
echo "[supervisor] chrome=$CHROME" >> "$L/supervisor.log"

# websockify: noVNC web + proxy 6080 -> 5999
/usr/bin/websockify --web=/usr/share/novnc 6080 localhost:5999 > "$L/ws.log" 2>&1 &
WS=$!
echo "[supervisor] ws=$WS all-launched" >> "$L/supervisor.log"

# reap loop; restart only genuinely dead critical components (no tight loop)
trap 'kill $XVNC $CHROME $WS 2>/dev/null; exit 0' TERM INT
while :; do
  for pid in $XVNC $WS $CHROME; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[supervisor] $pid died, restarting $(date)" >> "$L/supervisor.log"
      case "$pid" in
        "$XVNC") Xvnc :99 -geometry 1400x900 -depth 24 -rfbport 5999 -localhost -SecurityTypes none -AlwaysShared > "$L/xvnc.log" 2>&1 & XVNC=$!; sleep 2 ;;
        "$WS") /usr/bin/websockify --web=/usr/share/novnc 6080 localhost:5999 > "$L/ws.log" 2>&1 & WS=$! ;;
        "$CHROME") "$CHROME_BIN" --no-sandbox --disable-gpu --disable-gpu-compositing \
              --disable-dev-shm-usage --renderer-process-limit=1 --no-first-run \
              --no-default-browser-check --disable-session-crashed-bubble --disable-infobars \
              --disable-component-update --disable-background-networking --disable-features=Vulkan \
              --use-gl=swiftshader --window-size=1400,900 --user-data-dir="$L/chrome-profile" \
              https://open.spotify.com/ > "$L/chromium.log" 2>&1 & CHROME=$! ;;
      esac
    fi
  done
  sleep 5
done
