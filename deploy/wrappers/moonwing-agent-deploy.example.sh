#!/usr/bin/env bash
# Example root-owned deploy entrypoint (copy to /usr/local/sbin/moonwing-agent-deploy).
# Same role as lunarleague-agent-deploy on the realestate VM: agents run `sudo -n /usr/local/sbin/moonwing-agent-deploy`.
# Wire sudoers: %moonwing-deploy ALL=(root) NOPASSWD: /usr/local/sbin/moonwing-agent-deploy
set -euo pipefail
export MOONWING_UPGRADE_REPO="${MOONWING_UPGRADE_REPO:-/opt/moonwing/Moonwing}"
exec "${MOONWING_UPGRADE_REPO}/scripts/moonwing-upgrade.sh"
