"""Shared dataclass types for the Redfish client."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HeartbeatSnapshot:
    """One poll cycle's worth of state for a Redfish device.

    `cpu_temp_c` is None when no thermal reading was discoverable; this
    is normal on some firmware (older Supermicro, certain OpenBMC builds).
    Callers should treat None as "no signal" rather than "zero."
    """
    online: bool
    power_state: str | None      # "On" / "Off" / "PoweringOn" / "PoweringOff" / ...
    cpu_temp_c: float | None     # CPU socket temp; falls back to chassis inlet if no CPU sensor
    health: str | None           # "OK" / "Warning" / "Critical"
