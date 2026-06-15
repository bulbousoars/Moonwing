#!/usr/bin/env bash
# Run on secops (192.168.1.215) as dduggan after: ssh dduggan@192.168.1.215
# One-shot: locate Moonwing, enable host AI probes, stop/remove GVM containers.
set -euo pipefail

echo "==> Moonwing / secops maintenance"

# --- Find Moonwing compose project root ---
MOONWING_ROOT=""
for candidate in \
  /mnt/storage/docker/moonwing \
  /opt/moonwing/Moonwing \
  "$HOME/Moonwing" \
  /mnt/storage/docker/Moonwing
do
  if [[ -f "$candidate/docker-compose.yml" ]]; then
    MOONWING_ROOT="$candidate"
    break
  fi
done

if [[ -z "$MOONWING_ROOT" ]]; then
  echo "Could not find Moonwing checkout (docker-compose.yml). Search:"
  find /mnt/storage/docker /opt -maxdepth 4 -name docker-compose.yml 2>/dev/null \
    | xargs -r grep -l 'moonwing-api' 2>/dev/null | head -5 || true
  exit 1
fi

echo "Moonwing root: $MOONWING_ROOT"
cd "$MOONWING_ROOT"

OVERLAY="deploy/secops/docker-compose.secops.yml"
if [[ ! -f "$OVERLAY" ]]; then
  echo "Missing $OVERLAY — git pull Moonwing first."
  exit 1
fi

# --- Stop GVM / OpenVAS (free RAM; Moonwing is the scanner now) ---
echo "==> Stopping GVM/OpenVAS containers (if any)"
GVM_IDS="$(docker ps -aq --filter name=gvm 2>/dev/null || true)"
if [[ -n "$GVM_IDS" ]]; then
  docker stop $GVM_IDS || true
  docker rm $GVM_IDS || true
  echo "Removed GVM containers."
else
  echo "No running gvm* containers."
fi

# Optional: disable gvm in secops ELK compose (manual backup first)
SECOPS_COMPOSE="/mnt/storage/docker/docker-compose.yml"
if [[ -f "$SECOPS_COMPOSE" ]] && grep -qE '^\s+gvm:' "$SECOPS_COMPOSE" 2>/dev/null; then
  echo "NOTE: $SECOPS_COMPOSE still defines a gvm: service."
  echo "      After backup, comment/remove that block if you do not want GVM to return on compose up."
fi

# --- Redeploy Moonwing with secops overlay (host CLI probes) ---
echo "==> Rebuilding Moonwing with host-probe mounts"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "docker compose not found"
  exit 1
fi
if ! groups 2>/dev/null | grep -qw docker; then
  if sudo -n true 2>/dev/null; then
    COMPOSE=(sudo -n "${COMPOSE[@]}")
  fi
fi
"${COMPOSE[@]}" -f docker-compose.yml -f "$OVERLAY" --profile app up -d --build

echo "==> Health"
curl -fsS http://127.0.0.1:8000/health && echo

echo "==> Host AI tools API (requires admin session cookie for browser; this is server-side)"
"${COMPOSE[@]}" -f docker-compose.yml -f "$OVERLAY" --profile app exec -T moonwing-api \
  python -c "from moonwing.config import Settings; from moonwing.services.host_cli_discovery import scan_host_ai_tools; import json; print(json.dumps(scan_host_ai_tools(Settings()), indent=2))" \
  2>/dev/null || echo "(exec check skipped — open http://192.168.1.215:8000/settings?tab=ai-tools in browser)"

echo "Done. UI: http://192.168.1.215:8000"
