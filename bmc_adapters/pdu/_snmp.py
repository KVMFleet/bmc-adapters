"""Shared SNMP helpers for SNMP-based PDU adapters.

pysnmp v6 split the high-level API across `pysnmp.hlapi.v1arch` (sync,
older) and `pysnmp.hlapi.v3arch` (sync + asyncio). We use v3arch.asyncio
exclusively.

Imports are lazy because the SNMP backends are an optional extra; the
PDU package emits a clear error if pysnmp isn't installed.
"""
from __future__ import annotations

from typing import Any


def _import_pysnmp() -> Any:
    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            UsmUserData,
            bulk_cmd,
            get_cmd,
            next_cmd,
            set_cmd,
            usmAesCfb128Protocol,
            usmHMACSHAAuthProtocol,
        )
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "pysnmp is required for SNMP-based PDU support. "
            "Install with `pip install kvmfleet-bmc-adapters[pdu]` or "
            "`pip install 'pysnmp>=6.2,<7'`."
        ) from e
    return {
        "CommunityData": CommunityData,
        "ContextData": ContextData,
        "ObjectIdentity": ObjectIdentity,
        "ObjectType": ObjectType,
        "SnmpEngine": SnmpEngine,
        "UdpTransportTarget": UdpTransportTarget,
        "UsmUserData": UsmUserData,
        "bulk_cmd": bulk_cmd,
        "get_cmd": get_cmd,
        "next_cmd": next_cmd,
        "set_cmd": set_cmd,
        "usmAesCfb128Protocol": usmAesCfb128Protocol,
        "usmHMACSHAAuthProtocol": usmHMACSHAAuthProtocol,
    }


class SNMPMixin:
    """Mixin: bundles a SnmpEngine + auth credentials + helper get/set."""

    host: str
    port: int
    timeout: float

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
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._snmp = _import_pysnmp()
        self._engine = self._snmp["SnmpEngine"]()
        if snmp_v3_user:
            self._auth = self._snmp["UsmUserData"](
                snmp_v3_user,
                auth_key,
                priv_key,
                authProtocol=self._snmp["usmHMACSHAAuthProtocol"],
                privProtocol=self._snmp["usmAesCfb128Protocol"],
            )
            self._v3 = True
        else:
            self._auth = self._snmp["CommunityData"](
                community or "public", mpModel=1
            )
            self._v3 = False

    async def _get(self, oid: str) -> Any:
        snmp = self._snmp
        transport = await snmp["UdpTransportTarget"].create(
            (self.host, self.port), timeout=self.timeout
        )
        err_ind, err_status, _, var_binds = await snmp["get_cmd"](
            self._engine, self._auth, transport, snmp["ContextData"](),
            snmp["ObjectType"](snmp["ObjectIdentity"](oid)),
        )
        if err_ind or err_status:
            raise RuntimeError(
                f"SNMP get {oid} failed: {err_ind or err_status.prettyPrint()}"
            )
        return var_binds[0][1]

    async def _set(self, oid: str, value: int) -> None:
        snmp = self._snmp
        transport = await snmp["UdpTransportTarget"].create(
            (self.host, self.port), timeout=self.timeout
        )
        err_ind, err_status, _, _ = await snmp["set_cmd"](
            self._engine, self._auth, transport, snmp["ContextData"](),
            snmp["ObjectType"](snmp["ObjectIdentity"](oid), value),
        )
        if err_ind or err_status:
            raise RuntimeError(
                f"SNMP set {oid}={value} failed: "
                f"{err_ind or err_status.prettyPrint()}"
            )

    async def _walk(self, base_oid: str) -> list[tuple[str, Any]]:
        """Walk a subtree, returning (oid_str, value) pairs."""
        snmp = self._snmp
        transport = await snmp["UdpTransportTarget"].create(
            (self.host, self.port), timeout=self.timeout
        )
        out: list[tuple[str, Any]] = []
        cur = snmp["ObjectType"](snmp["ObjectIdentity"](base_oid))
        while True:
            err_ind, err_status, _, var_binds = await snmp["next_cmd"](
                self._engine, self._auth, transport, snmp["ContextData"](), cur,
                lexicographicMode=False,
            )
            if err_ind or err_status:
                break
            if not var_binds:
                break
            name, value = var_binds[0]
            name_str = str(name)
            if not name_str.startswith(base_oid):
                break
            out.append((name_str, value))
            cur = snmp["ObjectType"](snmp["ObjectIdentity"](name_str))
        return out
