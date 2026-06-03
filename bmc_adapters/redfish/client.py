"""Redfish REST client for non-PiKVM BMCs.

Read-only L1 scope:
  - power state (`ComputerSystem.PowerState`)
  - thermal (`Chassis.Thermal.Temperatures[0].ReadingCelsius`)
  - health (`ComputerSystem.Status.HealthRollup`)

Write surface:
  - power actions (`ComputerSystem.Reset`, mapped from four friendly verbs)
  - virtual media insert / eject (`VirtualMedia.InsertMedia` / `EjectMedia`)

Auth model: tries Redfish SessionService first; falls back to HTTP Basic
auth when SessionService is missing or incomplete (returns 204 without a
token, returns 404, returns 405, etc.). Both modes are standards-compliant
— the DMTF Redfish spec explicitly defines Basic auth as the universal
fallback. Many real BMCs (and every mockup) behave this way; clients that
don't fall back are fragile.

All HTTP is via httpx.AsyncClient with a short connect timeout and TLS
verify defaulting to OFF (most BMCs ship self-signed certs by default).
Pass `verify_tls=True` if you've pinned a real cert on the BMC.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bmc_adapters.redfish.errors import RedfishError
from bmc_adapters.redfish.types import HeartbeatSnapshot

log = logging.getLogger(__name__)

# Power-action mapping. Friendly verbs → DMTF Redfish ResetType values.
# Caller passes one of the four keys; client maps to the BMC-side string.
ACTION_TO_REDFISH = {
    "on": "On",
    "off": "GracefulShutdown",
    "off_hard": "ForceOff",
    "cycle": "ForceRestart",
}

# Session lifetime varies by vendor; iDRAC 30 min, iLO 30 min, Lenovo 60
# min. Refresh ahead of the smallest common window.
SESSION_REFRESH_SECONDS = 60

# Connect + read timeouts. BMCs can be slow under load; 10s read is
# generous without letting a hung BMC stall a polling loop.
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=10.0)


# A password can be a plaintext string OR a zero-arg callable that returns
# the plaintext password (sync or async). Callable form lets callers keep
# secrets encrypted at rest and decrypt only at use-time without forking
# this library.
PasswordSource = str | Callable[[], str] | Callable[[], Awaitable[str]]


async def _resolve_password(source: PasswordSource) -> str:
    if isinstance(source, str):
        return source
    result = source()
    if isinstance(result, str):
        return result
    # It's an awaitable.
    return await result


class RedfishClient:
    """One client per BMC connection. Holds the session token across calls
    in the same task. Use as an async context manager for clean shutdown,
    or call `close()` manually.

    Args:
      base_url: BMC root, e.g. "https://idrac.example.com". Trailing slash
        is stripped.
      username: BMC login. Often "root" / "ADMIN" / "Administrator".
      password: plaintext password OR a callable (sync or async) returning
        the plaintext password. Callable form is for callers who keep
        secrets encrypted at rest.
      session_token: optional cached X-Auth-Token from a prior session.
        When provided alongside `session_expires_at`, the client will try
        the cached token before re-authenticating.
      session_expires_at: when the cached `session_token` expires.
      verify_tls: validate the BMC's TLS leaf certificate. Default false
        because ~98% of factory-shipped BMCs serve self-signed certs;
        flip to true once you've pinned a real cert.

    Example:
      async with RedfishClient(
          base_url="https://idrac.example.com",
          username="root",
          password="calvin",
      ) as client:
          snap = await client.heartbeat()
          await client.power_action("cycle")
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: PasswordSource,
        session_token: str | None = None,
        session_expires_at: datetime | None = None,
        verify_tls: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._password_source = password
        self.session_token = session_token
        self.session_expires_at = session_expires_at
        self.verify_tls = verify_tls
        # 'session' = X-Auth-Token from SessionService; 'basic' = HTTP
        # Basic auth fallback (set on the first request after SessionService
        # is found to be unavailable). 'unknown' on first construction.
        self._auth_mode: str = "unknown"
        self._basic_header: str | None = None
        # True once login refreshed the cached session token. Callers
        # who persist session tokens across processes can check this
        # after each operation and re-cache when set.
        self.session_dirty = False
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RedfishClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=HTTP_TIMEOUT,
                verify=self.verify_tls,
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- auth ----------------------------------------------------------

    def _token_fresh(self, now: datetime) -> bool:
        if not self.session_token or not self.session_expires_at:
            return False
        return self.session_expires_at - now > timedelta(seconds=SESSION_REFRESH_SECONDS)

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_mode == "session":
            return {"X-Auth-Token": self.session_token or ""}
        if self._auth_mode == "basic":
            return {"Authorization": self._basic_header or ""}
        return {}

    async def _ensure_auth(self) -> None:
        """Make sure we have a usable auth posture before issuing a request.
        Basic creds don't expire, so once we've fallen back to basic we
        stay there for the life of the client."""
        if self._auth_mode == "basic":
            return
        if self._auth_mode == "session" and self._token_fresh(datetime.now(UTC)):
            return
        await self._login()

    async def _login(self) -> None:
        password = await _resolve_password(self._password_source)

        c = await self._client()
        # Try SessionService first.
        try:
            resp = await c.post(
                "/redfish/v1/SessionService/Sessions",
                json={"UserName": self.username, "Password": password},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise RedfishError(f"BMC unreachable: {e}") from e

        token = resp.headers.get("x-auth-token") or resp.headers.get("X-Auth-Token")
        # Happy path: SessionService returned a token.
        if resp.status_code in (200, 201) and token:
            self.session_token = token
            self.session_expires_at = datetime.now(UTC) + timedelta(minutes=25)
            self.session_dirty = True
            self._auth_mode = "session"
            return

        # Treat as "SessionService unusable, fall back to Basic" when:
        #   - status is 204 No Content (mockups, some firmware)
        #   - status is 404 / 405 (SessionService endpoint missing)
        #   - status is 2xx but no X-Auth-Token came back
        #   - status is 401 / 403 (vendor rejects session POST but might
        #     accept the same creds via Basic — happens with some iLO 4)
        if (
            resp.status_code in (204, 404, 405)
            or (200 <= resp.status_code < 300)
            or resp.status_code in (401, 403)
        ):
            creds = f"{self.username}:{password}".encode()
            self._basic_header = "Basic " + base64.b64encode(creds).decode("ascii")
            self._auth_mode = "basic"
            # Probe with Basic to confirm before declaring success — avoids
            # silently falling back to creds the BMC will also reject.
            try:
                probe = await c.get(
                    "/redfish/v1", headers={"Authorization": self._basic_header}
                )
            except httpx.HTTPError as e:
                raise RedfishError(f"Basic-auth probe failed: {e}") from e
            if probe.status_code >= 400:
                raise RedfishError(
                    f"BMC rejected both SessionService (HTTP {resp.status_code}) "
                    f"and Basic auth (HTTP {probe.status_code}): credentials likely wrong"
                )
            return

        # Anything else is a hard failure.
        raise RedfishError(
            f"BMC SessionService unexpected response: HTTP {resp.status_code} {resp.text[:200]}"
        )

    async def _get(self, path: str) -> dict[str, Any]:
        await self._ensure_auth()
        c = await self._client()
        try:
            resp = await c.get(path, headers=self._auth_headers())
        except httpx.HTTPError as e:
            raise RedfishError(f"GET {path}: {e}") from e
        if resp.status_code == 401 and self._auth_mode == "session":
            # Token expired between checks (clock skew, vendor inconsistency).
            # Re-login once and retry. Basic creds don't expire so 401 there
            # is terminal.
            self.session_token = None
            await self._ensure_auth()
            resp = await c.get(path, headers=self._auth_headers())
        if resp.status_code >= 400:
            raise RedfishError(f"GET {path}: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json()  # type: ignore[no-any-return]
        except ValueError as e:
            raise RedfishError(f"GET {path}: non-JSON response: {resp.text[:120]}") from e

    async def _post_action(self, path: str, body: dict[str, Any]) -> None:
        await self._ensure_auth()
        c = await self._client()
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        resp = await c.post(path, json=body, headers=headers)
        if resp.status_code == 401 and self._auth_mode == "session":
            self.session_token = None
            await self._ensure_auth()
            headers = {**self._auth_headers(), "Content-Type": "application/json"}
            resp = await c.post(path, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RedfishError(
                f"POST {path}: HTTP {resp.status_code} {resp.text[:200]}"
            )

    # --- public API ----------------------------------------------------

    async def heartbeat(self) -> HeartbeatSnapshot:
        """Single poll cycle. Returns a snapshot suitable for caching or
        rendering. Raises RedfishError on protocol-level failure; the
        caller decides whether to treat that as transient or terminal."""
        # Find the first computer system (most chassis have exactly one).
        systems = await self._get("/redfish/v1/Systems")
        members = systems.get("Members", []) or []
        if not members:
            return HeartbeatSnapshot(online=True, power_state=None, cpu_temp_c=None, health=None)
        sys_href = members[0]["@odata.id"]
        system = await self._get(sys_href)

        power_state = system.get("PowerState")
        health = (system.get("Status") or {}).get("HealthRollup")

        cpu_temp = None
        chassis_uri = (system.get("Links") or {}).get("Chassis", [])
        if chassis_uri:
            try:
                thermal = await self._get(chassis_uri[0]["@odata.id"] + "/Thermal")
                temps = thermal.get("Temperatures", []) or []
                cpu_temp = _pick_temp(temps)
            except RedfishError as e:
                log.warning("redfish thermal pull failed: %s", e)

        return HeartbeatSnapshot(
            online=True,
            power_state=power_state,
            cpu_temp_c=cpu_temp,
            health=health,
        )

    async def power_action(self, action: str) -> None:
        """Map one of four friendly verbs (`on` / `off` / `off_hard` / `cycle`)
        to `ComputerSystem.Reset`."""
        if action not in ACTION_TO_REDFISH:
            raise RedfishError(f"unknown action {action!r}")
        systems = await self._get("/redfish/v1/Systems")
        members = systems.get("Members", []) or []
        if not members:
            raise RedfishError("BMC has no ComputerSystem resources")
        sys_href = members[0]["@odata.id"]
        await self._post_action(
            sys_href + "/Actions/ComputerSystem.Reset",
            {"ResetType": ACTION_TO_REDFISH[action]},
        )

    # --- virtual media -------------------------------------------------

    async def _virtual_media_collection(self) -> tuple[str, list[dict[str, Any]]]:
        """Walk /Managers → /VirtualMedia and return (manager_href, members).
        Vendor slots differ wildly (iDRAC: CD / RemovableDisk; iLO: 1 / 2;
        Supermicro: numeric), so we don't hard-code names — we inspect each
        slot's MediaTypes."""
        managers = await self._get("/redfish/v1/Managers")
        mgr_members = managers.get("Members", []) or []
        if not mgr_members:
            raise RedfishError("BMC exposes no Managers")
        mgr_href = mgr_members[0]["@odata.id"]
        vm_collection = await self._get(f"{mgr_href}/VirtualMedia")
        vm_members = vm_collection.get("Members", []) or []
        if not vm_members:
            raise RedfishError("BMC manager exposes no VirtualMedia slots")
        return mgr_href, vm_members

    async def _pick_cd_slot(self, vm_members: list[dict[str, Any]]) -> dict[str, Any]:
        """Inspect each VirtualMedia slot and pick the first one whose
        MediaTypes include CD or DVD. Fallback to the first slot if no
        CD-capable slot exists — older firmware sometimes omits the
        MediaTypes field entirely on its only slot."""
        slots: list[dict[str, Any]] = []
        for m in vm_members:
            full = await self._get(m["@odata.id"])
            full["@odata.id"] = m["@odata.id"]
            slots.append(full)
            media_types = full.get("MediaTypes", []) or []
            if "CD" in media_types or "DVD" in media_types:
                return full
        if slots:
            return slots[0]
        raise RedfishError("could not inspect any VirtualMedia slot")

    async def insert_virtual_media(self, image_url: str) -> str:
        """Mount the image at `image_url` on the first CD-capable slot.
        Returns the slot @odata.id used (caller persists for the matching
        eject path). Best-effort eject of any pre-existing media before
        insertion — some firmware (iDRAC 9 prior to 4.40) refuses the
        InsertMedia call if the slot is busy."""
        _, vm_members = await self._virtual_media_collection()
        slot = await self._pick_cd_slot(vm_members)
        slot_href = slot["@odata.id"]

        if slot.get("Inserted"):
            # Pre-eject. Tolerate failure: many BMCs auto-eject on insert.
            try:
                await self._post_action(
                    f"{slot_href}/Actions/VirtualMedia.EjectMedia", {}
                )
            except RedfishError:
                pass

        await self._post_action(
            f"{slot_href}/Actions/VirtualMedia.InsertMedia",
            {"Image": image_url, "Inserted": True, "WriteProtected": True},
        )
        return slot_href  # type: ignore[no-any-return]

    async def eject_virtual_media(self) -> int:
        """Eject any mounted media on every CD/DVD-capable slot. Returns
        the count of slots actually ejected. Best-effort across slots —
        a failure on one slot doesn't prevent attempting the others."""
        _, vm_members = await self._virtual_media_collection()
        ejected = 0
        for m in vm_members:
            full = await self._get(m["@odata.id"])
            if not full.get("Inserted"):
                continue
            try:
                await self._post_action(
                    m["@odata.id"] + "/Actions/VirtualMedia.EjectMedia", {}
                )
                ejected += 1
            except RedfishError as e:
                log.warning("eject failed on %s: %s", m["@odata.id"], e)
        return ejected


def _pick_temp(temps: list[dict[str, Any]]) -> float | None:
    """Pick the first CPU temperature reading; fall back to chassis inlet
    if no CPU sensor exists. Vendor names vary ("CPU 1 Temp", "Inlet Temp",
    "Ambient", "P1 Therm Margin"...) so we substring-match conservatively."""
    cpu_first: float | None = None
    inlet_first: float | None = None
    for t in temps:
        name = (t.get("Name") or "").lower()
        reading = t.get("ReadingCelsius")
        if reading is None:
            continue
        if "cpu" in name and cpu_first is None:
            cpu_first = float(reading)
        elif "inlet" in name and inlet_first is None:
            inlet_first = float(reading)
    return cpu_first if cpu_first is not None else inlet_first
