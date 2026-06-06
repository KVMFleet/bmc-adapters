"""PiKVM adapter tests — httpx mocked via respx."""
from __future__ import annotations

import httpx
import pytest
import respx

from bmc_adapters.pikvm import PiKVMClient


@pytest.mark.asyncio
async def test_power_state_on() -> None:
    with respx.mock(base_url="https://pikvm.local") as mock:
        mock.get("/api/atx").respond(
            json={
                "ok": True,
                "result": {"busy": False, "enabled": True,
                           "leds": {"power": True, "hdd": False}},
            }
        )
        async with PiKVMClient("https://pikvm.local") as kvm:
            assert await kvm.power_state() == "on"


@pytest.mark.asyncio
async def test_power_state_off() -> None:
    with respx.mock(base_url="https://pikvm.local") as mock:
        mock.get("/api/atx").respond(
            json={
                "ok": True,
                "result": {"busy": False, "enabled": True,
                           "leds": {"power": False, "hdd": False}},
            }
        )
        async with PiKVMClient("https://pikvm.local") as kvm:
            assert await kvm.power_state() == "off"


@pytest.mark.asyncio
async def test_power_cycle_maps_to_reset_hard() -> None:
    with respx.mock(base_url="https://pikvm.local") as mock:
        route = mock.post(
            "/api/atx/power",
            params={"action": "reset_hard"},
        ).respond(json={"ok": True, "result": {}})
        async with PiKVMClient("https://pikvm.local") as kvm:
            await kvm.power_action("cycle")
        assert route.called


@pytest.mark.asyncio
async def test_power_action_off_hard() -> None:
    with respx.mock(base_url="https://pikvm.local") as mock:
        route = mock.post(
            "/api/atx/power",
            params={"action": "off_hard"},
        ).respond(json={"ok": True, "result": {}})
        async with PiKVMClient("https://pikvm.local") as kvm:
            await kvm.power_action("off_hard")
        assert route.called


@pytest.mark.asyncio
async def test_default_creds_emit_finding() -> None:
    async with PiKVMClient(
        "https://pikvm.local",
        username="admin",
        password="admin",
    ) as kvm:
        codes = [f.code for f in kvm.findings]
        assert "BMC_DEFAULT_CREDENTIALS_LIKELY" in codes


@pytest.mark.asyncio
async def test_unknown_action_raises() -> None:
    async with PiKVMClient("https://pikvm.local") as kvm:
        with pytest.raises(ValueError, match="unsupported PiKVM action"):
            await kvm.power_action("nonsense")
