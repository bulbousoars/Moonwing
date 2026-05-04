from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from moonwing_sensor import __version__
from moonwing_sensor.collectors import collect_host_inventory, collect_inventory_for_policy, collect_network_inventory


DEFAULT_CONFIG_PATH = Path("/etc/moonwing/sensor.json")


def normalize_server_url(server_url: str) -> str:
    return server_url.rstrip("/")


def build_heartbeat_payload(policy: dict | None = None) -> dict:
    effective_policy = policy or {"collectors": ["host"]}
    return {
        "inventory": collect_inventory_for_policy(effective_policy),
        "network": collect_network_inventory(),
        "sensor_version": __version__,
    }


def _request(method: str, url: str, *, body: dict | None = None, token: str | None = None) -> Any:
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Moonwing API request failed: {exc.code} {detail}") from exc


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def enroll(server_url: str, enrollment_token: str, config_path: Path) -> dict:
    host = collect_host_inventory()
    payload = {
        "enrollment_token": enrollment_token,
        "hostname": host["hostname"],
        "platform": host["platform"],
        "os_name": host["os_name"],
        "sensor_version": __version__,
        "labels": [host["platform"]],
    }
    server = normalize_server_url(server_url)
    response = _request("POST", f"{server}/api/sensors/enroll", body=payload)
    config = {
        "server_url": server,
        "sensor_id": response["sensor_id"],
        "token": response["token"],
        "policy": response.get("policy") or {},
    }
    save_config(config_path, config)
    return config


def run_once(config: dict) -> dict:
    server = normalize_server_url(config["server_url"])
    sensor_id = config["sensor_id"]
    token = config["token"]
    heartbeat = _request(
        "POST",
        f"{server}/api/sensors/{sensor_id}/heartbeat",
        body=build_heartbeat_payload(config.get("policy") or {}),
        token=token,
    )
    config["policy"] = heartbeat.get("policy") or config.get("policy") or {}
    task = _request("GET", f"{server}/api/sensors/{sensor_id}/tasks/next", token=token)
    if task:
        result = {"message": f"Unsupported task type {task['task_type']}", "sensor_version": __version__}
        _request(
            "POST",
            f"{server}/api/sensors/{sensor_id}/tasks/{task['id']}/result",
            body={"status": "failed", "result": result},
            token=token,
        )
    return config


def run_loop(config_path: Path, interval_seconds: int) -> None:
    while True:
        config = load_config(config_path)
        updated = run_once(config)
        save_config(config_path, updated)
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Moonwing endpoint sensor")
    sub = parser.add_subparsers(dest="command", required=True)
    enroll_parser = sub.add_parser("enroll")
    enroll_parser.add_argument("--server", required=True)
    enroll_parser.add_argument("--token", required=True)
    enroll_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run_parser.add_argument("--interval", type=int, default=60)
    once_parser = sub.add_parser("once")
    once_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args()

    if args.command == "enroll":
        enroll(args.server, args.token, Path(args.config))
    elif args.command == "run":
        run_loop(Path(args.config), args.interval)
    elif args.command == "once":
        config_path = Path(args.config)
        save_config(config_path, run_once(load_config(config_path)))


if __name__ == "__main__":
    main()
