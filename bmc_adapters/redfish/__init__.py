"""Redfish-protocol BMC adapter.

Re-exports the public surface from .client / .types / .errors so
`from bmc_adapters.redfish import RedfishClient` works alongside
`from bmc_adapters import RedfishClient`.
"""
from bmc_adapters.redfish.client import ACTION_TO_REDFISH, RedfishClient
from bmc_adapters.redfish.errors import RedfishError
from bmc_adapters.redfish.types import (
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
    SelEntry,
    StorageDrive,
    StorageVolume,
    SystemInfo,
    TemperatureReading,
)

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
]

# Note: vendor detection lives on RedfishClient as the async method
# `detect_vendor()`. See client.py for usage.
