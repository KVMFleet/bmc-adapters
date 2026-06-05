"""kvmfleet-bmc-adapters — multi-vendor BMC Redfish client.

Async Python client for Dell iDRAC, HPE iLO, Supermicro, Lenovo XCC,
and OpenBMC (and anything else that speaks the DMTF Redfish standard
well enough). Powers the hosted access-governance platform at
https://kvmfleet.io, released under Apache 2.0 so anyone building
tooling against multi-vendor BMCs can reuse the parts that matter.

The shape is deliberately small. Everything is async. The library
accepts plaintext credentials or a callable; secret encryption is
the caller's job.

Usage:

    from bmc_adapters import RedfishClient

    async with RedfishClient(
        base_url="https://idrac.example.com",
        username="root",
        password="calvin",
    ) as client:
        snap = await client.heartbeat()
        info = await client.system_info()
        temps = await client.temperatures()
        drives = await client.drive_inventory()
        await client.power_action("cycle")
        await client.insert_virtual_media("https://example.com/ubuntu.iso")

See README.md for the full interface + supported vendors.
"""
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

__version__ = "0.3.0"

__all__ = [
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
    "__version__",
]
