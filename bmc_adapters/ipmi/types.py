"""Shared dataclasses for the IPMI adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Friendly power verbs accepted by IPMIClient.power_action.
# These mirror RedfishClient's verbs so callers can branch on
# transport at the edge only.
PowerAction = Literal["on", "off", "soft", "cycle", "reset", "nmi"]

# Mapping to pyghmi's set_power() argument values.
ACTION_TO_PYGHMI: dict[PowerAction, str] = {
    "on": "on",
    "off": "off",
    "soft": "shutdown",
    "cycle": "boot",
    "reset": "reset",
    "nmi": "diag",
}


@dataclass(slots=True, frozen=True)
class ChassisStatus:
    """Distilled `Get Chassis Status` (NetFn 0x00, Cmd 0x01)."""

    power_on: bool
    power_overload: bool = False
    power_fault: bool = False
    last_event: str | None = None
    intrusion: bool = False
    front_panel_lockout: bool = False
    drive_fault: bool = False
    cooling_fault: bool = False
    identify_active: bool = False
    boot_device: str | None = None


@dataclass(slots=True, frozen=True)
class Sensor:
    """One sensor reading from an SDR walk."""

    name: str
    type: str | None
    value: float | None
    units: str | None
    state: Literal["ok", "unavailable", "warning", "critical", "unknown"] = "ok"
    health: str | None = None


@dataclass(slots=True, frozen=True)
class FRU:
    """Field-Replaceable Unit inventory record (FRU 0 of the BMC)."""

    chassis_part_number: str | None = None
    chassis_serial: str | None = None
    board_manufacturer: str | None = None
    board_product_name: str | None = None
    board_serial: str | None = None
    board_part_number: str | None = None
    product_manufacturer: str | None = None
    product_name: str | None = None
    product_serial: str | None = None
    product_asset_tag: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SELEntry:
    """One System Event Log record."""

    timestamp: datetime | None
    sensor: str | None
    event_type: str | None
    direction: Literal["asserted", "deasserted", "unknown"] = "unknown"
    severity: Literal["info", "warning", "critical", "unknown"] = "unknown"
    description: str | None = None
    raw: dict[str, object] = field(default_factory=dict)
