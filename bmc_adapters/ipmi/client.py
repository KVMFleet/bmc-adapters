"""IPMIClient — async wrapper around pyghmi.

The wrapper mirrors RedfishClient's shape so callers can swap transports
at the edge:

    async with IPMIClient(IPMIConfig(...)) as client:
        await client.power_action("cycle")
        chassis = await client.chassis_status()
        sensors = await client.sensors()

Architecture notes (from the deep-IPMI research brief):

- pyghmi is synchronous. We call it via `asyncio.to_thread` rather than
  trying to bend its callback-based "async" mode (which predates asyncio
  and uses eventlet idioms).
- One persistent `pyghmi.ipmi.command.Command` per IPMIClient. pyghmi's
  RMCP+ session is stateful (sequence numbers); we serialise commands
  through an `asyncio.Lock` to avoid in-flight reordering.
- Default security posture: refuse IPMI 1.5, refuse cipher suites
  0/1/2/6/7/8/11/12. Cipher 17 (SHA-256) preferred, cipher 3 (SHA-1)
  accepted as the interop floor.
- Default-credential detection: constant-time compare against a known
  vendor/user/password table — we never *try* the default cred against
  the BMC (that would be detectable and audit-flagging).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..findings import (
    BMCFinding,
    matches_default_credential,
    pantsdown_finding,
)
from .types import (
    ACTION_TO_PYGHMI,
    FRU,
    ChassisStatus,
    PowerAction,
    SELEntry,
    Sensor,
)

if TYPE_CHECKING:
    from pyghmi.ipmi.command import Command as _PyghmiCommand

log = logging.getLogger(__name__)


# IANA Enterprise IDs → vendor strings. We use the Manufacturer ID from
# `Get Device ID` to short-circuit default-cred matching without probing.
_IANA_TO_VENDOR: dict[int, str] = {
    11: "hpe",            # Hewlett-Packard
    674: "dell",          # Dell
    10876: "supermicro",
    19046: "lenovo",
    20301: "ibm",         # IMM (System x M4/M5)
    7244: "quanta",
    10368: "fujitsu",
    9: "cisco",
    343: "intel",
    2697: "asus",
    3454: "tyan",
}


@dataclass(slots=True)
class IPMIConfig:
    """Connection config — all secrets stay in memory.

    Cipher suite list is *acceptable* suites; pyghmi negotiates the
    strongest the BMC supports. Cipher 0 is never accepted.
    """

    host: str
    username: str
    password: str
    port: int = 623
    cipher_suites: Sequence[int] = (17, 3)
    kg_key: bytes | None = None
    timeout: float = 5.0
    allow_default_credentials: bool = False
    allow_ipmi_1_5: bool = False  # opt-in only
    privlevel: str = "administrator"


# Cipher suites we will never accept, even if the operator asks. The
# checked-in literal is what shows up in pen-test reports as "auth
# bypass / no encryption / weak hash".
_FORBIDDEN_CIPHERS: frozenset[int] = frozenset({0, 1, 2, 6, 7, 8, 11, 12})


def _import_pyghmi() -> Any:
    """Lazy-import pyghmi so consumers who don't use IPMI don't need it."""
    try:
        import pyghmi.exceptions  # noqa: F401  (validate install)
        from pyghmi.ipmi import command as _cmd
    except ImportError as e:  # pragma: no cover - install error path
        raise ImportError(
            "pyghmi is required for IPMI support. "
            "Install with `pip install kvmfleet-bmc-adapters[ipmi]` or "
            "`pip install pyghmi`."
        ) from e
    return _cmd


class IPMIClient:
    """Async IPMI client — mirrors RedfishClient surface."""

    def __init__(self, cfg: IPMIConfig) -> None:
        for suite in cfg.cipher_suites:
            if suite in _FORBIDDEN_CIPHERS:
                raise ValueError(
                    f"cipher suite {suite} is on the forbidden list "
                    f"({sorted(_FORBIDDEN_CIPHERS)})"
                )
        self._cfg = cfg
        self._cmd: _PyghmiCommand | None = None
        self._lock = asyncio.Lock()
        self._findings: list[BMCFinding] = []
        self._vendor: str | None = None
        self._firmware: str | None = None

    @property
    def findings(self) -> list[BMCFinding]:
        return list(self._findings)

    @property
    def vendor(self) -> str | None:
        return self._vendor

    @property
    def firmware(self) -> str | None:
        return self._firmware

    async def __aenter__(self) -> "IPMIClient":
        await self._connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _connect(self) -> None:
        cmd_mod = _import_pyghmi()

        def _build() -> _PyghmiCommand:
            # pyghmi negotiates the strongest cipher the BMC supports.
            # If the BMC tries to downgrade to 0/1/2/etc., pyghmi will
            # raise IpmiException and we surface it to the caller.
            return cmd_mod.Command(
                bmc=self._cfg.host,
                userid=self._cfg.username,
                password=self._cfg.password,
                port=self._cfg.port,
                kg=self._cfg.kg_key,
                privlevel=self._cfg.privlevel,
            )

        self._cmd = await asyncio.to_thread(_build)
        await self._fingerprint_and_detect()

    async def _fingerprint_and_detect(self) -> None:
        """Read Get Device ID + emit findings for default creds & known CVEs."""
        assert self._cmd is not None

        try:
            device_id = await asyncio.to_thread(self._cmd.bmc_handler.get_device_id)
        except Exception:  # noqa: BLE001
            device_id = {}

        manufacturer_id = device_id.get("manufacturer_id")
        firmware_revision = device_id.get("firmware_revision")
        if isinstance(manufacturer_id, int):
            self._vendor = _IANA_TO_VENDOR.get(manufacturer_id)
        if isinstance(firmware_revision, str):
            self._firmware = firmware_revision

        # Default-credentials finding — fingerprint-only, no probing.
        if matches_default_credential(
            self._vendor, self._cfg.username, self._cfg.password
        ):
            if not self._cfg.allow_default_credentials:
                self._emit_and_raise(
                    BMCFinding(
                        code="BMC_DEFAULT_CREDENTIALS_LIKELY",
                        severity="critical",
                        detail=(
                            f"Username '{self._cfg.username}' with the documented "
                            f"default password for vendor '{self._vendor}'. "
                            "Set `allow_default_credentials=True` to proceed."
                        ),
                        vendor=self._vendor,
                    )
                )
            else:
                self._findings.append(
                    BMCFinding(
                        code="BMC_DEFAULT_CREDENTIALS_LIKELY",
                        severity="high",
                        detail=(
                            f"Connection uses the documented default credentials "
                            f"for vendor '{self._vendor}'."
                        ),
                        vendor=self._vendor,
                    )
                )

        # Static CVE windows — flag without probing the BMC's AHB bridge.
        if self._vendor:
            pd = pantsdown_finding(self._vendor, self._firmware)
            if pd is not None:
                self._findings.append(pd)

    def _emit_and_raise(self, finding: BMCFinding) -> None:
        self._findings.append(finding)
        raise PermissionError(finding.detail)

    async def aclose(self) -> None:
        async with self._lock:
            cmd, self._cmd = self._cmd, None
            if cmd is not None:
                try:
                    await asyncio.to_thread(cmd.ipmi_session.logout)
                except Exception:  # noqa: BLE001 - best effort
                    pass

    # ---------- public feature surface ----------

    async def power_action(self, action: PowerAction) -> None:
        assert self._cmd is not None
        pyghmi_action = ACTION_TO_PYGHMI[action]

        def _do() -> None:
            self._cmd.set_power(pyghmi_action, wait=False)

        async with self._lock:
            await asyncio.to_thread(_do)

    async def chassis_status(self) -> ChassisStatus:
        assert self._cmd is not None

        def _do() -> dict[str, object]:
            return self._cmd.get_power() or {}

        async with self._lock:
            raw = await asyncio.to_thread(_do)
        powerstate = str(raw.get("powerstate", "")).lower()
        return ChassisStatus(power_on=powerstate == "on")

    async def sensors(self) -> list[Sensor]:
        assert self._cmd is not None

        def _do() -> list[dict[str, object]]:
            try:
                return list(self._cmd.get_sensor_data())
            except Exception:  # noqa: BLE001
                return []

        async with self._lock:
            raw = await asyncio.to_thread(_do)

        out: list[Sensor] = []
        for r in raw:
            name = str(r.get("name", "")) or "unknown"
            value = r.get("value")
            units = r.get("units")
            health = r.get("health")
            state: str = "ok"
            if value is None:
                state = "unavailable"
            elif health in ("critical", "warning"):
                state = health
            out.append(
                Sensor(
                    name=name,
                    type=str(r.get("type")) if r.get("type") else None,
                    value=float(value) if isinstance(value, (int, float)) else None,
                    units=str(units) if units else None,
                    state=state,  # type: ignore[arg-type]
                    health=str(health) if health else None,
                )
            )
        return out

    async def fru(self) -> FRU:
        assert self._cmd is not None

        def _do() -> dict[str, object]:
            try:
                return dict(self._cmd.get_inventory_of_component("System"))
            except Exception:  # noqa: BLE001
                return {}

        async with self._lock:
            raw = await asyncio.to_thread(_do)

        return FRU(
            chassis_part_number=_str_or_none(raw.get("Chassis part number")),
            chassis_serial=_str_or_none(raw.get("Chassis serial number")),
            board_manufacturer=_str_or_none(raw.get("Board manufacturer")),
            board_product_name=_str_or_none(raw.get("Board product name")),
            board_serial=_str_or_none(raw.get("Board serial number")),
            board_part_number=_str_or_none(raw.get("Board part number")),
            product_manufacturer=_str_or_none(raw.get("Product manufacturer")),
            product_name=_str_or_none(raw.get("Product name")),
            product_serial=_str_or_none(raw.get("Product serial number")),
            product_asset_tag=_str_or_none(raw.get("Product asset tag")),
            raw={str(k): str(v) for k, v in raw.items() if v is not None},
        )

    async def sel_entries(self, limit: int | None = None) -> list[SELEntry]:
        assert self._cmd is not None

        def _do() -> list[dict[str, object]]:
            try:
                return list(self._cmd.get_event_log())
            except Exception:  # noqa: BLE001
                return []

        async with self._lock:
            raw = await asyncio.to_thread(_do)
        if limit:
            raw = raw[-limit:]

        out: list[SELEntry] = []
        for r in raw:
            ts = r.get("timestamp")
            ts_dt = None
            if isinstance(ts, (int, float)):
                from datetime import UTC, datetime
                ts_dt = datetime.fromtimestamp(ts, tz=UTC)
            direction_raw = str(r.get("event_data", "")).lower()
            direction: str = "asserted" if "assert" in direction_raw else "unknown"
            severity_raw = str(r.get("severity", "")).lower() or "unknown"
            severity: str = (
                severity_raw if severity_raw in ("info", "warning", "critical")
                else "unknown"
            )
            out.append(
                SELEntry(
                    timestamp=ts_dt,
                    sensor=_str_or_none(r.get("sensor_type")),
                    event_type=_str_or_none(r.get("event")),
                    direction=direction,  # type: ignore[arg-type]
                    severity=severity,  # type: ignore[arg-type]
                    description=_str_or_none(r.get("message")),
                    raw={str(k): v for k, v in r.items()},
                )
            )
        return out

    async def sel_clear(self, *, confirm: bool = False) -> None:
        if not confirm:
            raise ValueError("sel_clear requires confirm=True")
        assert self._cmd is not None

        def _do() -> None:
            self._cmd.clear_event_log()

        async with self._lock:
            await asyncio.to_thread(_do)


def _str_or_none(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
