"""Redfish-protocol BMC adapter.

Re-exports the public surface from .client / .types / .errors so
`from bmc_adapters.redfish import RedfishClient` works alongside
`from bmc_adapters import RedfishClient`.
"""
from bmc_adapters.redfish.client import ACTION_TO_REDFISH, RedfishClient
from bmc_adapters.redfish.errors import RedfishError
from bmc_adapters.redfish.types import HeartbeatSnapshot

__all__ = [
    "ACTION_TO_REDFISH",
    "HeartbeatSnapshot",
    "RedfishClient",
    "RedfishError",
]
