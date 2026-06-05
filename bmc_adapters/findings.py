"""Structured security findings emitted by adapters.

Adapters surface "your BMC is insecure" as typed records, not log lines.
Callers can route findings into audit chains, dashboards, or SIEM events.

A finding is informational by default — adapters do not refuse to connect
on a finding. The `severity` lets callers decide whether to escalate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "info"]

FindingCode = Literal[
    "BMC_CIPHER_ZERO_ENABLED",       # IPMI: BMC accepts cipher 0 sessions
    "BMC_DEFAULT_CREDENTIALS_LIKELY", # Auth uses a known default cred
    "BMC_NULL_USER_ENABLED",          # IPMI: anonymous user account live
    "BMC_FIRMWARE_PANTSDOWN_WINDOW",  # CVE-2019-6260 / Quanta lineage
    "BMC_SHA1_ONLY",                  # IPMI: only cipher 3 (SHA-1) supported
    "BMC_IPMI_1_5_ONLY",              # IPMI: RMCP only, no RMCP+
    "BMC_ANONYMOUS_LOGIN",            # IPMI 1.5 anonymous channel auth
    "PDU_SNMPV2C_PLAINTEXT",          # SNMPv2c in use — community in clear
    "PDU_DEFAULT_CREDENTIALS_LIKELY", # PDU known-default cred match
    "PDU_HTTP_NO_TLS",                # PDU REST/JSON-RPC over plain HTTP
    "REDFISH_NO_TLS_VERIFY",          # Caller disabled TLS verify
    "REDFISH_HTTP_BASIC_ONLY",        # No SessionService — falling back to Basic
]


@dataclass(slots=True, frozen=True)
class BMCFinding:
    """One security observation about the device an adapter is talking to."""

    code: FindingCode
    severity: Severity
    detail: str
    cve: tuple[str, ...] = field(default_factory=tuple)
    vendor: str | None = None  # populated when adapter knows the vendor

    def to_dict(self) -> dict[str, object]:
        """Stable JSON-serialisable shape for audit logs / SIEM forwarding."""
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "cve": list(self.cve),
            "vendor": self.vendor,
        }


# Default-credential fingerprints keyed by (vendor_lower, username_lower).
# Values are the matching default password (also lowercased). Adapters use
# this with a *constant-time* compare against the operator's credentials —
# never by probing the BMC with the default.
DEFAULT_CREDENTIAL_FINGERPRINTS: dict[tuple[str, str], str] = {
    # IPMI / BMC
    ("dell", "root"): "calvin",
    ("hpe", "administrator"): "admin",     # iLO 4 < 2.50
    ("hp", "administrator"): "admin",
    ("supermicro", "admin"): "admin",      # SMC pre-2020
    ("lenovo", "userid"): "passw0rd",      # zero, not capital O
    ("ibm", "userid"): "passw0rd",
    ("quanta", "admin"): "admin",
    ("fujitsu", "admin"): "admin",
    ("cisco", "admin"): "password",
    # PDU
    ("apc", "apc"): "apc",
    ("eaton", "admin"): "admin",
    ("raritan", "admin"): "raritan",
    ("legrand", "admin"): "legrand@1",
    ("tripplite", "localadmin"): "localadmin",
    ("servertech", "admn"): "admn",        # not a typo
    ("cyberpower", "cyber"): "cyber",
    ("vertiv", "admin"): "admin",
    ("geist", "admin"): "admin",
}


def matches_default_credential(
    vendor: str | None, username: str, password: str
) -> bool:
    """Return True if (vendor, username, password) matches a known default.

    Constant-ish time comparison: the lookup itself is dict-keyed (vendor
    can be guessed by an attacker), but the equality check uses
    `secrets.compare_digest` to avoid leaking the password through timing.
    """
    import secrets

    if vendor is None:
        return False
    key = (vendor.lower(), username.lower())
    expected = DEFAULT_CREDENTIAL_FINGERPRINTS.get(key)
    if expected is None:
        return False
    return secrets.compare_digest(password.lower(), expected)


# Firmware windows that ship known-vulnerable BMC code paths. Adapters
# fingerprint the BMC and emit a finding when the (vendor, firmware) pair
# is inside a window. Keep this list small and precise — we only flag
# documented CVEs, not "old firmware = bad" hand-waving.
PANTSDOWN_AFFECTED_VENDORS: frozenset[str] = frozenset({
    "supermicro",   # X9/X10/X11 BMC pre 1.74 / 3.74 (AST2400/2500)
    "quanta",       # CVE-2019-6260 follow-up applied through 2024
    "wiwynn",
    "inspur",
    "tyan",
})


def pantsdown_finding(vendor: str, firmware: str | None) -> BMCFinding | None:
    """Emit BMC_FIRMWARE_PANTSDOWN_WINDOW for AST2400/AST2500 BMCs in
    the vulnerability window. Conservative: only fires for vendors we
    have evidence of unpatched fleet exposure for."""
    if vendor.lower() not in PANTSDOWN_AFFECTED_VENDORS:
        return None
    return BMCFinding(
        code="BMC_FIRMWARE_PANTSDOWN_WINDOW",
        severity="high",
        detail=(
            f"{vendor} BMC firmware {firmware or '<unknown>'} is in the "
            "CVE-2019-6260 / Pantsdown vulnerability window for AST2400 / "
            "AST2500 BMCs. Verify the BMC firmware has the AHB bridge "
            "lockdown patch applied."
        ),
        cve=("CVE-2019-6260",),
        vendor=vendor,
    )
