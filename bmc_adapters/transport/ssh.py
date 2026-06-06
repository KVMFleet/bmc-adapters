"""AsyncSSHCLIClient — persistent SSH connection with command multiplexing.

Used by the RACADM adapter (and reserved for any future SSH-CLI
wrappers — Cisco UCS imcadmin, Lenovo OneCLI in CLI mode, etc.).

Designed for BMCs, which means:

- iDRAC7 / older CMC may only speak `diffie-hellman-group14-sha1`
  KEX and `ssh-rsa` host keys. Defaults include those.
- BMCs rotate host keys aggressively (firmware updates regen them);
  we deliberately bypass `known_hosts`. Operators who want host-key
  pinning can pass their own `known_hosts` file via `creds.options`.
- Per-host concurrency cap: most BMCs cap concurrent sessions at 4
  per user. We default to 2 to leave headroom.
- Session reuse: one persistent connection per IPMIClient lifetime,
  multiplexing all `run()` calls.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncssh as _asyncssh

log = logging.getLogger(__name__)


def _import_asyncssh() -> Any:
    try:
        import asyncssh
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "asyncssh is required for SSH-based adapters. "
            "Install with `pip install kvmfleet-bmc-adapters[ssh]` or "
            "`pip install asyncssh`."
        ) from e
    return asyncssh


@dataclass(slots=True)
class SSHCreds:
    """SSH auth — password or key (or both)."""

    username: str
    password: str | None = None
    client_keys: list[str] | None = None
    # Free-form passthrough to asyncssh.connect kwargs (advanced).
    options: dict[str, Any] = field(default_factory=dict)


# Default KEX + host-key algorithm lists — include legacy entries so
# iDRAC7 / older CMC servers connect without operator intervention.
_KEX_ALGS_LEGACY: tuple[str, ...] = (
    "curve25519-sha256",
    "curve25519-sha256@libssh.org",
    "ecdh-sha2-nistp256",
    "ecdh-sha2-nistp384",
    "ecdh-sha2-nistp521",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
)

_HOST_KEY_ALGS_LEGACY: tuple[str, ...] = (
    "rsa-sha2-512",
    "rsa-sha2-256",
    "ssh-rsa",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
)


class AsyncSSHCLIClient:
    """One reusable SSH connection per host with bounded concurrency."""

    def __init__(
        self,
        host: str,
        creds: SSHCreds,
        *,
        port: int = 22,
        connect_timeout: float = 10.0,
        command_timeout: float = 30.0,
        max_concurrent: int = 2,
        kex_algs: tuple[str, ...] = _KEX_ALGS_LEGACY,
        server_host_key_algs: tuple[str, ...] = _HOST_KEY_ALGS_LEGACY,
    ) -> None:
        self.host = host
        self.port = port
        self.creds = creds
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._kex = kex_algs
        self._hka = server_host_key_algs
        self._asyncssh = _import_asyncssh()
        self._conn: _asyncssh.SSHClientConnection | None = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        async with self._lock:
            if self._conn is None or self._conn.is_closed():
                self._conn = await asyncio.wait_for(
                    self._asyncssh.connect(
                        self.host,
                        port=self.port,
                        username=self.creds.username,
                        password=self.creds.password,
                        client_keys=self.creds.client_keys,
                        known_hosts=None,  # BMCs rotate host keys
                        kex_algs=self._kex,
                        server_host_key_algs=self._hka,
                        **self.creds.options,
                    ),
                    timeout=self.connect_timeout,
                )
            return self._conn

    async def run(self, cmd: str, *, timeout: float | None = None) -> str:
        """Run `cmd`, return stdout (empty string on no output).

        Raises asyncio.TimeoutError on timeout; closes the connection
        so the next call re-establishes (BMCs occasionally wedge).
        """
        async with self._sem:
            conn = await self._ensure()
            try:
                result = await asyncio.wait_for(
                    conn.run(cmd, check=False),
                    timeout=timeout or self.command_timeout,
                )
            except asyncio.TimeoutError:
                await self.aclose()
                raise
            stdout = result.stdout or ""
            return stdout if isinstance(stdout, str) else stdout.decode(
                "utf-8", errors="replace"
            )

    async def aclose(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                try:
                    await self._conn.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None

    async def __aenter__(self) -> "AsyncSSHCLIClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
