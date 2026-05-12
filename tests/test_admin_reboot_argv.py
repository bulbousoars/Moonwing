from moonwing.config import Settings
from moonwing.services.system_updates import parse_admin_reboot_argv


def test_parse_admin_reboot_argv_valid():
    s = Settings(admin_reboot_argv_json='["sudo","/sbin/reboot"]')
    assert parse_admin_reboot_argv(s) == ["sudo", "/sbin/reboot"]


def test_parse_admin_reboot_argv_empty_disabled():
    s = Settings(admin_reboot_argv_json="")
    assert parse_admin_reboot_argv(s) is None


def test_parse_admin_reboot_argv_invalid_json():
    s = Settings(admin_reboot_argv_json="not-json")
    assert parse_admin_reboot_argv(s) is None


def test_parse_admin_reboot_argv_rejects_non_strings():
    s = Settings(admin_reboot_argv_json="[1,2]")
    assert parse_admin_reboot_argv(s) is None
