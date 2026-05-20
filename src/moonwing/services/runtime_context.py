"""Where Moonwing is running (container vs bare metal) — for honest CLI probes."""

from __future__ import annotations

import os
import platform
from typing import Any


def running_in_container() -> bool:
    """Best-effort: Docker / containerd / Podman style isolation."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="replace") as fh:
            cg = fh.read()
    except OSError:
        return False
    markers = ("docker", "containerd", "kubelet", "kubepods", "podman", "lxc")
    return any(m in cg for m in markers)


def build_runtime_context() -> dict[str, Any]:
    return {
        "in_container": running_in_container(),
        "hostname": platform.node() or None,
        "platform": platform.platform(),
    }
