#!/usr/bin/env bash
# Shortcut: docker compose --profile app up -d --build (from repo root).
# Require .env first (e.g. cp .env.example .env && edit). Migrations run via moonwing-migrate in compose.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose --profile app up -d --build "$@"
