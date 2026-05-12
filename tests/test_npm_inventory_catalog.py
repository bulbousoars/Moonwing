from types import SimpleNamespace
from uuid import uuid4

from moonwing.services.npm_inventory_catalog import aggregate_npm_inventory_rows


def test_aggregate_npm_inventory_rows_flattens_sensor_inventory():
    sid = uuid4()
    other = uuid4()
    sensors = [
        SimpleNamespace(
            id=sid,
            hostname="b-host",
            platform="linux",
            inventory={"npm_packages": [{"name": "left-pad", "version": "1.0.0", "origin": "global"}]},
        ),
        SimpleNamespace(
            id=other,
            hostname="a-host",
            platform="windows",
            inventory={
                "npm_packages": [
                    {"name": "lodash", "version": "4.17.21", "origin": "global"},
                    {"name": "lodash", "version": "4.17.21", "origin": "global"},
                ]
            },
        ),
        SimpleNamespace(id=uuid4(), hostname="empty", platform="linux", inventory={}),
    ]
    rows = aggregate_npm_inventory_rows(sensors)
    assert len(rows) == 3
    a_rows = [r for r in rows if r["hostname"] == "a-host"]
    b_rows = [r for r in rows if r["hostname"] == "b-host"]
    assert len(a_rows) == 2
    assert all(r["name"] == "lodash" for r in a_rows)
    assert b_rows[0]["name"] == "left-pad"
