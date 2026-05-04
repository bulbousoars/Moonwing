from __future__ import annotations

import os
import platform as platform_module
import socket
from pathlib import Path


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
    inventory["sensor_user"] = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return inventory
