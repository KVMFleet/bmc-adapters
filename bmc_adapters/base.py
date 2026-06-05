"""Abstract base for all adapters + the Feature taxonomy.

Borrows the registry / per-feature compatibility pattern from
bmclib (github.com/bmc-toolbox/bmclib) — the right way to model
"protocol-agnostic out-of-band server management" is a small feature
ontology + per-adapter compatibility, not a god-interface.
"""
from __future__ import annotations

import abc
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .findings import BMCFinding


class Feature(StrEnum):
    """Operations any out-of-band path may or may not support.

    Adapters declare the subset they implement. The top-level `BMC`
    orchestrator (bmc.py) walks its registered adapters and dispatches
    to the first one that supports the requested feature on this device.
    """

    POWER_STATE = "power_state"
    POWER_SET = "power_set"
    BOOT_DEVICE_SET = "boot_device_set"
    SENSORS = "sensors"
    INVENTORY = "inventory"
    FRU = "fru"
    SEL_READ = "sel_read"
    SEL_CLEAR = "sel_clear"
    SOL = "sol"
    BMC_RESET = "bmc_reset"
    VIRTUAL_MEDIA = "virtual_media"
    OUTLET_CONTROL = "outlet_control"       # PDU
    OUTLET_METRICS = "outlet_metrics"       # PDU
    WAKE = "wake"                           # WoL


@runtime_checkable
class FeatureProvider(Protocol):
    """Anything that declares supported features and emits findings."""

    @property
    def vendor(self) -> str: ...

    @property
    def features(self) -> frozenset[Feature]: ...

    @property
    def findings(self) -> list[BMCFinding]: ...


class BMCAdapter(abc.ABC):
    """Shared base for protocol-specific adapters (IPMI, PDU, PiKVM, ...).

    Subclasses MUST set `vendor`, `features`, and implement `aclose`.
    They MAY override any feature method they declare in `features`.
    Default method implementations raise `NotImplementedError` so a
    miswired adapter fails loudly.
    """

    vendor: str = "unknown"
    features: frozenset[Feature] = frozenset()

    def __init__(self) -> None:
        self._findings: list[BMCFinding] = []

    @property
    def findings(self) -> list[BMCFinding]:
        return list(self._findings)

    def _emit(self, finding: BMCFinding) -> None:
        self._findings.append(finding)

    # --- lifecycle ---

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Close the underlying transport."""

    async def __aenter__(self) -> "BMCAdapter":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- feature stubs; subclasses override what they implement ---

    async def power_state(self) -> str:
        raise NotImplementedError(f"{self.vendor} does not implement power_state")

    async def power_action(self, action: str) -> None:
        raise NotImplementedError(f"{self.vendor} does not implement power_action")
