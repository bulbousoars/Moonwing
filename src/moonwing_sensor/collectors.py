from __future__ import annotations

import json
import os
import platform as platform_module
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Iterator


def current_platform() -> str:
    system = platform_module.system().lower()
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    return "linux"


def collect_host_inventory() -> dict:
    return {
        "hostname": socket.gethostname(),
        "platform": current_platform(),
        "os_name": platform_module.platform(),
        "machine": platform_module.machine(),
        "python_version": platform_module.python_version(),
    }


def collect_network_inventory() -> dict:
    hostname = socket.gethostname()
    ips: list[str] = []
    try:
        for item in socket.getaddrinfo(hostname, None):
            address = item[4][0]
            if address not in ips:
                ips.append(address)
    except OSError:
        pass
    return {"hostname": hostname, "ips": ips}


def packages_from_dpkg_status(status_text: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in status_text.splitlines():
        if not line.strip():
            if current.get("name"):
                packages.append(current)
            current = {}
            continue
        if line.startswith("Package: "):
            current["name"] = line.split(": ", 1)[1].strip()
        elif line.startswith("Version: "):
            current["version"] = line.split(": ", 1)[1].strip()
    if current.get("name"):
        packages.append(current)
    return packages


def collect_linux_packages(status_path: str = "/var/lib/dpkg/status") -> list[dict[str, str]]:
    path = Path(status_path)
    if not path.exists():
        return []
    return packages_from_dpkg_status(path.read_text(encoding="utf-8", errors="replace"))


def collect_windows_installed_apps() -> list[dict[str, str]]:
    if current_platform() != "windows":
        return []
    try:
        import winreg
    except ImportError:
        return []

    apps: list[dict[str, str]] = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, subkey in roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        child_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, child_name) as child:
                            name = winreg.QueryValueEx(child, "DisplayName")[0]
                            version = ""
                            try:
                                version = winreg.QueryValueEx(child, "DisplayVersion")[0]
                            except OSError:
                                pass
                            apps.append({"name": str(name), "version": str(version)})
                    except OSError:
                        continue
        except OSError:
            continue
    return apps


def collect_macos_apps(applications_path: str = "/Applications") -> list[dict[str, str]]:
    if current_platform() != "macos":
        return []
    path = Path(applications_path)
    if not path.exists():
        return []
    return [{"name": item.stem, "path": str(item)} for item in path.glob("*.app")]


def packages_from_npm_ls_tree(tree: dict[str, Any], *, origin_label: str) -> list[dict[str, str]]:
    """Parse ``npm list --json`` style output (top-level ``dependencies`` only)."""
    out: list[dict[str, str]] = []
    deps = tree.get("dependencies")
    if not isinstance(deps, dict):
        return out
    for name, meta in deps.items():
        if not isinstance(meta, dict) or meta.get("missing"):
            continue
        version = meta.get("version", "")
        out.append({"name": str(name), "version": str(version), "origin": origin_label})
    return out


def _run_npm_json(args: list[str], *, timeout: float = 120.0) -> dict[str, Any] | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        proc = subprocess.run(
            [npm, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _policy_package_json_paths(policy: dict) -> Iterator[Path]:
    raw = policy.get("npm_scan_roots")
    if not isinstance(raw, list):
        return
    for item in raw[:20]:
        if not isinstance(item, str) or not item.strip():
            continue
        root = Path(item).expanduser()
        candidate = root / "package.json" if root.is_dir() else root
        if candidate.is_file() and candidate.name == "package.json":
            yield candidate


def _shallow_home_package_json_paths(*, max_files: int = 24) -> Iterator[Path]:
    """Bounded discovery: ``$HOME/*`` and ``$HOME/*/*`` (skips dot dirs and ``node_modules``)."""
    home = Path.home()
    if not home.is_dir():
        return
    yielded = 0
    try:
        tier0_dirs = [p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return
    for tier0 in tier0_dirs:
        if yielded >= max_files:
            return
        pj = tier0 / "package.json"
        if pj.is_file():
            yield pj
            yielded += 1
        try:
            tier1_dirs = [
                p for p in tier0.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "node_modules"
            ]
        except OSError:
            continue
        for tier1 in tier1_dirs:
            if yielded >= max_files:
                return
            pj = tier1 / "package.json"
            if pj.is_file():
                yield pj
                yielded += 1


def packages_from_package_json(path: Path) -> list[dict[str, str]]:
    """Read declared dependencies from a ``package.json`` (versions as declared, not lockfile-resolved)."""
    try:
        if not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8", errors="replace")
        if len(raw) > 2_000_000:
            return []
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    origin = f"project:{path.parent}"
    combined: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            for dep_name, spec in block.items():
                if isinstance(spec, str):
                    combined[str(dep_name)] = spec
    return [{"name": k, "version": v, "origin": origin} for k, v in sorted(combined.items())]


def collect_npm_packages(policy: dict | None = None) -> list[dict[str, str]]:
    """Global NPM tree plus optional policy roots and a shallow ``$HOME`` project scan."""
    policy = policy or {}
    items: list[dict[str, str]] = []

    gtree = _run_npm_json(["list", "-g", "--json", "--depth=0"])
    if gtree:
        items.extend(packages_from_npm_ls_tree(gtree, origin_label="global"))

    seen_paths: set[Path] = set()
    for pj in _policy_package_json_paths(policy):
        rp = pj.resolve()
        if rp in seen_paths:
            continue
        seen_paths.add(rp)
        items.extend(packages_from_package_json(pj))

    for pj in _shallow_home_package_json_paths():
        rp = pj.resolve()
        if rp in seen_paths:
            continue
        seen_paths.add(rp)
        items.extend(packages_from_package_json(pj))

    deduped: list[dict[str, str]] = []
    seen_key: set[tuple[str, str, str]] = set()
    for it in items:
        key = (it["name"], it["version"], it["origin"])
        if key in seen_key:
            continue
        seen_key.add(key)
        deduped.append(it)
        if len(deduped) >= 800:
            break
    return deduped


def collect_process_snapshot() -> list[dict[str, str | int]]:
    proc = Path("/proc")
    if current_platform() != "linux" or not proc.exists():
        return []
    processes: list[dict[str, str | int]] = []
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        comm = item / "comm"
        try:
            processes.append({"pid": int(item.name), "name": comm.read_text(encoding="utf-8", errors="replace").strip()})
        except OSError:
            continue
    return processes[:500]


def collect_inventory_for_policy(policy: dict) -> dict:
    collectors = set(policy.get("collectors") or ["host"])
    inventory: dict = {}
    if "host" in collectors:
        inventory["host"] = collect_host_inventory()
    if "packages" in collectors:
        inventory["packages"] = collect_linux_packages()
    if "installed_apps" in collectors:
        inventory["installed_apps"] = collect_windows_installed_apps() or collect_macos_apps()
    if "processes" in collectors:
        inventory["processes"] = collect_process_snapshot()
    if "npm_packages" in collectors:
        inventory["npm_packages"] = collect_npm_packages(policy)
    inventory["sensor_user"] = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return inventory
