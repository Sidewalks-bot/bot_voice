#!/bin/bash
# noVNC browser-stack supervisor — all paths in /agent_home (persistent)
# Includes PulseAudio for audio capture from Chrome -> Discord voice
set -uo pipefail
L=/agent_home/spotcast
LIB=/agent_home/.local/usr/lib/x86_64-linux-gnu:/agent_home/.local/lib/x86_64-linux-gnu
export DISPLAY=:99
export HOME=/agent_home
export LD_LIBRARY_PATH=$LIB
export PATH=/agent_home/.local/usr/bin:$PATH
CHROME_BIN=/agent_home/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome
XVFB_BIN=/agent_home/.local/usr/bin/Xvfb
VNC_BIN=/agent_home/.local/usr/bin/x11vnc
NOVNC_WEB=/agent_home/spotcast/webroot
WS_BIN=/agent_home/discord_gateway/.venv/bin/websockify
XKB_DIR=/agent_home/.local/usr/share/X11/xkb
mkdir -p "$L"

echo "[supervisor] start $(date)" > "$L/supervisor.log"

# --- PulseAudio for audio capture ---
start_pulse() {
  if ! pactl info >/dev/null 2>&1; then
    echo "[supervisor] starting pulseaudio" >> "$L/supervisor.log"
    # Set low-latency daemon defaults
    sed -i 's/; default-fragments = 4/default-fragments = 2/' /etc/pulse/daemon.conf 2>/dev/null
    sed -i 's/; default-fragment-size-msec = 25/default-fragment-size-msec = 10/' /etc/pulse/daemon.conf 2>/dev/null
    pulseaudio -D --log-target=file:"$L/pulse.log" 2>/dev/null
    sleep 2
  fi
  # Set up virtual speaker sink + monitor
  PULSE_SOCK=$(ls -d /tmp/pulse-*/native 2>/dev/null | head -1)
  if [ -n "$PULSE_SOCK" ]; then
    export PULSE_SERVER="$PULSE_SOCK"
    echo "[supervisor] pulse_server=$PULSE_SOCK" >> "$L/supervisor.log"
    # Load null sink if not already loaded
    if ! pactl list short sinks 2>/dev/null | grep -q virtual_speaker; then
      pactl load-module module-null-sink sink_name=virtual_speaker \
        sink_properties=device.description=VirtualSpeaker 2>/dev/null
      echo "[supervisor] virtual_speaker sink loaded" >> "$L/supervisor.log"
    fi
    pactl set-default-sink virtual_speaker 2>/dev/null
    pactl set-default-source virtual_speaker.monitor 2>/dev/null
  else
    echo "[supervisor] WARNING: no pulse socket found" >> "$L/supervisor.log"
  fi
}

start_pulse

# Create xkbcomp wrapper at /usr/bin so Xvfb can find it
cat > /usr/bin/xkbcomp << 'WRAPPER'
#!/bin/bash
export LD_LIBRARY_PATH=/agent_home/.local/usr/lib/x86_64-linux-gnu:/agent_home/.local/lib/x86_64-linux-gnu
exec /agent_home/.local/usr/bin/xkbcomp "$@"
WRAPPER
chmod +x /usr/bin/xkbcomp

# Clean stale X locks
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null

start_xvfb() {
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null
  "$XVFB_BIN" :99 -screen 0 1400x900x24 -nolisten tcp -xkbdir "$XKB_DIR" > "$L/xvfb.log" 2>&1 &
  XVFB=$!
  sleep 2
}

start_vnc() {
  "$VNC_BIN" -display :99 -rfbport 5999 -localhost -nopw -shared -bg -o "$L/vnc.log" 2>&1
  sleep 1
}

start_chrome() {
  nice -n 10 "$CHROME_BIN" --no-sandbox --disable-gpu --disable-gpu-compositing \
    --disable-dev-shm-usage --no-first-run \
    --no-default-browser-check --disable-session-crashed-bubble --disable-infobars \
    --disable-component-update --disable-background-networking --disable-features=Vulkan \
    --use-gl=swiftshader --window-size=1400,900 --user-data-dir="$L/chrome-profile" \
    --single-process --disable-extensions --disable-plugins \
    --disable-animations --disable-smooth-scrolling --disable-paint-holding \
    --disable-image-animation --blink-settings=reduceMotion=true \
    https://open.spotify.com/ > "$L/chromium.log" 2>&1 &
  CHROME=$!
}

start_ws() {
  "$WS_BIN" --web="$NOVNC_WEB" 6080 localhost:5999 > "$L/ws.log" 2>&1 &
  WS=$!
}

# Launch all
start_xvfb
echo "[supervisor] xvfb=$XVFB" >> "$L/supervisor.log"
start_vnc
echo "[supervisor] x11vnc launched" >> "$L/supervisor.log"
start_chrome
echo "[supervisor] chrome=$CHROME" >> "$L/supervisor.log"
start_ws
echo "[supervisor] ws=$WS all-launched" >> "$L/supervisor.log"

# reap loop — properly wait() on dead children to prevent zombies
trap 'kill $XVFB $CHROME $WS 2>/dev/null; pkill -f "x11vnc.*:99" 2>/dev/null; exit 0' TERM INT
while :; do
  # Reap any zombie children
  while wait -n 2>/dev/null; do :; done

  for pid in $XVFB $WS $CHROME; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[supervisor] $pid died, restarting $(date)" >> "$L/supervisor.log"
      case "$pid" in
        "$XVFB") start_xvfb ;;
        "$WS") start_ws ;;
        "$CHROME") start_chrome ;;
      esac
    fi
  done
  # Also check x11vnc
  if ! pgrep -f "x11vnc.*:99" >/dev/null 2>&1; then
    echo "[supervisor] x11vnc died, restarting $(date)" >> "$L/supervisor.log"
    start_vnc
  fi
  sleep 5
done
