"""Redfish REST client for non-PiKVM BMCs.

Reads:
  - heartbeat (power, CPU temp, health rollup)
  - vendor detection
  - system identity (manufacturer, model, serial, BIOS / BMC firmware)
  - sensors (full thermal, fans, PSUs, chassis power metrics)
  - hardware inventory (CPUs, memory, drives, volumes, NICs, firmware)
  - boot configuration (current + persistent + one-time override)
  - network configuration of the BMC management interface
  - System Event Log (Lifecycle Log on iDRAC, IML on iLO)
  - BMC user list (read-only — no CRUD)
  - license info (best-effort, vendor-specific)
  - chassis health rollup (per-subsystem)

Writes:
  - power actions (`ComputerSystem.Reset`, mapped from friendly verbs)
  - virtual media insert / eject
  - boot-order / boot-source override
  - clear SEL
  - chassis indicator LED on/off
  - BMC self-reset (recovery)
  - NMI trigger (kernel-panic / dump)

Out of scope by design — these would force vendor-specific subclasses
and a different library shape:
  - Firmware update binaries (vendor-specific delivery, signed images)
  - BIOS attribute CRUD (vendor-quirky attribute trees)
  - RAID controller configuration (vendor-specific volumes / spares)
  - BMC user CRUD (different shape; auth concern)

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
from bmc_adapters.redfish.types import (
    BmcUser,
    BootConfig,
    FanReading,
    FirmwareComponent,
    HealthRollup,
    HeartbeatSnapshot,
    LicenseInfo,
    MemoryModule,
    NetworkAdapter,
    NetworkInfo,
    PowerMetrics,
    PowerSupplyReading,
    ProcessorInfo,
    SelEntry,
    StorageDrive,
    StorageVolume,
    SystemInfo,
    TemperatureReading,
)

log = logging.getLogger(__name__)

# Power-action mapping. Friendly verbs → DMTF Redfish ResetType values.
# Caller passes one of the four keys; client maps to the BMC-side string.
ACTION_TO_REDFISH = {
    "on": "On",
    "off": "GracefulShutdown",
    "off_hard": "ForceOff",
    "cycle": "ForceRestart",
    # "reboot" = ACPI-graceful restart (asks the OS to shut down and
    # boot, vs cycle's hard power-cycle). Vendors that don't support
    # GracefulRestart will surface a 4xx, which our caller handles.
    "reboot": "GracefulRestart",
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

    async def detect_vendor(self) -> str:
        """Probe `/redfish/v1` and the first Manager to identify the BMC
        vendor. Returns one of "idrac" / "ilo" / "supermicro" /
        "lenovo-xcc" / "redfish-generic". Falls back to "redfish-generic"
        when no vendor-specific markers are present (e.g., OpenBMC, mock
        BMCs).

        Detection is best-effort and operator-overrideable — some vendors
        badge each other's hardware, and firmware revisions change Oem
        keys. Treat the result as a smart default, not authoritative."""
        root = await self._get("/redfish/v1")
        oem = (root.get("Oem") or {}) if isinstance(root.get("Oem"), dict) else {}
        # Oem keys are vendor-named (Dell, Hpe, Supermicro, Lenovo).
        # Case-fold for safety; some firmware emits "HPE" vs "Hpe".
        oem_keys_lc = {k.lower() for k in oem.keys()}
        if "dell" in oem_keys_lc:
            return "idrac"
        if "hpe" in oem_keys_lc or "hp" in oem_keys_lc:
            return "ilo"
        if "supermicro" in oem_keys_lc:
            return "supermicro"
        if "lenovo" in oem_keys_lc:
            return "lenovo-xcc"

        # Fallback: probe Manager @odata.id — iDRAC.Embedded.1, etc.
        try:
            managers = await self._get("/redfish/v1/Managers")
            for m in managers.get("Members", []) or []:
                mid = (m.get("@odata.id") or "").lower()
                if "idrac" in mid:
                    return "idrac"
                if "ilo" in mid or "hpe" in mid:
                    return "ilo"
                if "supermicro" in mid:
                    return "supermicro"
                if "lenovo" in mid or "xclarity" in mid:
                    return "lenovo-xcc"
        except RedfishError:
            pass

        return "redfish-generic"

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

    # --- Internal helpers for path discovery -------------------------
    # The Redfish spec doesn't fix the @odata.id paths — vendors pick
    # them. These helpers cache the first System / Chassis / Manager
    # paths so subsequent calls don't re-walk the collection.

    async def _first_system_path(self) -> str:
        if getattr(self, "_cached_system_path", None):
            return self._cached_system_path  # type: ignore[return-value]
        systems = await self._get("/redfish/v1/Systems")
        members = systems.get("Members") or []
        if not members:
            raise RedfishError("BMC exposes no Systems")
        self._cached_system_path = members[0]["@odata.id"]
        return self._cached_system_path

    async def _first_chassis_path(self) -> str:
        if getattr(self, "_cached_chassis_path", None):
            return self._cached_chassis_path  # type: ignore[return-value]
        chassis = await self._get("/redfish/v1/Chassis")
        members = chassis.get("Members") or []
        if not members:
            raise RedfishError("BMC exposes no Chassis")
        self._cached_chassis_path = members[0]["@odata.id"]
        return self._cached_chassis_path

    async def _first_manager_path(self) -> str:
        if getattr(self, "_cached_manager_path", None):
            return self._cached_manager_path  # type: ignore[return-value]
        managers = await self._get("/redfish/v1/Managers")
        members = managers.get("Members") or []
        if not members:
            raise RedfishError("BMC exposes no Managers")
        self._cached_manager_path = members[0]["@odata.id"]
        return self._cached_manager_path

    @staticmethod
    def _status(obj: dict[str, Any]) -> str | None:
        s = obj.get("Status") or {}
        return s.get("HealthRollup") or s.get("Health")

    @staticmethod
    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        # Redfish uses ISO 8601; some firmware ships trailing "Z" or
        # offset-aware. fromisoformat handles modern firmware; older
        # iDRAC may ship "2024-03-14T12:34:56" without TZ.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    # --- System identity ---------------------------------------------

    async def system_info(self) -> SystemInfo:
        """Identity + BIOS / BMC firmware versions for the first system.

        Pulled from /Systems/{id} plus the first chassis (for the
        model fallback) and the first manager (for the BMC firmware
        version). Returns a SystemInfo with `None` for any field the
        vendor leaves blank."""
        system = await self._get(await self._first_system_path())
        chassis = None
        try:
            chassis = await self._get(await self._first_chassis_path())
        except RedfishError:
            pass
        manager = None
        try:
            manager = await self._get(await self._first_manager_path())
        except RedfishError:
            pass

        model = system.get("Model") or (chassis or {}).get("Model")
        return SystemInfo(
            manufacturer=system.get("Manufacturer") or (chassis or {}).get("Manufacturer"),
            model=model,
            serial_number=system.get("SerialNumber") or (chassis or {}).get("SerialNumber"),
            asset_tag=system.get("AssetTag") or (chassis or {}).get("AssetTag"),
            sku=system.get("SKU"),
            host_name=system.get("HostName"),
            uuid=system.get("UUID"),
            bios_version=system.get("BiosVersion"),
            bmc_firmware_version=(manager or {}).get("FirmwareVersion"),
            bmc_model=(manager or {}).get("Model"),
        )

    # --- Sensors -----------------------------------------------------

    async def temperatures(self) -> list[TemperatureReading]:
        """Full thermal-sensor list from /Chassis/{id}/Thermal.

        Newer firmware (Redfish 1.13+) moved to ThermalSubsystem +
        TemperatureSensors; we try the legacy path first because
        every vendor still supports it."""
        chassis_path = await self._first_chassis_path()
        try:
            thermal = await self._get(f"{chassis_path}/Thermal")
        except RedfishError:
            return []
        out: list[TemperatureReading] = []
        for t in thermal.get("Temperatures") or []:
            reading = t.get("ReadingCelsius")
            out.append(TemperatureReading(
                name=t.get("Name") or t.get("MemberId") or "",
                reading_c=float(reading) if reading is not None else None,
                upper_non_critical_c=t.get("UpperThresholdNonCritical"),
                upper_critical_c=t.get("UpperThresholdCritical"),
                status=self._status(t),
                physical_context=t.get("PhysicalContext"),
            ))
        return out

    async def fans(self) -> list[FanReading]:
        """Fan readings (RPM or PWM%). Same path as temperatures."""
        chassis_path = await self._first_chassis_path()
        try:
            thermal = await self._get(f"{chassis_path}/Thermal")
        except RedfishError:
            return []
        out: list[FanReading] = []
        for f in thermal.get("Fans") or []:
            reading = f.get("Reading")
            units = (f.get("ReadingUnits") or "").upper()
            rpm = int(reading) if reading is not None and units == "RPM" else None
            pct = int(reading) if reading is not None and units == "PERCENT" else None
            out.append(FanReading(
                name=f.get("Name") or f.get("MemberId") or "",
                reading_rpm=rpm,
                reading_percent=pct,
                status=self._status(f),
                upper_non_critical_rpm=f.get("UpperThresholdNonCritical"),
                lower_non_critical_rpm=f.get("LowerThresholdNonCritical"),
            ))
        return out

    async def power_supplies(self) -> list[PowerSupplyReading]:
        """PSU readings. Path: /Chassis/{id}/Power."""
        chassis_path = await self._first_chassis_path()
        try:
            power = await self._get(f"{chassis_path}/Power")
        except RedfishError:
            return []
        red_status = None
        for r in power.get("Redundancy") or []:
            red_status = self._status(r)
            break
        out: list[PowerSupplyReading] = []
        for p in power.get("PowerSupplies") or []:
            line = p.get("LineInputVoltageType")
            input_range = p.get("InputRanges") or []
            input_v = None
            if input_range:
                input_v = input_range[0].get("OutputWattage")  # fallback
            out.append(PowerSupplyReading(
                name=p.get("Name") or p.get("MemberId") or "",
                model=p.get("Model"),
                serial_number=p.get("SerialNumber"),
                status=self._status(p),
                power_capacity_w=p.get("PowerCapacityWatts"),
                power_output_w=p.get("PowerOutputWatts") or p.get("LastPowerOutputWatts"),
                input_voltage_v=p.get("LineInputVoltage"),
                input_power_w=p.get("PowerInputWatts"),
                line_input_voltage_type=line,
                redundancy_status=red_status,
            ))
        return out

    async def power_metrics(self) -> PowerMetrics:
        """Chassis-aggregated power consumption. Most BMCs surface
        this even when they refuse to expose individual PSU output."""
        chassis_path = await self._first_chassis_path()
        try:
            power = await self._get(f"{chassis_path}/Power")
        except RedfishError:
            return PowerMetrics(None, None, None, None, None)
        controls = power.get("PowerControl") or []
        if not controls:
            return PowerMetrics(None, None, None, None, None)
        pc = controls[0]
        metrics = pc.get("PowerMetrics") or {}
        limit = pc.get("PowerLimit") or {}
        return PowerMetrics(
            consumed_w=pc.get("PowerConsumedWatts"),
            average_w=metrics.get("AverageConsumedWatts"),
            min_w=metrics.get("MinConsumedWatts"),
            max_w=metrics.get("MaxConsumedWatts"),
            limit_w=limit.get("LimitInWatts"),
        )

    # --- Hardware inventory -----------------------------------------

    async def processor_inventory(self) -> list[ProcessorInfo]:
        """CPU sockets. Pulled from /Systems/{id}/Processors."""
        sys_path = await self._first_system_path()
        try:
            collection = await self._get(f"{sys_path}/Processors")
        except RedfishError:
            return []
        out: list[ProcessorInfo] = []
        for m in collection.get("Members") or []:
            try:
                p = await self._get(m["@odata.id"])
            except RedfishError:
                continue
            out.append(ProcessorInfo(
                id=p.get("Id") or "",
                socket=p.get("Socket"),
                model=p.get("Model"),
                manufacturer=p.get("Manufacturer"),
                instruction_set=p.get("InstructionSet"),
                max_speed_mhz=p.get("MaxSpeedMHz"),
                total_cores=p.get("TotalCores"),
                total_threads=p.get("TotalThreads"),
                status=self._status(p),
            ))
        return out

    async def memory_inventory(self) -> list[MemoryModule]:
        """DIMM inventory. Pulled from /Systems/{id}/Memory."""
        sys_path = await self._first_system_path()
        try:
            collection = await self._get(f"{sys_path}/Memory")
        except RedfishError:
            return []
        out: list[MemoryModule] = []
        for m in collection.get("Members") or []:
            try:
                d = await self._get(m["@odata.id"])
            except RedfishError:
                continue
            out.append(MemoryModule(
                id=d.get("Id") or "",
                name=d.get("Name") or d.get("DeviceLocator"),
                capacity_mib=d.get("CapacityMiB"),
                operating_speed_mhz=d.get("OperatingSpeedMhz"),
                rated_speed_mhz=d.get("AllowedSpeedsMHz", [None])[0] if d.get("AllowedSpeedsMHz") else None,
                manufacturer=d.get("Manufacturer"),
                part_number=d.get("PartNumber"),
                serial_number=d.get("SerialNumber"),
                memory_type=d.get("MemoryDeviceType"),
                channel=d.get("MemoryLocation", {}).get("Channel") if isinstance(d.get("MemoryLocation"), dict) else None,
                status=self._status(d),
            ))
        return out

    async def drive_inventory(self, max_drives: int | None = None) -> list[StorageDrive]:
        """Physical drives across all storage controllers.

        Walks /Systems/{id}/Storage → each Storage → Drives. Pass
        `max_drives` to cap (default unlimited) — useful on dense
        chassis with 48+ drives where the per-drive GET adds up."""
        sys_path = await self._first_system_path()
        try:
            storage_collection = await self._get(f"{sys_path}/Storage")
        except RedfishError:
            return []
        out: list[StorageDrive] = []
        for sm in storage_collection.get("Members") or []:
            try:
                storage = await self._get(sm["@odata.id"])
            except RedfishError:
                continue
            for dm in storage.get("Drives") or []:
                try:
                    d = await self._get(dm["@odata.id"])
                except RedfishError:
                    continue
                out.append(StorageDrive(
                    id=d.get("Id") or "",
                    name=d.get("Name"),
                    model=d.get("Model"),
                    manufacturer=d.get("Manufacturer"),
                    serial_number=d.get("SerialNumber"),
                    capacity_bytes=d.get("CapacityBytes"),
                    media_type=d.get("MediaType"),
                    protocol=d.get("Protocol"),
                    rotation_speed_rpm=d.get("RotationSpeedRPM"),
                    predicted_life_left_percent=d.get("PredictedMediaLifeLeftPercent"),
                    status=self._status(d),
                    failure_predicted=d.get("FailurePredicted"),
                ))
                if max_drives is not None and len(out) >= max_drives:
                    return out
        return out

    async def volume_inventory(self) -> list[StorageVolume]:
        """Logical volumes (RAID arrays, LVM). Read-only — we don't
        expose CRUD (vendor-quirk hell)."""
        sys_path = await self._first_system_path()
        try:
            storage_collection = await self._get(f"{sys_path}/Storage")
        except RedfishError:
            return []
        out: list[StorageVolume] = []
        for sm in storage_collection.get("Members") or []:
            try:
                storage = await self._get(sm["@odata.id"])
            except RedfishError:
                continue
            vols = storage.get("Volumes")
            if not vols:
                continue
            vol_href = vols.get("@odata.id") if isinstance(vols, dict) else None
            if not vol_href:
                continue
            try:
                vol_collection = await self._get(vol_href)
            except RedfishError:
                continue
            for vm in vol_collection.get("Members") or []:
                try:
                    v = await self._get(vm["@odata.id"])
                except RedfishError:
                    continue
                drives_refs = [d.get("@odata.id") for d in (v.get("Links", {}) or {}).get("Drives") or [] if d.get("@odata.id")]
                out.append(StorageVolume(
                    id=v.get("Id") or "",
                    name=v.get("Name"),
                    raid_type=v.get("RAIDType") or v.get("VolumeType"),
                    capacity_bytes=v.get("CapacityBytes"),
                    block_size_bytes=v.get("BlockSizeBytes"),
                    status=self._status(v),
                    drives=drives_refs,
                ))
        return out

    async def network_adapter_inventory(self) -> list[NetworkAdapter]:
        """Host-side NICs (not the BMC NIC). Pulled from
        /Chassis/{id}/NetworkAdapters."""
        chassis_path = await self._first_chassis_path()
        try:
            collection = await self._get(f"{chassis_path}/NetworkAdapters")
        except RedfishError:
            return []
        out: list[NetworkAdapter] = []
        for m in collection.get("Members") or []:
            try:
                a = await self._get(m["@odata.id"])
            except RedfishError:
                continue
            ports = a.get("Controllers") or []
            port_count = None
            if ports:
                port_count = sum(c.get("ControllerCapabilities", {}).get("NetworkPortCount", 0) for c in ports)
                if port_count == 0:
                    port_count = None
            fw = None
            if ports:
                fw_pkg = ports[0].get("FirmwarePackageVersion")
                fw = fw_pkg
            out.append(NetworkAdapter(
                id=a.get("Id") or "",
                name=a.get("Name"),
                manufacturer=a.get("Manufacturer"),
                model=a.get("Model"),
                serial_number=a.get("SerialNumber"),
                part_number=a.get("PartNumber"),
                firmware_version=fw,
                port_count=port_count,
                status=self._status(a),
            ))
        return out

    async def firmware_inventory(self) -> list[FirmwareComponent]:
        """Firmware components from /UpdateService/FirmwareInventory.

        Vendors disagree on what's a component: iDRAC enumerates 30+
        entries (BIOS, BMC, each NIC, each drive, RAID controller,
        PSU FW); iLO is similar; Supermicro tends to expose fewer.

        Best-effort: empty list when the BMC doesn't expose
        UpdateService (some Supermicro and older OpenBMC builds)."""
        try:
            update_service = await self._get("/redfish/v1/UpdateService")
        except RedfishError:
            return []
        fw = update_service.get("FirmwareInventory") or {}
        fw_href = fw.get("@odata.id") if isinstance(fw, dict) else None
        if not fw_href:
            return []
        try:
            collection = await self._get(fw_href)
        except RedfishError:
            return []
        out: list[FirmwareComponent] = []
        for m in collection.get("Members") or []:
            try:
                c = await self._get(m["@odata.id"])
            except RedfishError:
                continue
            out.append(FirmwareComponent(
                id=c.get("Id") or "",
                name=c.get("Name") or "",
                version=c.get("Version"),
                manufacturer=c.get("Manufacturer"),
                release_date=self._parse_dt(c.get("ReleaseDate")),
                software_id=c.get("SoftwareId"),
                updatable=c.get("Updateable"),
            ))
        return out

    # --- Boot management --------------------------------------------

    async def boot_config(self) -> BootConfig:
        """Current boot configuration."""
        system = await self._get(await self._first_system_path())
        boot = system.get("Boot") or {}
        order = boot.get("BootOrder") or []
        return BootConfig(
            boot_source_override_enabled=boot.get("BootSourceOverrideEnabled"),
            boot_source_override_target=boot.get("BootSourceOverrideTarget"),
            boot_source_override_mode=boot.get("BootSourceOverrideMode"),
            boot_order=list(order),
        )

    async def set_next_boot(self, target: str, mode: str = "UEFI") -> None:
        """Set a one-time boot override. Common targets: 'Pxe',
        'Hdd', 'Cd', 'UsbStick', 'BiosSetup', 'Utilities', 'None'.

        `mode` accepts 'UEFI' (default) or 'Legacy'. Some firmware
        rejects mode changes on the same call as the target; if you
        hit that, call set_next_boot() twice — once to set the mode
        on a 'None' target, once with the real target."""
        sys_path = await self._first_system_path()
        await self._patch(sys_path, {
            "Boot": {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": target,
                "BootSourceOverrideMode": mode,
            }
        })

    async def set_boot_order(self, order: list[str]) -> None:
        """Persistent boot order. Pass a list of boot-option references
        as returned by `boot_config().boot_order`. Vendor quirk: iLO
        rejects empty / unknown entries silently; iDRAC errors. Validate
        against the current `boot_config()` before calling if you care."""
        sys_path = await self._first_system_path()
        await self._patch(sys_path, {"Boot": {"BootOrder": order}})

    # --- Network info -----------------------------------------------

    async def network_info(self) -> NetworkInfo:
        """BMC management-interface network configuration. Returns the
        first EthernetInterface; most BMCs only expose one."""
        manager_path = await self._first_manager_path()
        try:
            interfaces = await self._get(f"{manager_path}/EthernetInterfaces")
        except RedfishError:
            return NetworkInfo(None, None, None, None, None, None, None, None)
        members = interfaces.get("Members") or []
        if not members:
            return NetworkInfo(None, None, None, None, None, None, None, None)
        iface = await self._get(members[0]["@odata.id"])

        ipv4_list = iface.get("IPv4Addresses") or []
        ipv4 = ipv4_list[0] if ipv4_list else {}
        ipv6_list = iface.get("IPv6Addresses") or []
        ipv6 = ipv6_list[0] if ipv6_list else {}

        # NTP + DNS are sometimes on the interface, sometimes on the
        # manager itself. Try both.
        dns_servers = iface.get("StaticNameServers") or iface.get("NameServers") or []
        ntp_servers: list[str] = []
        try:
            manager = await self._get(manager_path)
            ntp = manager.get("NetworkProtocol") or {}
            if isinstance(ntp, dict) and ntp.get("@odata.id"):
                np = await self._get(ntp["@odata.id"])
                ntp_servers = list((np.get("NTP") or {}).get("NTPServers") or [])
        except RedfishError:
            pass

        return NetworkInfo(
            hostname=iface.get("HostName"),
            fqdn=iface.get("FQDN"),
            mac_address=iface.get("MACAddress") or iface.get("PermanentMACAddress"),
            ipv4_address=ipv4.get("Address"),
            ipv4_gateway=ipv4.get("Gateway"),
            ipv4_subnet_mask=ipv4.get("SubnetMask"),
            ipv4_origin=ipv4.get("AddressOrigin"),
            ipv6_address=ipv6.get("Address"),
            dns_servers=list(dns_servers),
            ntp_servers=ntp_servers,
        )

    # --- System Event Log -------------------------------------------

    async def sel_entries(self, limit: int | None = 100) -> list[SelEntry]:
        """System Event Log readout.

        Tries paths in this order:
          1. /Systems/{id}/LogServices/Sel/Entries  (standard SEL)
          2. /Managers/{id}/LogServices/Lclog/Entries  (iDRAC Lifecycle)
          3. /Managers/{id}/LogServices/IML/Entries   (iLO IML)
          4. /Systems/{id}/LogServices/Log/Entries    (some Supermicro)

        Returns up to `limit` most-recent entries (default 100; pass
        None for all)."""
        candidates = []
        try:
            sys_path = await self._first_system_path()
            candidates.append(f"{sys_path}/LogServices/Sel/Entries")
            candidates.append(f"{sys_path}/LogServices/Log/Entries")
        except RedfishError:
            pass
        try:
            mgr_path = await self._first_manager_path()
            candidates.append(f"{mgr_path}/LogServices/Lclog/Entries")
            candidates.append(f"{mgr_path}/LogServices/IML/Entries")
        except RedfishError:
            pass

        for path in candidates:
            try:
                entries = await self._get(path)
            except RedfishError:
                continue
            members = entries.get("Members") or []
            if not members:
                continue
            out: list[SelEntry] = []
            for raw in members:
                if isinstance(raw, dict) and "Id" not in raw:
                    # Reference; need to fetch.
                    try:
                        raw = await self._get(raw["@odata.id"])
                    except (RedfishError, KeyError):
                        continue
                out.append(SelEntry(
                    id=str(raw.get("Id") or raw.get("EntryCode") or ""),
                    created=self._parse_dt(raw.get("Created")),
                    severity=raw.get("Severity"),
                    message=raw.get("Message") or "",
                    message_id=raw.get("MessageId"),
                    sensor_type=raw.get("SensorType"),
                    entry_code=raw.get("EntryCode"),
                ))
                if limit is not None and len(out) >= limit:
                    return out
            return out
        return []

    async def clear_sel(self) -> None:
        """Clear the System Event Log. Best-effort across the same path
        list as sel_entries(); calls LogService.ClearLog on the first
        matching service."""
        candidates = []
        try:
            sys_path = await self._first_system_path()
            candidates.append(f"{sys_path}/LogServices/Sel")
            candidates.append(f"{sys_path}/LogServices/Log")
        except RedfishError:
            pass
        try:
            mgr_path = await self._first_manager_path()
            candidates.append(f"{mgr_path}/LogServices/Lclog")
            candidates.append(f"{mgr_path}/LogServices/IML")
        except RedfishError:
            pass

        for path in candidates:
            try:
                svc = await self._get(path)
            except RedfishError:
                continue
            actions = svc.get("Actions") or {}
            clear = actions.get("#LogService.ClearLog")
            if isinstance(clear, dict) and clear.get("target"):
                await self._post_action(clear["target"], {})
                return
        raise RedfishError("no clearable log service found")

    # --- BMC users (read-only) --------------------------------------

    async def bmc_users(self) -> list[BmcUser]:
        """List BMC accounts. Read-only — we do NOT support user CRUD
        in this library (different shape, vendor-quirk hell)."""
        try:
            account_service = await self._get("/redfish/v1/AccountService")
        except RedfishError:
            return []
        accounts = account_service.get("Accounts") or {}
        accounts_href = accounts.get("@odata.id") if isinstance(accounts, dict) else None
        if not accounts_href:
            return []
        try:
            collection = await self._get(accounts_href)
        except RedfishError:
            return []
        out: list[BmcUser] = []
        for m in collection.get("Members") or []:
            try:
                a = await self._get(m["@odata.id"])
            except RedfishError:
                continue
            out.append(BmcUser(
                id=str(a.get("Id") or ""),
                user_name=a.get("UserName") or "",
                role_id=a.get("RoleId"),
                enabled=a.get("Enabled"),
                locked=a.get("Locked"),
            ))
        return out

    # --- License info -----------------------------------------------

    async def license_info(self) -> LicenseInfo:
        """Best-effort license detection. Vendor-specific:
          - iDRAC: returns Express / Enterprise / Datacenter from
            Oem.Dell.DellLicensableDeviceCollection.
          - iLO: returns iLO Standard / Advanced / Essentials from
            Oem.Hpe.License.
          - Others: usually returns mostly-empty LicenseInfo.

        Treat the result as informational; the absence of a license
        block doesn't mean the system is unlicensed."""
        # Probe the root for Oem hints.
        try:
            root = await self._get("/redfish/v1")
        except RedfishError:
            return LicenseInfo(None, None, None, None, [])
        oem = root.get("Oem") or {}
        oem_keys_lc = {k.lower(): k for k in oem.keys()} if isinstance(oem, dict) else {}

        if "dell" in oem_keys_lc:
            # iDRAC: license info lives at /Managers/{id}/Oem/Dell/DellLicenseManagementService
            try:
                mgr_path = await self._first_manager_path()
                mgr = await self._get(mgr_path)
                lic = ((mgr.get("Links") or {}).get("Oem") or {}).get("Dell", {}).get("DellLicenseCollection") or {}
                lic_href = lic.get("@odata.id") if isinstance(lic, dict) else None
                if lic_href:
                    coll = await self._get(lic_href)
                    members = coll.get("Members") or []
                    if members:
                        first = await self._get(members[0]["@odata.id"])
                        return LicenseInfo(
                            vendor="Dell",
                            license_type=first.get("LicenseType") or first.get("LicenseDescription"),
                            license_key_fingerprint=None,  # never the full key
                            expires_at=self._parse_dt(first.get("ExpirationDate")),
                            features=list(first.get("EntitlementID") or []),
                        )
            except RedfishError:
                pass
            return LicenseInfo("Dell", None, None, None, [])

        if "hpe" in oem_keys_lc or "hp" in oem_keys_lc:
            # iLO: license info lives at Manager.Oem.Hpe.License
            try:
                mgr_path = await self._first_manager_path()
                mgr = await self._get(mgr_path)
                hpe_oem = ((mgr.get("Oem") or {}).get(oem_keys_lc.get("hpe") or "Hpe") or {})
                lic = hpe_oem.get("License") or {}
                return LicenseInfo(
                    vendor="HPE",
                    license_type=lic.get("LicenseType") or lic.get("Description"),
                    license_key_fingerprint=None,
                    expires_at=self._parse_dt(lic.get("ExpirationDate")),
                    features=list(lic.get("FeatureList") or []),
                )
            except RedfishError:
                pass
            return LicenseInfo("HPE", None, None, None, [])

        if "supermicro" in oem_keys_lc:
            return LicenseInfo("Supermicro", None, None, None, [])
        if "lenovo" in oem_keys_lc:
            return LicenseInfo("Lenovo", None, None, None, [])

        return LicenseInfo(None, None, None, None, [])

    # --- Chassis health ---------------------------------------------

    async def chassis_health(self) -> HealthRollup:
        """Aggregate health across the chassis sub-components. Pulls
        the rollup statuses from System / Chassis / Manager."""
        system = None
        chassis = None
        manager = None
        try:
            system = await self._get(await self._first_system_path())
        except RedfishError:
            pass
        try:
            chassis = await self._get(await self._first_chassis_path())
        except RedfishError:
            pass
        try:
            manager = await self._get(await self._first_manager_path())
        except RedfishError:
            pass

        def _sub_health(obj: dict[str, Any] | None, key: str) -> str | None:
            if not obj:
                return None
            section = obj.get(key)
            if isinstance(section, dict):
                s = section.get("Status") or {}
                return s.get("HealthRollup") or s.get("Health")
            return None

        return HealthRollup(
            overall=self._status(system or {}) or self._status(chassis or {}),
            system=self._status(system or {}),
            processor=_sub_health(system, "ProcessorSummary"),
            memory=_sub_health(system, "MemorySummary"),
            storage=_sub_health(system, "Storage"),
            power=_sub_health(chassis, "Power"),
            thermal=_sub_health(chassis, "Thermal"),
            network=_sub_health(system, "EthernetInterfaces"),
            bmc=self._status(manager or {}),
        )

    # --- Chassis control --------------------------------------------

    async def indicator_led(self, state: str) -> None:
        """Set the chassis indicator (locator) LED.

        `state` accepts: 'Lit' / 'Blinking' / 'Off'. Some vendors only
        support 'Lit' + 'Off'; we surface the BMC's error message as-is."""
        chassis_path = await self._first_chassis_path()
        await self._patch(chassis_path, {"IndicatorLED": state})

    async def reset_bmc(self) -> None:
        """Soft-reset the management controller (BMC). Equivalent to
        a `racadm racreset` (iDRAC) or `hponcfg reset` (iLO). Use as
        a recovery action when the BMC web UI is unresponsive but
        the BMC still answers Redfish."""
        mgr_path = await self._first_manager_path()
        await self._post_action(
            f"{mgr_path}/Actions/Manager.Reset", {"ResetType": "ForceRestart"}
        )

    async def nmi_trigger(self) -> None:
        """Send a Non-Maskable Interrupt to the host OS. Triggers a
        kernel panic + dump on most operating systems — use this when
        the OS is hung and you need a crash dump to diagnose."""
        sys_path = await self._first_system_path()
        await self._post_action(
            f"{sys_path}/Actions/ComputerSystem.Reset", {"ResetType": "Nmi"}
        )

    # --- Internal patch helper --------------------------------------

    async def _patch(self, path: str, body: dict[str, Any]) -> None:
        """PATCH a Redfish resource. Used by boot config + indicator
        LED + a few others."""
        await self._ensure_auth()
        c = await self._client()
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        resp = await c.patch(path, json=body, headers=headers)
        if resp.status_code == 401 and self._auth_mode == "session":
            self.session_token = None
            await self._ensure_auth()
            headers = {**self._auth_headers(), "Content-Type": "application/json"}
            resp = await c.patch(path, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RedfishError(
                f"PATCH {path}: HTTP {resp.status_code} {resp.text[:200]}"
            )


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
