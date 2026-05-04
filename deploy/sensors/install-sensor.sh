#!/usr/bin/env bash
# Moonwing endpoint sensor installer (Linux) — shipped in-repo.
#
# Requires a stable MOONWING_MANAGER_URL (DNS or IP) and MOONWING_ENROLLMENT_TOKEN
# from Management → Sensors → Install. Operational twin of UI-generated installers;
# keep aligned with src/moonwing/services/sensor_installer.py.

set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  sudo MOONWING_MANAGER_URL=<url> MOONWING_ENROLLMENT_TOKEN=<secret> $0

  sudo $0 --manager-url <url> --enrollment-token <secret>

<murl> must be the manager base URL (http or https), e.g. https://moonwing.example.com
USAGE
}

MANAGER_URL="${MOONWING_MANAGER_URL:-}"
ENROLLMENT_TOKEN="${MOONWING_ENROLLMENT_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manager-url)
      MANAGER_URL="${2:?}"
      shift 2
      ;;
    --enrollment-token)
      ENROLLMENT_TOKEN="${2:?}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

MANAGER_URL="${MANAGER_URL%/}"
if [[ -z "$MANAGER_URL" || -z "$ENROLLMENT_TOKEN" ]]; then
  echo "[moonwing-sensor] Set MOONWING_MANAGER_URL and MOONWING_ENROLLMENT_TOKEN or pass --manager-url/--enrollment-token" >&2
  usage >&2
  exit 2
fi

case "$MANAGER_URL" in
  http://*|https://*) ;;
  *)
    echo "[moonwing-sensor] Manager URL must start with http:// or https://" >&2
    exit 2
    ;;
esac

INSTALL_DIR="/opt/moonwing-sensor"
CONFIG_DIR="/etc/moonwing"
CONFIG_FILE="$CONFIG_DIR/sensor.env"
AGENT_SCRIPT="$INSTALL_DIR/moonwing-sensor.sh"
SERVICE_FILE="/etc/systemd/system/moonwing-sensor.service"

if [[ $EUID -ne 0 ]]; then
  echo "[moonwing-sensor] must run as root" >&2
  exit 1
fi

for tool in curl python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "[moonwing-sensor] missing required tool: $tool" >&2
    exit 1
  fi
done

HOSTNAME_VALUE="$(hostname -f 2>/dev/null || hostname)"
OS_NAME="$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-Linux}" || echo "Linux")"
SENSOR_VERSION="0.1.0"

mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"

echo "[moonwing-sensor] enrolling with $MANAGER_URL"

export MW_ENROLLMENT_TOKEN="$ENROLLMENT_TOKEN"
export MW_HOSTNAME="$HOSTNAME_VALUE"
export MW_OS_NAME="$OS_NAME"
export MW_SENSOR_VERSION="$SENSOR_VERSION"
ENROLL_PAYLOAD=$(python3 <<'PY'
import json
import os

print(
    json.dumps(
        {
            "enrollment_token": os.environ["MW_ENROLLMENT_TOKEN"],
            "hostname": os.environ["MW_HOSTNAME"],
            "platform": "linux",
            "os_name": os.environ["MW_OS_NAME"],
            "sensor_version": os.environ["MW_SENSOR_VERSION"],
            "labels": [],
        }
    )
)
PY
)
unset MW_ENROLLMENT_TOKEN MW_HOSTNAME MW_OS_NAME MW_SENSOR_VERSION

ENROLL_RESPONSE=$(curl -sS -f -X POST "$MANAGER_URL/api/sensors/enroll" \
  -H "Content-Type: application/json" \
  -d "$ENROLL_PAYLOAD")

SENSOR_ID=$(printf '%s' "$ENROLL_RESPONSE" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sensor_id"])')
SENSOR_TOKEN=$(printf '%s' "$ENROLL_RESPONSE" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

if [[ -z "${SENSOR_ID:-}" || -z "${SENSOR_TOKEN:-}" ]]; then
  echo "[moonwing-sensor] enrollment failed: $ENROLL_RESPONSE" >&2
  exit 1
fi

cat > "$CONFIG_FILE" <<EOF
MOONWING_MANAGER_URL=$MANAGER_URL
MOONWING_SENSOR_ID=$SENSOR_ID
MOONWING_SENSOR_TOKEN=$SENSOR_TOKEN
MOONWING_SENSOR_VERSION=$SENSOR_VERSION
EOF
chmod 600 "$CONFIG_FILE"

cat > "$AGENT_SCRIPT" <<'AGENT'
#!/usr/bin/env bash
# Minimal Moonwing sensor heartbeat loop.
set -u
# shellcheck source=/etc/moonwing/sensor.env
source /etc/moonwing/sensor.env

heartbeat_interval=${MOONWING_SENSOR_HEARTBEAT_SECONDS:-60}

while true; do
  inventory=$(python3 - <<PY
import json, platform, socket
print(json.dumps({
    "hostname": socket.gethostname(),
    "fqdn": socket.getfqdn(),
    "kernel": platform.release(),
    "machine": platform.machine(),
    "python": platform.python_version(),
}))
PY
)
  payload=$(python3 - <<PY
import json, os
print(json.dumps({
    "inventory": json.loads(os.environ["INVENTORY"]),
    "network": {},
    "sensor_version": os.environ.get("MOONWING_SENSOR_VERSION", ""),
}))
PY
)
  INVENTORY="$inventory" \
    curl -sS -f -X POST "$MOONWING_MANAGER_URL/api/sensors/$MOONWING_SENSOR_ID/heartbeat" \
    -H "Authorization: Bearer $MOONWING_SENSOR_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null || echo "[moonwing-sensor] heartbeat failed at $(date -Iseconds)" >&2
  sleep "$heartbeat_interval"
done
AGENT
chmod 750 "$AGENT_SCRIPT"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Moonwing Endpoint Sensor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$CONFIG_FILE
ExecStart=$AGENT_SCRIPT
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now moonwing-sensor.service

echo "[moonwing-sensor] installed and running. sensor_id=$SENSOR_ID"
echo "[moonwing-sensor] view status: systemctl status moonwing-sensor"
echo "[moonwing-sensor] logs:        journalctl -u moonwing-sensor -f"