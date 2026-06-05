"""Tests for vendor auto-detect by SNMP sysObjectID."""
from __future__ import annotations

from bmc_adapters.pdu import vendor_from_sysobjectid


def test_apc_prefix() -> None:
    assert vendor_from_sysobjectid("1.3.6.1.4.1.318.1.3.4.5") == "apc"


def test_eaton_prefix() -> None:
    assert vendor_from_sysobjectid("1.3.6.1.4.1.534.6.6.7") == "eaton"


def test_raritan_prefix() -> None:
    assert vendor_from_sysobjectid("1.3.6.1.4.1.13742.6.3") == "raritan"


def test_tripp_lite() -> None:
    assert vendor_from_sysobjectid("1.3.6.1.4.1.850.1.2") == "tripplite"


def test_unknown_returns_unknown() -> None:
    assert vendor_from_sysobjectid("1.3.6.1.4.1.9999.1") == "unknown"
