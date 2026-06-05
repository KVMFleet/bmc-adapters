"""Wake-on-LAN — magic-packet sender.

WoL is the simplest power path in the universe: 6 bytes of 0xFF followed
by the target MAC repeated 16 times, sent as a UDP datagram to a
broadcast address on the local L2 segment. No library needed — pure
stdlib.

Limitations (document at call sites):
- Routable only on the local L2 segment unless the upstream router
  is configured for subnet-directed broadcast (`ip directed-broadcast`).
- IPv6 has no standardised WoL.
- WoL must be enabled in the target's BIOS/UEFI AND in the OS
  network stack — most consumer boards ship it disabled.
- Intel AMT / ME may intercept WoL before the host OS.
"""
from __future__ import annotations

import asyncio
import re
import socket

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]?){5}[0-9A-Fa-f]{2}$")


def _build_magic_packet(mac: str) -> bytes:
    if not _MAC_RE.match(mac):
        raise ValueError(f"invalid MAC: {mac!r}")
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return b"\xff" * 6 + raw * 16


def wake_on_lan_sync(
    mac: str,
    *,
    broadcast: str = "255.255.255.255",
    port: int = 9,
) -> None:
    """Send a WoL magic packet (synchronous).

    `port` is conventionally 9 (discard) or 7 (echo); the NIC
    firmware pattern-matches at L2 before the IP stack so the port
    is cosmetic.
    """
    packet = _build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (broadcast, port))


async def wake_on_lan(
    mac: str,
    *,
    broadcast: str = "255.255.255.255",
    port: int = 9,
) -> None:
    """Async-friendly wrapper around `wake_on_lan_sync`.

    The underlying `sendto` is one syscall and returns immediately;
    we keep the async signature for shape parity with the rest of
    the library.
    """
    await asyncio.to_thread(wake_on_lan_sync, mac, broadcast=broadcast, port=port)
