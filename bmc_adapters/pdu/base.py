"""PDU adapter ABC + shared dataclasses.

Three families of operations across vendors:

- Control (writes): outlet on / off / cycle, outlet rename
- State (reads, hot path): outlet state, list outlets with per-outlet power
- Metrics (reads, slower): total power, energy, per-phase current

Outlets are addressable by 1-indexed integer (matching chassis labels)
OR by name. Each adapter normalises to its on-wire scheme.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Literal

from ..findings import BMCFinding

OutletState = Literal["on", "off", "cycling", "unknown"]
OutletIdx = int | str  # int = label-numbered (1-based), str = outlet name


@dataclass(slots=True, frozen=True)
class Outlet:
    """One PDU outlet."""

    index: int                 # 1-indexed to match chassis labels
    name: str
    state: OutletState
    current_a: float | None = None
    power_w: float | None = None


@dataclass(slots=True, frozen=True)
class PDUMetrics:
    """Whole-PDU readings (for capacity planning)."""

    total_power_w: float | None = None
    total_energy_kwh: float | None = None
    phase_currents_a: tuple[float, ...] = ()  # 1 for 1-phase, 3 for 3-phase
    pdu_temperature_c: float | None = None


@dataclass(slots=True, frozen=True)
class EnvironmentSensor:
    """A peripheral sensor (temp / humidity / contact / leak) dangling
    off the PDU. APC calls them NetBotz, Raritan 'peripherals',
    Eaton 'EMDs'."""

    name: str
    kind: Literal["temperature", "humidity", "contact", "leak", "other"]
    value: float | bool
    unit: str


class PDUClient(abc.ABC):
    """Async PDU control."""

    vendor: str = "unknown"
    model: str | None = None

    def __init__(self) -> None:
        self._findings: list[BMCFinding] = []

    @property
    def findings(self) -> list[BMCFinding]:
        return list(self._findings)

    @abc.abstractmethod
    async def list_outlets(self) -> list[Outlet]: ...

    @abc.abstractmethod
    async def outlet_state(self, idx: OutletIdx) -> OutletState: ...

    @abc.abstractmethod
    async def outlet_on(self, idx: OutletIdx) -> None: ...

    @abc.abstractmethod
    async def outlet_off(self, idx: OutletIdx) -> None: ...

    @abc.abstractmethod
    async def outlet_cycle(self, idx: OutletIdx) -> None: ...

    @abc.abstractmethod
    async def power_metrics(self) -> PDUMetrics: ...

    async def environment_sensors(self) -> list[EnvironmentSensor]:
        """Default: not supported. Override per-vendor."""
        return []

    async def set_outlet_name(self, idx: OutletIdx, name: str) -> None:
        raise NotImplementedError(f"{self.vendor} does not implement outlet rename")

    async def aclose(self) -> None:
        """Close the underlying transport. Default no-op."""
        return None

    async def __aenter__(self) -> "PDUClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- helpers ---

    async def _resolve_idx(self, idx: OutletIdx) -> int:
        """Resolve a name-based outlet ref to an int index."""
        if isinstance(idx, int):
            return idx
        outlets = await self.list_outlets()
        for o in outlets:
            if o.name == idx:
                return o.index
        raise KeyError(f"no outlet named {idx!r}")
