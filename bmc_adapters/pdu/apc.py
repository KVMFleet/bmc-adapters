"""APC (Schneider Electric) PDU adapter — SNMP-based.

The APC REST API on NMC3 is incomplete and version-fragile; SNMP is the
robust path. PowerNet-MIB enterprise OID is `.1.3.6.1.4.1.318`.

OIDs used (rPDU2 table — modern AP86xx+):

- Outlet command:  1.3.6.1.4.1.318.1.1.26.9.2.3.1.6  (write)
    1 = immediateOn, 2 = immediateOff, 3 = immediateReboot
- Outlet state:    1.3.6.1.4.1.318.1.1.26.9.3.5.1.5  (read)
    1 = off, 2 = on
- Outlet name:     1.3.6.1.4.1.318.1.1.26.9.2.3.1.4
- Outlet current:  1.3.6.1.4.1.318.1.1.26.10.2.3.1.5  (in 0.01 A)
- Outlet power:    1.3.6.1.4.1.318.1.1.26.10.2.3.1.6  (in W)
- Total power:     1.3.6.1.4.1.318.1.1.26.4.3.1.5
- Phase currents:  1.3.6.1.4.1.318.1.1.26.6.3.1.5
"""
from __future__ import annotations

from ..findings import BMCFinding, matches_default_credential
from ._snmp import SNMPMixin
from .base import Outlet, OutletIdx, OutletState, PDUClient, PDUMetrics

_OID_OUTLET_NAME = "1.3.6.1.4.1.318.1.1.26.9.2.3.1.4"
_OID_OUTLET_CMD = "1.3.6.1.4.1.318.1.1.26.9.2.3.1.6"
_OID_OUTLET_STATE = "1.3.6.1.4.1.318.1.1.26.9.3.5.1.5"
_OID_OUTLET_CURRENT = "1.3.6.1.4.1.318.1.1.26.10.2.3.1.5"
_OID_OUTLET_POWER = "1.3.6.1.4.1.318.1.1.26.10.2.3.1.6"
_OID_TOTAL_POWER = "1.3.6.1.4.1.318.1.1.26.4.3.1.5"
_OID_PHASE_CURRENT = "1.3.6.1.4.1.318.1.1.26.6.3.1.5"

_CMD_ON = 1
_CMD_OFF = 2
_CMD_CYCLE = 3

_STATE_MAP: dict[int, OutletState] = {1: "off", 2: "on"}


class APCPDUClient(SNMPMixin, PDUClient):
    """APC (Schneider Electric) PDU — AP86xx / AP88xx / AP89xx (NMC2/3)."""

    vendor = "apc"

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
                        "plaintext. Move to SNMPv3 (authPriv) for production "
                        "deployments."
                    ),
                    vendor="apc",
                )
            )
        if community is not None and matches_default_credential(
            "apc", "apc", community
        ):
            self._findings.append(
                BMCFinding(
                    code="PDU_DEFAULT_CREDENTIALS_LIKELY",
                    severity="high",
                    detail=(
                        "SNMP community matches the documented APC default "
                        "('apc'). Rotate before deploying to a shared management "
                        "network."
                    ),
                    vendor="apc",
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
        currents = dict(await self._walk(_OID_OUTLET_CURRENT))
        powers = dict(await self._walk(_OID_OUTLET_POWER))

        out: list[Outlet] = []
        for oid, name_v in names:
            idx = int(oid.rsplit(".", 1)[-1])
            state_v = states.get(_OID_OUTLET_STATE + f".{idx}")
            current_v = currents.get(_OID_OUTLET_CURRENT + f".{idx}")
            power_v = powers.get(_OID_OUTLET_POWER + f".{idx}")
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
                        float(int(current_v)) / 100.0
                        if current_v is not None
                        else None
                    ),
                    power_w=(float(int(power_v)) if power_v is not None else None),
                )
            )
        return sorted(out, key=lambda o: o.index)

    async def power_metrics(self) -> PDUMetrics:
        try:
            total = await self._get(f"{_OID_TOTAL_POWER}.1")
            total_w = float(int(total)) if total is not None else None
        except Exception:  # noqa: BLE001
            total_w = None
        phases = await self._walk(_OID_PHASE_CURRENT)
        phase_currents = tuple(
            float(int(v)) / 10.0 for _, v in phases if v is not None
        )
        return PDUMetrics(
            total_power_w=total_w,
            phase_currents_a=phase_currents,
        )

    async def set_outlet_name(self, idx: OutletIdx, name: str) -> None:
        i = await self._resolve_idx(idx)
        # SNMP SET expects an octet-string; pysnmp accepts a Python str.
        await self._set(f"{_OID_OUTLET_NAME}.{i}", name)  # type: ignore[arg-type]
