"""Parser tests for the RACADM adapter — no SSH needed."""
from __future__ import annotations

from bmc_adapters.racadm.parsers import (
    detect_error,
    parse_key_value_block,
    parse_power_status,
)


def test_parse_key_value_equals() -> None:
    txt = """
System Service Tag = 1ABCDE2
System Manufacturer = Dell Inc.
System Model = PowerEdge R640
"""
    out = parse_key_value_block(txt)
    assert out["system service tag"] == "1ABCDE2"
    assert out["system manufacturer"] == "Dell Inc."
    assert out["system model"] == "PowerEdge R640"


def test_parse_key_value_colon() -> None:
    txt = "Firmware Version: 4.40.40.00\nLast System Reboot: Tue Jan 2"
    out = parse_key_value_block(txt)
    assert out["firmware version"] == "4.40.40.00"
    assert out["last system reboot"] == "Tue Jan 2"


def test_parse_key_value_ignores_comments() -> None:
    txt = "# comment\n// another\nFoo = bar"
    out = parse_key_value_block(txt)
    assert out == {"foo": "bar"}


def test_detect_error_rac_code() -> None:
    txt = "ERROR: RAC0218 Already connected.\n"
    err = detect_error(txt)
    assert err is not None
    code, msg = err
    assert code == "RAC0218"
    assert "Already connected" in msg


def test_detect_error_no_code() -> None:
    txt = "ERROR: Something went wrong.\n"
    err = detect_error(txt)
    assert err is not None
    code, msg = err
    assert code == ""
    assert "Something went wrong" in msg


def test_detect_error_none() -> None:
    assert detect_error("Server power status: ON\n") is None


def test_parse_power_status_on() -> None:
    assert parse_power_status("Server power status: ON") == "on"


def test_parse_power_status_off() -> None:
    assert parse_power_status("Server power status = OFF") == "off"


def test_parse_power_status_unknown() -> None:
    assert parse_power_status("Something else entirely") == "unknown"
