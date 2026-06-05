"""IPMI adapter — async wrapper around pyghmi.

Covers the long tail of pre-Redfish hardware: Dell iDRAC6/7/8 (with
caveats on iDRAC9 4.40+ where IPMI is disabled by default), HPE iLO
3/4/5/6, Supermicro X9/X10/X11/X12+, Lenovo IMM2/XCC/XCC3, and
OEM whiteboxes built around Aspeed AST2400/2500/2600.

The wrapper enforces secure defaults: refuses IPMI 1.5, refuses
cipher suites 0/1/2/6/7/8/11/12, prefers SHA-256 (cipher 17) and
falls back to SHA-1 (cipher 3). Default-credential matches are
checked locally without probing the BMC.

Usage:

    from bmc_adapters.ipmi import IPMIClient, IPMIConfig

    async with IPMIClient(IPMIConfig(
        host="bmc.example.com",
        username="ADMIN",
        password=__SECRET__,
    )) as client:
        await client.power_action("cycle")
        sensors = await client.sensors()
        for finding in client.findings:
            audit_log.append(finding.to_dict())
"""
from .client import IPMIClient, IPMIConfig
from .types import (
    ACTION_TO_PYGHMI,
    FRU,
    ChassisStatus,
    PowerAction,
    SELEntry,
    Sensor,
)

__all__ = [
    "ACTION_TO_PYGHMI",
    "ChassisStatus",
    "FRU",
    "IPMIClient",
    "IPMIConfig",
    "PowerAction",
    "SELEntry",
    "Sensor",
]
