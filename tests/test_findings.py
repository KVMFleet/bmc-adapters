"""Tests for the BMCFinding / default-cred / Pantsdown helpers."""
from __future__ import annotations

from bmc_adapters import BMCFinding, matches_default_credential, pantsdown_finding


def test_default_cred_match() -> None:
    assert matches_default_credential("dell", "root", "calvin")
    assert matches_default_credential("apc", "apc", "apc")
    assert matches_default_credential("lenovo", "USERID", "PASSW0RD")  # case-insensitive


def test_default_cred_miss() -> None:
    assert not matches_default_credential("dell", "root", "not-the-default")
    assert not matches_default_credential("dell", "alice", "calvin")
    assert not matches_default_credential(None, "root", "calvin")


def test_pantsdown_vendors() -> None:
    finding = pantsdown_finding("supermicro", "1.74")
    assert finding is not None
    assert finding.code == "BMC_FIRMWARE_PANTSDOWN_WINDOW"
    assert "CVE-2019-6260" in finding.cve
    assert finding.severity == "high"


def test_pantsdown_unaffected_vendor() -> None:
    # Dell BMCs are not in the Pantsdown window — they use their own
    # codebase, not AST-vendor MegaRAC. Adapter must not emit.
    assert pantsdown_finding("dell", "3.50") is None


def test_finding_serialises() -> None:
    f = BMCFinding(
        code="BMC_CIPHER_ZERO_ENABLED",
        severity="critical",
        detail="Cipher 0 accepted by BMC",
        cve=("CVE-2013-4786",),
        vendor="supermicro",
    )
    d = f.to_dict()
    assert d["code"] == "BMC_CIPHER_ZERO_ENABLED"
    assert d["severity"] == "critical"
    assert d["cve"] == ["CVE-2013-4786"]
    assert d["vendor"] == "supermicro"
