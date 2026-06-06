"""Tripp Lite (now Eaton) PDU adapter — SNMP-based.

Tripp Lite WEBCARDLX firmware uses TRIPPLITE-PRODUCTS-MIB
(enterprise OID `.1.3.6.1.4.1.850.1`), distinct from Eaton's
EATON-EPDU-MIB despite both being Eaton-owned now. We target
PDUMH / PDUMV / PDU3 series.

Note: Tripp Lite also exposes a "JSON" web interface, but it's
undocumented and changes per firmware revision — we deliberately
do not use it.
"""
from __future__ import annotations

from ..findings import BMCFinding
from ._snmp import SNMPMixin
from .base import Outlet, OutletIdx, OutletState, PDUClient, PDUMetrics

# Tripp Lite hot paths under TRIPPLITE-PRODUCTS-MIB
_BASE = "1.3.6.1.4.1.850.1.1.3.2.3.3.1"
_OID_OUTLET_NAME = f"{_BASE}.2"
_OID_OUTLET_STATE = f"{_BASE}.3"   # 1 = on, 2 = off (per device line)
_OID_OUTLET_CMD = f"{_BASE}.4"     # 1=off, 2=on, 3=cycle

_STATE_MAP: dict[int, OutletState] = {1: "on", 2: "off"}
_CMD_OFF = 1
_CMD_ON = 2
_CMD_CYCLE = 3


class TrippLitePDUClient(SNMPMixin, PDUClient):
    """Tripp Lite PDUMH / PDUMV / PDU3 (WEBCARDLX)."""

    vendor = "tripplite"

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
                    detail="Connecting via SNMPv2c — community in plaintext.",
                    vendor="tripplite",
                )
            )

    async def outlet_on(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.1.{i}", _CMD_ON)

    async def outlet_off(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.1.{i}", _CMD_OFF)

    async def outlet_cycle(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.1.{i}", _CMD_CYCLE)

    async def outlet_state(self, idx: OutletIdx) -> OutletState:
        i = await self._resolve_idx(idx)
        raw = await self._get(f"{_OID_OUTLET_STATE}.1.{i}")
        return _STATE_MAP.get(int(raw), "unknown")

    async def list_outlets(self) -> list[Outlet]:
        names = await self._walk(_OID_OUTLET_NAME)
        states = dict(await self._walk(_OID_OUTLET_STATE))
        out: list[Outlet] = []
        for oid, name_v in names:
            try:
                idx = int(oid.rsplit(".", 1)[-1])
            except ValueError:
                continue
            state_v = states.get(f"{_OID_OUTLET_STATE}.1.{idx}")
            state: OutletState = "unknown"
            if state_v is not None:
                try:
                    state = _STATE_MAP.get(int(state_v), "unknown")
                except (TypeError, ValueError):
                    state = "unknown"
            out.append(Outlet(index=idx, name=str(name_v), state=state))
        return sorted(out, key=lambda o: o.index)

    async def power_metrics(self) -> PDUMetrics:
        return PDUMetrics()
