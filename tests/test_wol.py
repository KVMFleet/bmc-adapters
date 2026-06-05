"""Wake-on-LAN tests — no library deps, pure stdlib + asyncio."""
from __future__ import annotations

import socket

import pytest

from bmc_adapters import wake_on_lan, wake_on_lan_sync
from bmc_adapters.wol import _build_magic_packet


def test_magic_packet_structure() -> None:
    pkt = _build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("aabbccddeeff") * 16


def test_magic_packet_accepts_hyphen_format() -> None:
    pkt = _build_magic_packet("AA-BB-CC-DD-EE-FF")
    assert pkt[6:12] == bytes.fromhex("aabbccddeeff")


def test_magic_packet_accepts_no_separator() -> None:
    pkt = _build_magic_packet("aabbccddeeff")
    assert pkt[6:12] == bytes.fromhex("aabbccddeeff")


def test_magic_packet_rejects_bad_mac() -> None:
    with pytest.raises(ValueError):
        _build_magic_packet("not-a-mac")


def test_wake_on_lan_sync_sends_packet() -> None:
    # Bind a UDP socket on a free port, send a packet to it,
    # verify the bytes match.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.settimeout(1.0)

    try:
        wake_on_lan_sync("aa:bb:cc:dd:ee:ff", broadcast="127.0.0.1", port=port)
        data, _ = sock.recvfrom(200)
        assert data[:6] == b"\xff" * 6
        assert data[6:12] == bytes.fromhex("aabbccddeeff")
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_wake_on_lan_async() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.settimeout(1.0)
    try:
        await wake_on_lan("aa:bb:cc:dd:ee:ff", broadcast="127.0.0.1", port=port)
        data, _ = sock.recvfrom(200)
        assert len(data) == 102
    finally:
        sock.close()
