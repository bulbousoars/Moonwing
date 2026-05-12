"""Aggregate NPM package inventory reported by endpoint sensors for UI display."""

from __future__ import annotations

from typing import Any, Sequence

from moonwing.db.models import SensorEndpoint


def aggregate_npm_inventory_rows(sensors: Sequence[SensorEndpoint]) -> list[dict[str, Any]]:
    """Flatten per-sensor ``inventory.npm_packages`` into sortable table rows."""
    rows: list[dict[str, Any]] = []
    for ep in sensors:
        inv = ep.inventory or {}
        raw = inv.get("npm_packages")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            rows.append(
                {
                    "sensor_id": str(ep.id),
                    "hostname": ep.hostname,
                    "platform": ep.platform,
                    "name": str(name),
                    "version": str(entry.get("version", "")),
                    "origin": str(entry.get("origin", "")),
                }
            )
    rows.sort(key=lambda r: (r["hostname"].lower(), r["name"].lower(), r["version"], r["origin"]))
    return rows
