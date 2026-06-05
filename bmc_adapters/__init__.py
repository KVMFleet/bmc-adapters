"""kvmfleet-bmc-adapters — async Python library for out-of-band server management.

Multi-vendor, multi-protocol. The library covers the operator-facing
surface every fleet needs: power, sensors, inventory, system event log,
virtual media — across Redfish (Dell iDRAC / HPE iLO / Supermicro /
Lenovo XCC / OpenBMC), IPMI (pre-Redfish hardware), smart PDUs
(APC / Eaton / Raritan), and Wake-on-LAN.

Vendor quirks are absorbed inside the clients so they don't bleed into
calling code. Releases under Apache 2.0; powers the hosted access-
governance platform at https://kvmfleet.io.

Usage — Redfish (covered since v0.1.0):

    from bmc_adapters import RedfishClient
    async with RedfishClient(
        base_url="https://idrac.example.com",
        username="root",
        password="calvin",
    ) as bmc:
        await bmc.power_action("cycle")

Usage — IPMI (v0.4.0, pyghmi-backed, requires `[ipmi]` extra):

    from bmc_adapters.ipmi import IPMIClient, IPMIConfig
    async with IPMIClient(IPMIConfig(
        host="bmc.example.com",
        username="ADMIN",
        password=__SECRET__,
    )) as bmc:
        await bmc.power_action("cycle")
        for f in bmc.findings:
            audit.append(f.to_dict())

Usage — PDU (v0.4.0, requires `[pdu]` extra for SNMP backends):

    from bmc_adapters.pdu import APCPDUClient
    async with APCPDUClient("10.0.5.20", community=__SECRET__) as pdu:
        await pdu.outlet_cycle(3)

Usage — Wake-on-LAN (v0.4.0):

    from bmc_adapters import wake_on_lan
    await wake_on_lan("aa:bb:cc:dd:ee:ff")
"""
from bmc_adapters.base import BMCAdapter, Feature
from bmc_adapters.bmc import BMC
from bmc_adapters.findings import (
    DEFAULT_CREDENTIAL_FINGERPRINTS,
    BMCFinding,
    FindingCode,
    Severity,
    matches_default_credential,
    pantsdown_finding,
)
from bmc_adapters.redfish import (
    ACTION_TO_REDFISH,
    BmcUser,
    BootConfig,
    FanReading,
    FirmwareComponent,
    HealthRollup,
    HeartbeatSnapshot,
    LicenseInfo,
    MemoryModule,
    NetworkAdapter,
    NetworkInfo,
    PowerMetrics,
    PowerSupplyReading,
    ProcessorInfo,
    RedfishClient,
    RedfishError,
    SelEntry,
    StorageDrive,
    StorageVolume,
    SystemInfo,
    TemperatureReading,
)
from bmc_adapters.wol import wake_on_lan, wake_on_lan_sync

__version__ = "0.4.0"

__all__ = [
    # Redfish (existing)
    "ACTION_TO_REDFISH",
    "BmcUser",
    "BootConfig",
    "FanReading",
    "FirmwareComponent",
    "HealthRollup",
    "HeartbeatSnapshot",
    "LicenseInfo",
    "MemoryModule",
    "NetworkAdapter",
    "NetworkInfo",
    "PowerMetrics",
    "PowerSupplyReading",
    "ProcessorInfo",
    "RedfishClient",
    "RedfishError",
    "SelEntry",
    "StorageDrive",
    "StorageVolume",
    "SystemInfo",
    "TemperatureReading",
    # Cross-protocol
    "BMC",
    "BMCAdapter",
    "BMCFinding",
    "DEFAULT_CREDENTIAL_FINGERPRINTS",
    "Feature",
    "FindingCode",
    "Severity",
    "matches_default_credential",
    "pantsdown_finding",
    # WoL
    "wake_on_lan",
    "wake_on_lan_sync",
    # Version
    "__version__",
]
