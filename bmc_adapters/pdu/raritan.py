"""Raritan PX2 / PX3 / PX4 adapter — JSON-RPC over HTTPS.

Raritan publishes the best vendor PDU API of the major brands. Endpoints
are JSON-RPC 2.0 over HTTPS, scoped per object path
(`/model/pdu/0/outlet/{idx}`), with session-token auth.

Quirk: outlets are 0-indexed on the wire but 1-indexed on the chassis
labels. The adapter normalises — callers use 1-indexed numbers like
they see on the device.

Default-credential note: PX2 ships `admin/raritan`; PX4 (and rebranded
Legrand PDUs since 2020) ships `admin/legrand@1`. Both are matched by
`matches_default_credential` in findings.py.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..findings import BMCFinding, matches_default_credential
from .base import Outlet, OutletIdx, OutletState, PDUClient, PDUMetrics


class RaritanPDUClient(PDUClient):
    """Raritan / Legrand PDU JSON-RPC client."""

    vendor = "raritan"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify: bool = True,
        timeout: float = 10.0,
    ) -> None:
        PDUClient.__init__(self)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            verify=verify,
            timeout=timeout,
        )
        self._user = username
        self._pw = password
        self._token: str | None = None
        if not base_url.startswith("https://"):
            self._findings.append(
                BMCFinding(
                    code="PDU_HTTP_NO_TLS",
                    severity="high",
                    detail=(
                        "Raritan JSON-RPC endpoint is plain HTTP — credentials "
                        "and outlet commands cross the wire unencrypted."
                    ),
                    vendor="raritan",
                )
            )
        if matches_default_credential("raritan", username, password) or \
                matches_default_credential("legrand", username, password):
            self._findings.append(
                BMCFinding(
                    code="PDU_DEFAULT_CREDENTIALS_LIKELY",
                    severity="high",
                    detail=(
                        f"User '{username}' with documented default password "
                        "for Raritan / Legrand PDU."
                    ),
                    vendor="raritan",
                )
            )

    async def _login(self) -> None:
        r = await self._http.post(
            "/auth/login",
            json={"username": self._user, "password": self._pw},
        )
        r.raise_for_status()
        body = r.json()
        token = body.get("token") or body.get("authToken")
        if not isinstance(token, str):
            raise RuntimeError("Raritan login: no token returned")
        self._token = token
        self._http.headers["X-SessionToken"] = token

    async def _rpc(
        self,
        path: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._token is None:
            await self._login()
        r = await self._http.post(
            path,
            json={
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": 1,
            },
        )
        r.raise_for_status()
        body = r.json()
        if "error" in body and body["error"]:
            raise RuntimeError(f"Raritan RPC {method}: {body['error']}")
        return body.get("result")

    # --- public surface ---

    async def outlet_on(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._rpc(
            f"/model/pdu/0/outlet/{i - 1}",
            "Outlet.setPowerState",
            {"pstate": True},
        )

    async def outlet_off(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._rpc(
            f"/model/pdu/0/outlet/{i - 1}",
            "Outlet.setPowerState",
            {"pstate": False},
        )

    async def outlet_cycle(self, idx: OutletIdx) -> None:
        i = await self._resolve_idx(idx)
        await self._rpc(
            f"/model/pdu/0/outlet/{i - 1}",
            "Outlet.cyclePowerState",
            {},
        )

    async def outlet_state(self, idx: OutletIdx) -> OutletState:
        i = await self._resolve_idx(idx)
        result = await self._rpc(
            f"/model/pdu/0/outlet/{i - 1}",
            "Outlet.getState",
            {},
        )
        if not isinstance(result, dict):
            return "unknown"
        state = result.get("pstate")
        if state is True:
            return "on"
        if state is False:
            return "off"
        return "unknown"

    async def list_outlets(self) -> list[Outlet]:
        result = await self._rpc("/model/pdu/0", "getOutlets", {})
        outlets: list[Outlet] = []
        if not isinstance(result, list):
            return outlets
        for wire_idx, raw in enumerate(result):
            if not isinstance(raw, dict):
                continue
            label = raw.get("label") or raw.get("name") or f"Outlet {wire_idx + 1}"
            state_raw = raw.get("state") or raw.get("pstate")
            state: OutletState
            if state_raw in (True, "on", "On", "ON"):
                state = "on"
            elif state_raw in (False, "off", "Off", "OFF"):
                state = "off"
            else:
                state = "unknown"
            outlets.append(
                Outlet(
                    index=wire_idx + 1,
                    name=str(label),
                    state=state,
                    current_a=_to_float(raw.get("current_a") or raw.get("current")),
                    power_w=_to_float(raw.get("power_w") or raw.get("power")),
                )
            )
        return outlets

    async def power_metrics(self) -> PDUMetrics:
        result = await self._rpc("/model/pdu/0", "getMetering", {})
        if not isinstance(result, dict):
            return PDUMetrics()
        return PDUMetrics(
            total_power_w=_to_float(result.get("totalPower")),
            total_energy_kwh=_to_float(result.get("totalEnergy")),
        )

    async def aclose(self) -> None:
        if self._token is not None:
            try:
                await self._http.post("/auth/logout")
            except Exception:  # noqa: BLE001
                pass
        await self._http.aclose()


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
