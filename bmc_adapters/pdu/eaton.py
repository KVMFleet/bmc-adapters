"""Eaton ePDU G4 adapter — SNMP-based.

EATON-EPDU-MIB enterprise OID is `.1.3.6.1.4.1.534`. We target the G4
hot-paths under `eatonEpduBase = 1.3.6.1.4.1.534.6.6.7`.

For older G3 hardware the OIDs sit under a different sub-tree
(`pduOutletCommand`). We don't cover G3 in v0.4.0 — the G3 installed
base is shrinking and Eaton itself has moved on.
"""
from __future__ import annotations

from ..findings import BMCFinding
from ._snmp import SNMPMixin
from .base import Outlet, OutletIdx, OutletState, PDUClient, PDUMetrics

# Eaton G4 hot paths (per EATON-EPDU-MIB / "Network-M2 Reference")
_OID_OUTLET_CONTROL_STATUS = "1.3.6.1.4.1.534.6.6.7.6.6.1.2"  # 0=off, 1=on
_OID_OUTLET_CONTROL_OFF = "1.3.6.1.4.1.534.6.6.7.6.6.1.3"
_OID_OUTLET_CONTROL_ON = "1.3.6.1.4.1.534.6.6.7.6.6.1.4"
_OID_OUTLET_CONTROL_REBOOT = "1.3.6.1.4.1.534.6.6.7.6.6.1.5"
_OID_OUTLET_NAME = "1.3.6.1.4.1.534.6.6.7.6.1.1.3"
_OID_OUTLET_CURRENT_MA = "1.3.6.1.4.1.534.6.6.7.6.4.1.3"
_OID_OUTLET_WH = "1.3.6.1.4.1.534.6.6.7.6.5.1.3"

_STATE_MAP: dict[int, OutletState] = {0: "off", 1: "on", 2: "off", 3: "on"}


class EatonPDUClient(SNMPMixin, PDUClient):
    """Eaton ePDU G4 (Network-M2 / Network-M3 card)."""

    vendor = "eaton"

    def __init__(
        self,
        host: str,
        *,
        community: str | None = None,
        snmp_v3_user: str | None = None,
        auth_key: str | None = None,
        priv_key: str | None = None,
        port: int = 161,
        timeout: float = 3.0,
        allow_snmpv2c: bool = True,
    ) -> None:
        SNMPMixin.__init__(
            self,
            host,
            community=community,
            snmp_v3_user=snmp_v3_user,
            auth_key=auth_key,
            priv_key=priv_key,
            port=port,
            timeout=timeout,
        )
        PDUClient.__init__(self)
        if not self._v3:
            if not allow_snmpv2c:
                raise PermissionError(
                    "SNMPv2c is plaintext. Pass `allow_snmpv2c=True` to accept."
                )
            self._findings.append(
                BMCFinding(
                    code="PDU_SNMPV2C_PLAINTEXT",
                    severity="medium",
                    detail=(
                        "Connecting via SNMPv2c — community string is sent in "
                        "plaintext."
                    ),
                    vendor="eaton",
                )
            )

    async def outlet_on(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CONTROL_ON}.0.{i}", 1)

    async def outlet_off(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CONTROL_OFF}.0.{i}", 1)

    async def outlet_cycle(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CONTROL_REBOOT}.0.{i}", 1)

    async def outlet_state(self, idx: OutletIdx) -> OutletState:
        i = await self._resolve_idx(idx)
        raw = await self._get(f"{_OID_OUTLET_CONTROL_STATUS}.0.{i}")
        return _STATE_MAP.get(int(raw), "unknown")

    async def list_outlets(self) -> list[Outlet]:
        names = await self._walk(_OID_OUTLET_NAME)
        states = dict(await self._walk(_OID_OUTLET_CONTROL_STATUS))
        currents = dict(await self._walk(_OID_OUTLET_CURRENT_MA))

        out: list[Outlet] = []
        for oid, name_v in names:
            tail = oid[len(_OID_OUTLET_NAME) + 1:]
            try:
                idx = int(tail.rsplit(".", 1)[-1])
            except ValueError:
                continue
            state_v = states.get(f"{_OID_OUTLET_CONTROL_STATUS}.0.{idx}")
            current_v = currents.get(f"{_OID_OUTLET_CURRENT_MA}.0.{idx}")
            state: OutletState = "unknown"
            if state_v is not None:
                try:
                    state = _STATE_MAP.get(int(state_v), "unknown")
                except (TypeError, ValueError):
                    state = "unknown"
            out.append(
                Outlet(
                    index=idx,
                    name=str(name_v),
                    state=state,
                    current_a=(
                        float(int(current_v)) / 1000.0
                        if current_v is not None
                        else None
                    ),
                )
            )
        return sorted(out, key=lambda o: o.index)

    async def power_metrics(self) -> PDUMetrics:
        # Whole-PDU rollup OIDs vary across G4 firmware revisions; the
        # safest cross-firmware path is to sum outlet currents/power.
        outlets = await self.list_outlets()
        total_a = sum(o.current_a or 0 for o in outlets)
        # No reliable scalar OID for cumulative energy across all G4
        # firmwares — leave unset rather than guess.
        return PDUMetrics(
            phase_currents_a=(total_a,),
        )
