#!/usr/bin/env bash
# Upgrade a Docker Compose-based Moonwing install: optional git pull, rebuild, health wait.
# Run on the Linux host from the repository root (same directory as docker-compose.yml).
#
# Environment (optional):
#   MOONWING_UPGRADE_REPO     if set, cd here before upgrade (clone root with docker-compose.yml)
#   MOONWING_GIT_REMOTE       default: origin
#   MOONWING_GIT_BRANCH       default: main (used only when no upstream tracking branch exists)
#   MOONWING_SKIP_GIT         set to 1 to skip git fetch/pull (e.g. you rsynced the tree by hand)
#   MOONWING_HEALTH_URL       default: http://127.0.0.1:8000/health
#   MOONWING_HEALTH_RETRIES   default: 30 (two-second sleep between attempts)
set -euo pipefail

ROOT="${MOONWING_UPGRADE_REPO:-}"
if [[ -n "$ROOT" ]]; then
  ROOT="$(cd "$ROOT" && pwd)"
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "moonwing-upgrade: missing .env in $ROOT (copy from .env.example)" >&2
  exit 1
fi

REMOTE="${MOONWING_GIT_REMOTE:-origin}"
SKIP_GIT="${MOONWING_SKIP_GIT:-0}"

if [[ "$SKIP_GIT" != "1" ]] && [[ -d .git ]]; then
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    echo "moonwing-upgrade: git working tree is not clean - commit, stash, or set MOONWING_SKIP_GIT=1" >&2
    exit 1
  fi
  echo "[moonwing-upgrade] git fetch $REMOTE"
  git fetch "$REMOTE"
  if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    echo "[moonwing-upgrade] git pull --ff-only (tracking branch)"
    git pull --ff-only
  else
    BRANCH="${MOONWING_GIT_BRANCH:-main}"
    echo "[moonwing-upgrade] git pull --ff-only $REMOTE $BRANCH (no upstream - set MOONWING_GIT_BRANCH if needed)"
    git pull --ff-only "$REMOTE" "$BRANCH"
  fi
fi

if [[ "$SKIP_GIT" != "1" ]] && [[ ! -d .git ]]; then
  echo "[moonwing-upgrade] no .git directory - skipping git pull (set MOONWING_SKIP_GIT=1 to silence)" >&2
fi

echo "[moonwing-upgrade] rebuilding and restarting stack"
"$ROOT/scripts/moonwing-up.sh" "$@"

HEALTH_URL="${MOONWING_HEALTH_URL:-http://127.0.0.1:8000/health}"
RETRIES="${MOONWING_HEALTH_RETRIES:-30}"
echo "[moonwing-upgrade] waiting for $HEALTH_URL (up to $((RETRIES * 2))s)"
for ((i = 1; i <= RETRIES; i++)); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "[moonwing-upgrade] health OK"
    exit 0
  fi
  sleep 2
done

echo "moonwing-upgrade: health check failed for $HEALTH_URL" >&2
exit 1
