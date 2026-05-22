#!/usr/bin/env bash
# Shortcut: docker compose --profile app up -d --build (from repo root).
# Require .env first (e.g. cp .env.example .env && edit). Migrations run via moonwing-migrate in compose.
#
# Optional: MOONWING_COMPOSE_OVERLAY=deploy/secops/docker-compose.secops.yml (secops MinIO 9011, host-probe)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE_ARGS=(-f docker-compose.yml)
if [[ -n "${MOONWING_COMPOSE_OVERLAY:-}" ]]; then
  if [[ ! -f "$MOONWING_COMPOSE_OVERLAY" ]]; then
    echo "moonwing-up: MOONWING_COMPOSE_OVERLAY not found: $MOONWING_COMPOSE_OVERLAY" >&2
    exit 1
  fi
  COMPOSE_ARGS+=(-f "$MOONWING_COMPOSE_OVERLAY")
fi
docker compose "${COMPOSE_ARGS[@]}" --profile app up -d --build "$@"
