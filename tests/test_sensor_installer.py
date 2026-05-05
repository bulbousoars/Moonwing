from __future__ import annotations

import io
import zipfile

import pytest

from moonwing.services.sensor_installer import (
    InstallerConfigError,
    build_windows_sensor_zip_bytes,
    render_installer,
    render_linux_installer,
    render_windows_installer,
)


class TestRenderLinuxInstaller:
    def test_bakes_in_manager_url_and_token(self):
        body = render_linux_installer(
            manager_url="https://moonwing.example.com",
            enrollment_token="enroll-token-abc",
        )
        assert "MANAGER_URL='https://moonwing.example.com'" in body
        assert "ENROLLMENT_TOKEN='enroll-token-abc'" in body
        assert body.startswith("#!/usr/bin/env bash")

    def test_strips_trailing_slash_from_manager_url(self):
        body = render_linux_installer(
            manager_url="https://moonwing.example.com/",
            enrollment_token="t",
        )
        assert "MANAGER_URL='https://moonwing.example.com'" in body
        assert "MANAGER_URL='https://moonwing.example.com/'" not in body

    def test_installs_systemd_service_and_heartbeat_call(self):
        body = render_linux_installer(
            manager_url="https://m",
            enrollment_token="t",
        )
        assert "/etc/systemd/system/moonwing-sensor.service" in body
        assert "systemctl daemon-reload" in body
        assert "systemctl enable --now moonwing-sensor.service" in body
        assert "/api/sensors/enroll" in body
        assert "/heartbeat" in body

    def test_quotes_single_quotes_in_token(self):
        body = render_linux_installer(
            manager_url="https://m",
            enrollment_token="weird'token",
        )
        # bash single-quote-escape pattern: '"'"'
        assert "weird'\"'\"'token" in body

    def test_rejects_missing_token(self):
        with pytest.raises(InstallerConfigError):
            render_linux_installer(manager_url="https://m", enrollment_token="")

    def test_rejects_whitespace_only_token(self):
        with pytest.raises(InstallerConfigError):
            render_linux_installer(manager_url="https://m", enrollment_token="   ")

    def test_rejects_missing_manager_url(self):
        with pytest.raises(InstallerConfigError):
            render_linux_installer(manager_url="", enrollment_token="t")


class TestRenderWindowsInstaller:
    def test_bakes_in_manager_url_and_token(self):
        body = render_windows_installer(
            manager_url="https://moonwing.example.com",
            enrollment_token="enroll-token-abc",
        )
        assert "$ManagerUrl       = 'https://moonwing.example.com'" in body
        assert "$EnrollmentToken  = 'enroll-token-abc'" in body
        assert "#requires -RunAsAdministrator" in body

    def test_registers_scheduled_task_and_calls_enroll(self):
        body = render_windows_installer(
            manager_url="https://m",
            enrollment_token="t",
        )
        assert "Register-ScheduledTask" in body
        assert "Moonwing Sensor Heartbeat" in body
        assert "/api/sensors/enroll" in body
        assert "/heartbeat" in body
        assert "C:\\ProgramData\\Moonwing\\sensor" in body

    def test_quotes_single_quotes_in_token(self):
        body = render_windows_installer(
            manager_url="https://m",
            enrollment_token="weird'token",
        )
        # PowerShell single-quote-escape: ''
        assert "weird''token" in body

    def test_rejects_missing_token(self):
        with pytest.raises(InstallerConfigError):
            render_windows_installer(manager_url="https://m", enrollment_token="")


class TestWindowsSensorZip:
    def test_contains_ps1_setup_and_readme(self):
        blob = build_windows_sensor_zip_bytes(
            manager_url="https://moonwing.example.com",
            enrollment_token="tok-enroll",
        )
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            names = set(zf.namelist())
        assert names == {"README.txt", "Moonwing-Sensor-Install.ps1", "Run Moonwing Sensor Setup.bat"}
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            ps1 = zf.read("Moonwing-Sensor-Install.ps1").decode("utf-8")
            bat = zf.read("Run Moonwing Sensor Setup.bat").decode("ascii", errors="replace")
            readme = zf.read("README.txt").decode("utf-8")
        assert "tok-enroll" in ps1
        assert "#requires -RunAsAdministrator" in ps1
        assert "Moonwing-Sensor-Install.ps1" in bat
        assert "@echo off" in bat
        assert "double-click" in readme.lower()


class TestRenderInstallerDispatch:
    def test_returns_linux_spec(self):
        spec = render_installer(
            platform="linux",
            manager_url="https://m",
            enrollment_token="t",
        )
        assert spec.platform == "linux"
        assert spec.filename == "moonwing-sensor-install.sh"
        assert spec.content_type == "text/x-shellscript"
        assert "#!/usr/bin/env bash" in spec.body

    def test_returns_windows_spec(self):
        spec = render_installer(
            platform="windows",
            manager_url="https://m",
            enrollment_token="t",
        )
        assert spec.platform == "windows"
        assert spec.filename == "moonwing-sensor-install.ps1"
        assert spec.content_type == "text/x-powershell"
        assert "#requires -RunAsAdministrator" in spec.body

    def test_normalizes_aliases(self):
        spec_a = render_installer(platform="WINDOWS", manager_url="https://m", enrollment_token="t")
        spec_b = render_installer(platform="win32", manager_url="https://m", enrollment_token="t")
        assert spec_a.platform == "windows"
        assert spec_b.platform == "windows"

    def test_rejects_unsupported_platform(self):
        with pytest.raises(InstallerConfigError):
            render_installer(platform="aix", manager_url="https://m", enrollment_token="t")

    def test_rejects_macos_for_now(self):
        # macOS installer is not yet shipped from the manager
        with pytest.raises(InstallerConfigError):
            render_installer(platform="macos", manager_url="https://m", enrollment_token="t")
