"""CyberPower PDU adapter — SNMP-based.

CyberPower-MIB (enterprise OID `.1.3.6.1.4.1.3808`). Covers
PDU15Mxxx / PDU20Mxxx / PDU30Mxxx switched and metered units.

CyberPower is the SMB / homelab pick: cheap, decent SNMP, no real
REST. We stick to SNMP exclusively.
"""
from __future__ import annotations

from ..findings import BMCFinding, matches_default_credential
from ._snmp import SNMPMixin
from .base import Outlet, OutletIdx, OutletState, PDUClient, PDUMetrics

# CyberPower hot paths (per CyberPower-MIB)
_BASE = "1.3.6.1.4.1.3808.1.1.3.3"
_OID_OUTLET_NAME = f"{_BASE}.3.3.1.2"
_OID_OUTLET_STATE = f"{_BASE}.5.1.1.4"   # 1=off, 2=on
_OID_OUTLET_CMD = f"{_BASE}.5.1.1.6"     # 1=immediateOn, 2=immediateOff, 3=immediateReboot

_STATE_MAP: dict[int, OutletState] = {1: "off", 2: "on"}
_CMD_ON = 1
_CMD_OFF = 2
_CMD_CYCLE = 3


class CyberPowerPDUClient(SNMPMixin, PDUClient):
    """CyberPower switched/metered PDU."""

    vendor = "cyberpower"

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
                    vendor="cyberpower",
                )
            )
        if community is not None and matches_default_credential(
            "cyberpower", "cyber", community
        ):
            self._findings.append(
                BMCFinding(
                    code="PDU_DEFAULT_CREDENTIALS_LIKELY",
                    severity="high",
                    detail=(
                        "SNMP community matches the documented CyberPower "
                        "default ('cyber')."
                    ),
                    vendor="cyberpower",
                )
            )

    async def outlet_on(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.{i}", _CMD_ON)

    async def outlet_off(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.{i}", _CMD_OFF)

    async def outlet_cycle(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._set(f"{_OID_OUTLET_CMD}.{i}", _CMD_CYCLE)

    async def outlet_state(self, idx: OutletIdx) -> OutletState:
        i = await self._resolve_idx(idx)
        raw = await self._get(f"{_OID_OUTLET_STATE}.{i}")
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
            state_v = states.get(f"{_OID_OUTLET_STATE}.{idx}")
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
