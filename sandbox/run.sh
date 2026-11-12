#!/bin/bash
# Dedupearr playground with plain docker (no compose needed; works on Unraid): the real app on the fake disk from tests/fake_disk.py.
#   sandbox/run.sh up        start it (port $SANDBOX_PORT, default 3096)
#   sandbox/run.sh reset     remove and start again from a fresh fake disk (cron this hourly for a public copy)
#   sandbox/run.sh down      remove it
# SANDBOX_PUBLIC=1 puts it on an internal network with no host port: attach your reverse proxy to network $SANDBOX_NAME and point it at $SANDBOX_NAME:3092.
set -eu
IMG="${DEDUPEARR_IMAGE:-ghcr.io/chrizantoz/dedupearr:latest}"; NAME="${SANDBOX_NAME:-dedupearr-sandbox}"; NET="$NAME"; PORT="${SANDBOX_PORT:-3096}"; PUBLIC="${SANDBOX_PUBLIC:-0}"
down(){ docker rm -f "$NAME" >/dev/null 2>&1 || true; }
up(){
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create $([ "$PUBLIC" = "1" ] && echo --internal) "$NET" >/dev/null
  docker run -d --name "$NAME" --network "$NET" $([ "$PUBLIC" = "1" ] || echo -p "$PORT:3092") --restart unless-stopped --memory=256m --pids-limit 128 \
    --tmpfs /data --tmpfs /config -e SANDBOX=1 -e TZ="${TZ:-UTC}" -e SCAN_ROOTS=/data -e MIN_SIZE_MB=1 -e HASH_BUDGET_MB=200 -e RECYCLE_DAYS=30 -e SCAN_TIME=23:59 \
    -l com.centurylinklabs.watchtower.enable=false "$IMG" sh -c "python3 /app/tests/fake_disk.py /data >/dev/null && exec python3 /app/dedupearr.py --daemon" >/dev/null
  echo "playground $NAME up: $([ "$PUBLIC" = "1" ] && echo "internal network $NET, no host port - attach the reverse proxy to it and point it at $NAME:3092" || echo "http://localhost:$PORT")"
}
case "${1:-}" in up) up ;; reset) down; up ;; down) down ;; *) echo "usage: $0 up|reset|down"; exit 2 ;; esac
