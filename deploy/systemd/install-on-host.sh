#!/usr/bin/env bash
# Install moonwing-auto-upgrade systemd units (run as root).
# Resolves repo root from this file's location (…/Moonwing/deploy/systemd/install-on-host.sh).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "install-on-host: must run as root (try: sudo bash $0)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ ! -f "$REPO/docker-compose.yml" ]] || [[ ! -f "$REPO/scripts/moonwing-upgrade.sh" ]]; then
  echo "install-on-host: repo root looks wrong (missing docker-compose.yml or scripts/moonwing-upgrade.sh): $REPO" >&2
  exit 1
fi

UNIT_SRC="$SCRIPT_DIR/moonwing-auto-upgrade.service"
TIMER_SRC="$SCRIPT_DIR/moonwing-auto-upgrade.timer"
if [[ ! -f "$UNIT_SRC" ]] || [[ ! -f "$TIMER_SRC" ]]; then
  echo "install-on-host: missing unit files next to this script" >&2
  exit 1
fi

export MOONWING_INSTALL_REPO="$REPO"
export MOONWING_INSTALL_UNIT_SRC="$UNIT_SRC"
python3 -c '
import os
from pathlib import Path
repo = Path(os.environ["MOONWING_INSTALL_REPO"]).resolve()
unit_src = Path(os.environ["MOONWING_INSTALL_UNIT_SRC"])
text = unit_src.read_text()
text = text.replace("/opt/moonwing/Moonwing", str(repo))
Path("/etc/systemd/system/moonwing-auto-upgrade.service").write_text(text)
' || {
  echo "install-on-host: python3 substitution failed; install python3 or edit units manually" >&2
  exit 1
}
install -m 0644 "$TIMER_SRC" /etc/systemd/system/moonwing-auto-upgrade.timer

systemctl daemon-reload
systemctl enable --now moonwing-auto-upgrade.timer

echo "[install-on-host] timer status:"
systemctl status moonwing-auto-upgrade.timer --no-pager || true
echo "[install-on-host] next triggers:"
systemctl list-timers moonwing-auto-upgrade.timer --no-pager || true
echo "[install-on-host] done (repo=$REPO)"
