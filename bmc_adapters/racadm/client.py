"""RACADMClient — async wrapper around Dell iDRAC's racadm SSH shell.

10-command core. Reuses the asyncssh transport from
`bmc_adapters.transport.ssh`. Output parsing is per-command in
`parsers.py`.

Connection model:
  - One persistent SSH connection per RACADMClient (per iDRAC).
  - Concurrent commands bounded at 2 (iDRAC session cap is 4).
  - Commands run via `racadm <cmd>` interactive shell prompts on
    older iDRACs; modern firmware accepts `racadm <cmd>` as a
    one-shot command on the same SSH session. We use the one-shot
    form.

Error handling: `racadm` exit codes are unreliable; we parse
stdout for the `ERROR: <RACxxxx>` prefix and raise on match.
"""
from __future__ import annotations

import asyncio
import logging

from ..transport import AsyncSSHCLIClient, SSHCreds
from .parsers import (
    detect_error,
    parse_key_value_block,
    parse_power_status,
    parse_version,
)

log = logging.getLogger(__name__)


class RACADMError(RuntimeError):
    """Raised when RACADM returns a parseable error line."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code} {message}" if code else message)
        self.code = code
        self.message = message


_POWER_VERB_MAP: dict[str, str] = {
    "on": "powerup",
    "off": "graceshutdown",       # ACPI-friendly soft off
    "off_hard": "powerdown",      # hard power down
    "cycle": "powercycle",
    "reboot": "hardreset",        # warm reset
    "soft": "graceshutdown",
}


class RACADMClient:
    """Async wrapper around Dell iDRAC's racadm SSH shell (10-command core)."""

    vendor = "dell"

    def __init__(
        self,
        host: str,
        username: str,
        password: str | None = None,
        *,
        client_keys: list[str] | None = None,
        port: int = 22,
        command_timeout: float = 30.0,
    ) -> None:
        self.host = host
        self._ssh = AsyncSSHCLIClient(
            host,
            SSHCreds(
                username=username,
                password=password,
                client_keys=client_keys,
            ),
            port=port,
            command_timeout=command_timeout,
        )

    async def __aenter__(self) -> "RACADMClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._ssh.aclose()

    async def _racadm(
        self,
        cmd: str,
        *,
        timeout: float | None = None,
    ) -> str:
        out = await self._ssh.run(f"racadm {cmd}", timeout=timeout)
        err = detect_error(out)
        if err is not None:
            raise RACADMError(*err)
        return out

    # ---- power (6 commands collapsed into power_action) ----

    async def power_action(self, action: str) -> None:
        verb = _POWER_VERB_MAP.get(action)
        if verb is None:
            raise ValueError(
                f"unsupported RACADM action {action!r}; "
                f"accepted: {sorted(_POWER_VERB_MAP)}"
            )
        await self._racadm(f"serveraction {verb}")

    async def power_status(self) -> str:
        out = await self._racadm("serveraction powerstatus")
        return parse_power_status(out)

    # ---- inventory ----

    async def get_system_info(self) -> dict[str, str]:
        out = await self._racadm("getsysinfo", timeout=60.0)
        return parse_key_value_block(out)

    async def get_version(self) -> dict[str, str]:
        out = await self._racadm("getversion")
        return parse_version(out)

    async def get_service_tag(self) -> str | None:
        info = await self.get_system_info()
        for key in ("service tag", "system service tag"):
            v = info.get(key)
            if v:
                return v
        return None

    # ---- logs ----

    async def get_sel(self) -> str:
        """Return the raw SEL output. Parsing is left to the caller —
        iDRAC SEL output varies by firmware. v0.7.0 will add a
        structured parser."""
        return await self._racadm("getsel", timeout=60.0)

    # ---- BMC management ----

    async def racreset(self, *, hard: bool = False) -> None:
        """Soft- or hard-reset the iDRAC itself.

        Drops the SSH session. Caller MUST treat the BMC as
        unavailable for 60-120 seconds afterwards before retrying.
        """
        verb = "hard" if hard else "soft"
        try:
            await self._racadm(f"racreset {verb}", timeout=15.0)
        except (RACADMError, asyncio.TimeoutError):
            # racreset kills the session; receiving an error or
            # timeout is expected and not a real failure.
            pass
        await self._ssh.aclose()

    # ---- property DB get/set ----

    async def get(self, fqdd: str) -> dict[str, str]:
        """`racadm get <FQDD>` against the iDRAC property database."""
        out = await self._racadm(f"get {fqdd}")
        return parse_key_value_block(out)

    async def set(self, fqdd: str, value: str) -> None:
        """`racadm set <FQDD> <value>`."""
        await self._racadm(f"set {fqdd} {value}")
