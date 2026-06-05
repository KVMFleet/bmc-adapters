"""Vendor auto-detect for PDUs by SNMP sysObjectID prefix.

Used by `make_pdu_client(url)` to pick the right adapter without forcing
the operator to declare a vendor. The fallback is the same as for IPMI:
ask the operator to specify if we can't fingerprint.
"""
from __future__ import annotations

from typing import Literal

PDUVendor = Literal[
    "apc", "eaton", "raritan", "tripplite",
    "servertech", "cyberpower", "geist", "vertiv", "unknown",
]


# sysObjectID enterprise OID prefix → vendor. We only match against
# stable, well-known prefixes; OEMs that re-use a chip vendor's
# enterprise number stay 'unknown'.
_SYSOBJECTID_TO_VENDOR: dict[str, PDUVendor] = {
    "1.3.6.1.4.1.318.1.3":   "apc",
    "1.3.6.1.4.1.534":       "eaton",
    "1.3.6.1.4.1.13742":     "raritan",
    "1.3.6.1.4.1.850.1":     "tripplite",
    "1.3.6.1.4.1.1718":      "servertech",
    "1.3.6.1.4.1.3808":      "cyberpower",
    "1.3.6.1.4.1.21239":     "geist",          # Geist (now Vertiv)
}


def vendor_from_sysobjectid(oid: str) -> PDUVendor:
    for prefix, vendor in _SYSOBJECTID_TO_VENDOR.items():
        if oid.startswith(prefix):
            return vendor
    return "unknown"
