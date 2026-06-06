"""IPMI Serial-over-LAN (SoL) bridge.

Wraps pyghmi's `pyghmi.ipmi.console.Console` (which uses callbacks)
behind an async iterator + send method, so callers can read SoL
output like an async generator and write back via a coroutine.

Usage:

    async with IPMIClient(IPMIConfig(...)) as bmc:
        async with bmc.sol() as (recv, send):
            await send(b"\r\n")
            async for chunk in recv:
                stdout.buffer.write(chunk)

Important quirks (from the deep-IPMI research brief):

- Always send "Deactivate Payload" before closing. SMC X10 SoL
  will not deactivate cleanly on session timeout — pyghmi handles
  this via Console.close(), so we always call it in the finally
  block.
- HPE iLO 3 RAKP timing is slow; pyghmi defaults are usually OK
  but if you see "session not established" raise the
  `IPMIConfig.timeout`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import IPMIClient

log = logging.getLogger(__name__)


class _SoLSender:
    """Awaitable wrapper that sends a chunk through pyghmi's Console."""

    def __init__(self, console_obj: object, lock: asyncio.Lock) -> None:
        self._console = console_obj
        self._lock = lock

    async def __call__(self, data: bytes) -> None:
        if not data:
            return
        async with self._lock:
            await asyncio.to_thread(self._console.send_data, data)  # type: ignore[attr-defined]


@asynccontextmanager
async def sol_session(
    client: "IPMIClient",
) -> AsyncIterator[tuple[AsyncIterator[bytes], _SoLSender]]:
    """Open an SoL session backed by pyghmi's Console.

    Returns (receiver, sender):
      - receiver yields raw bytes from the host UART
      - sender(data: bytes) writes bytes to the host UART
    """
    try:
        from pyghmi.ipmi import console as _console
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "pyghmi is required for IPMI SoL support. "
            "Install with `pip install kvmfleet-bmc-adapters[ipmi]`."
        ) from e

    cfg = client._cfg  # noqa: SLF001 — module-private collaborator
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    def _on_data(data: object, _self: object) -> None:
        # Called from pyghmi's session thread.
        if isinstance(data, bytes):
            payload = data
        elif isinstance(data, str):
            payload = data.encode("utf-8", errors="replace")
        else:
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except RuntimeError:
            # loop gone — best-effort, drop.
            pass

    def _build_console() -> object:
        return _console.Console(
            bmc=cfg.host,
            userid=cfg.username,
            password=cfg.password,
            iohandler=_on_data,
            force=True,
            port=cfg.port,
            kg=cfg.kg_key,
        )

    console_obj = await asyncio.to_thread(_build_console)
    send_lock = asyncio.Lock()
    sender = _SoLSender(console_obj, send_lock)

    async def _receiver() -> AsyncIterator[bytes]:
        while not closed.is_set():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if chunk:
                yield chunk

    try:
        yield _receiver(), sender
    finally:
        closed.set()
        try:
            await asyncio.to_thread(console_obj.close)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.debug("pyghmi Console.close raised; ignoring", exc_info=True)
