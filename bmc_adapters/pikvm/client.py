"""PiKVMClient — async HTTP client for PiKVM's kvmd ATX endpoint.

PiKVM's kvmd ATX module drives GPIO relays wired into the target's
front-panel power/reset header. The library does not detect whether
the harness is physically wired (a 200 OK from kvmd means "I toggled
GPIO 23" — whether that affects a motherboard is a hardware question).

Endpoints used:

- `GET  /api/atx`                       — read state
- `POST /api/atx/power?action=<verb>`   — fire an action

`verb` values accepted by kvmd: `on`, `off`, `off_hard`, `reset_hard`.
We map friendly verbs into them.
"""
from __future__ import annotations

import httpx

from ..base import BMCAdapter, Feature
from ..findings import BMCFinding

# Friendly power verbs → kvmd ATX action strings.
_ACTION_MAP: dict[str, str] = {
    "on": "on",
    "off": "off",
    "off_hard": "off_hard",
    "cycle": "reset_hard",
    "reboot": "reset_hard",   # PiKVM has no ACPI path; collapse to reset_hard
}


class PiKVMClient(BMCAdapter):
    """PiKVM ATX power control via kvmd HTTP API."""

    vendor = "pikvm"
    features = frozenset({
        Feature.POWER_STATE,
        Feature.POWER_SET,
        Feature.WAKE,
    })

    def __init__(
        self,
        base_url: str,
        username: str = "admin",
        password: str = "admin",
        *,
        verify: bool = False,
        timeout: float = 5.0,
        use_basic_auth: bool = False,
    ) -> None:
        super().__init__()
        base = base_url.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        self._http = httpx.AsyncClient(
            base_url=base,
            verify=verify,
            timeout=timeout,
        )
        self._user = username
        self._pw = password
        if use_basic_auth:
            self._http.auth = (username, password)
        else:
            self._http.headers["X-KVMD-User"] = username
            self._http.headers["X-KVMD-Passwd"] = password

        # Default-credential finding for `admin/admin`.
        if username == "admin" and password == "admin":
            self._emit(
                BMCFinding(
                    code="BMC_DEFAULT_CREDENTIALS_LIKELY",
                    severity="high",
                    detail=(
                        "PiKVM default kvmd credentials ('admin/admin') in use. "
                        "Rotate before exposing the management interface."
                    ),
                    vendor="pikvm",
                )
            )

    async def power_state(self) -> str:
        r = await self._http.get("/api/atx")
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            return "unknown"
        leds = body.get("result", {}).get("leds", {})
        return "on" if leds.get("power") else "off"

    async def power_action(self, action: str) -> None:
        verb = _ACTION_MAP.get(action)
        if verb is None:
            raise ValueError(
                f"unsupported PiKVM action {action!r}; "
                f"accepted: {sorted(_ACTION_MAP)}"
            )
        r = await self._http.post(f"/api/atx/power?action={verb}")
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(
                f"kvmd ATX action {verb} failed: "
                f"{body.get('result', {}).get('error_msg', body)}"
            )

    async def atx_state(self) -> dict[str, object]:
        """Full ATX state — busy flag + LED rollup, for diagnostics."""
        r = await self._http.get("/api/atx")
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(
                f"kvmd /api/atx error: "
                f"{body.get('result', {}).get('error_msg', body)}"
            )
        return body.get("result", {})

    async def aclose(self) -> None:
        await self._http.aclose()
