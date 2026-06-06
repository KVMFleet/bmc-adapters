"""PDU adapter — multi-vendor outlet control.

Async clients for the common rack-PDU vendors. Each adapter speaks the
right protocol for that vendor (SNMP for APC/Eaton, JSON-RPC for
Raritan).

Usage:

    from bmc_adapters.pdu import APCPDUClient, RaritanPDUClient

    async with APCPDUClient("10.0.5.20", community="private") as pdu:
        for o in await pdu.list_outlets():
            print(o.index, o.name, o.state)
        await pdu.outlet_cycle(3)

    async with RaritanPDUClient(
        "https://pdu-2.example.com",
        username="admin",
        password=__SECRET__,
    ) as pdu:
        await pdu.outlet_off("server-rack-7")
"""
from .apc import APCPDUClient
from .base import (
    EnvironmentSensor,
    Outlet,
    OutletIdx,
    OutletState,
    PDUClient,
    PDUMetrics,
)
from .cyberpower import CyberPowerPDUClient
from .detect import PDUVendor, vendor_from_sysobjectid
from .eaton import EatonPDUClient
from .raritan import RaritanPDUClient
from .tripplite import TrippLitePDUClient

__all__ = [
    "APCPDUClient",
    "CyberPowerPDUClient",
    "EatonPDUClient",
    "EnvironmentSensor",
    "Outlet",
    "OutletIdx",
    "OutletState",
    "PDUClient",
    "PDUMetrics",
    "PDUVendor",
    "RaritanPDUClient",
    "TrippLitePDUClient",
    "vendor_from_sysobjectid",
]
