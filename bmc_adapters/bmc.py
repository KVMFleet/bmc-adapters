"""Top-level `BMC` orchestrator — composes the per-protocol adapters.

The pattern is borrowed from bmclib (github.com/bmc-toolbox/bmclib):
operators don't care which protocol they're talking — they want
`power.cycle("server-12")` to "just work" on whatever transport the
device exposes. The orchestrator walks its registered adapters and
dispatches to the first one that declares support for the requested
feature.

In v0.4.0 the orchestrator is intentionally thin — wrap any of
`RedfishClient`, `IPMIClient`, `PDUClient`, `wake_on_lan`. Future
versions can add prefer-protocol hints, automatic protocol probing,
and circuit-breaker behaviour.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Feature

if TYPE_CHECKING:
    from .ipmi import IPMIClient
    from .pdu.base import PDUClient
    from .redfish import RedfishClient


class BMC:
    """Composes a Redfish + IPMI + PDU adapter under a single object.

    Each adapter can be None; the orchestrator skips it on feature
    dispatch. `power_action` walks adapters in preferred order
    (Redfish → IPMI → PDU outlet cycle if the device's PDU mapping
    is known).
    """

    def __init__(
        self,
        *,
        redfish: "RedfishClient | None" = None,
        ipmi: "IPMIClient | None" = None,
        pdu: "PDUClient | None" = None,
        pdu_outlet: int | str | None = None,
    ) -> None:
        self.redfish = redfish
        self.ipmi = ipmi
        self.pdu = pdu
        self.pdu_outlet = pdu_outlet

    @property
    def features(self) -> frozenset[Feature]:
        out: set[Feature] = set()
        if self.redfish is not None:
            out.update({
                Feature.POWER_STATE, Feature.POWER_SET, Feature.SENSORS,
                Feature.INVENTORY, Feature.SEL_READ, Feature.BMC_RESET,
                Feature.VIRTUAL_MEDIA, Feature.BOOT_DEVICE_SET,
            })
        if self.ipmi is not None:
            out.update({
                Feature.POWER_STATE, Feature.POWER_SET, Feature.SENSORS,
                Feature.FRU, Feature.SEL_READ, Feature.SEL_CLEAR, Feature.SOL,
            })
        if self.pdu is not None and self.pdu_outlet is not None:
            out.update({Feature.OUTLET_CONTROL, Feature.OUTLET_METRICS,
                       Feature.POWER_SET})
        return frozenset(out)

    @property
    def findings(self) -> list[Any]:
        out: list[Any] = []
        for adapter in (self.redfish, self.ipmi, self.pdu):
            if adapter is None:
                continue
            out.extend(getattr(adapter, "findings", []) or [])
        return out

    async def power_action(self, action: str) -> str:
        """Try Redfish → IPMI → PDU-outlet-cycle in order.

        Returns the name of the transport that handled the action.
        """
        if self.redfish is not None:
            try:
                await self.redfish.power_action(action)  # type: ignore[arg-type]
                return "redfish"
            except Exception:  # noqa: BLE001
                pass
        if self.ipmi is not None:
            try:
                # IPMI verb mapping: 'cycle' → cycle, 'off' → soft, etc.
                from .ipmi.types import ACTION_TO_PYGHMI
                if action in ACTION_TO_PYGHMI:
                    await self.ipmi.power_action(action)  # type: ignore[arg-type]
                    return "ipmi"
            except Exception:  # noqa: BLE001
                pass
        if self.pdu is not None and self.pdu_outlet is not None:
            # PDU is the last resort: a hard outlet cycle.
            if action in ("off", "off_hard"):
                await self.pdu.outlet_off(self.pdu_outlet)
            elif action in ("on",):
                await self.pdu.outlet_on(self.pdu_outlet)
            else:
                await self.pdu.outlet_cycle(self.pdu_outlet)
            return "pdu"
        raise RuntimeError("No adapter could service the power action")

    async def aclose(self) -> None:
        for adapter in (self.redfish, self.ipmi, self.pdu):
            if adapter is None:
                continue
            try:
                await adapter.aclose()
            except Exception:  # noqa: BLE001
                pass
