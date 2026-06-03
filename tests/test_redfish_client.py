"""Tests for the RedfishClient.

Uses respx to mock the httpx layer — no live BMC required. The fixtures
mirror the JSON shape real BMCs return, including the vendor quirks the
client is designed to absorb (missing SessionService, basic-auth fallback,
slot MediaTypes variation).
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from bmc_adapters import HeartbeatSnapshot, RedfishClient, RedfishError

BMC = "https://idrac.example"


# --- auth: SessionService happy path ------------------------------------

@respx.mock
async def test_login_uses_session_service_when_token_returned() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok-123"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": []})
    )

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        snap = await client.heartbeat()

    assert client.session_token == "tok-123"
    assert client.session_dirty is True
    assert snap == HeartbeatSnapshot(
        online=True, power_state=None, cpu_temp_c=None, health=None
    )


# --- auth: SessionService 204-no-token falls back to Basic --------------

@respx.mock
async def test_login_falls_back_to_basic_on_204_no_token() -> None:
    """Some firmware (and most mockups) return 204 No Content with no
    X-Auth-Token. The client must fall back to HTTP Basic without
    pretending session auth worked."""
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(204)  # no token header
    )
    respx.get(f"{BMC}/redfish/v1").mock(return_value=Response(200, json={}))
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": []})
    )

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        await client.heartbeat()

    assert client._auth_mode == "basic"
    assert client.session_token is None


# --- auth: bad creds → hard error ---------------------------------------

@respx.mock
async def test_login_raises_when_session_and_basic_both_rejected() -> None:
    """SessionService returns 401, Basic probe also returns 401: the BMC
    rejected the creds via both paths — credentials are wrong."""
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(401)
    )
    respx.get(f"{BMC}/redfish/v1").mock(return_value=Response(401))

    async with RedfishClient(
        base_url=BMC, username="root", password="wrong"
    ) as client:
        with pytest.raises(RedfishError, match="credentials likely wrong"):
            await client.heartbeat()


# --- password callable --------------------------------------------------

@respx.mock
async def test_login_accepts_sync_password_callable() -> None:
    """Plaintext-or-callable means callers who keep secrets encrypted at
    rest can pass a getter without forking the library."""
    call_count = 0

    def get_pw() -> str:
        nonlocal call_count
        call_count += 1
        return "calvin"

    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": []})
    )

    async with RedfishClient(
        base_url=BMC, username="root", password=get_pw
    ) as client:
        await client.heartbeat()

    assert call_count == 1, "password getter called exactly once during login"


@respx.mock
async def test_login_accepts_async_password_callable() -> None:
    async def get_pw() -> str:
        return "calvin"

    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": []})
    )

    async with RedfishClient(
        base_url=BMC, username="root", password=get_pw
    ) as client:
        await client.heartbeat()

    assert client._auth_mode == "session"


# --- heartbeat: full system + thermal -----------------------------------

@respx.mock
async def test_heartbeat_pulls_power_health_and_cpu_temp() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]})
    )
    respx.get(f"{BMC}/redfish/v1/Systems/1").mock(return_value=Response(200, json={
        "PowerState": "On",
        "Status": {"HealthRollup": "OK"},
        "Links": {"Chassis": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
    }))
    respx.get(f"{BMC}/redfish/v1/Chassis/1/Thermal").mock(return_value=Response(200, json={
        "Temperatures": [
            {"Name": "Inlet Temp", "ReadingCelsius": 22},
            {"Name": "CPU 1 Temp", "ReadingCelsius": 51.5},
            {"Name": "CPU 2 Temp", "ReadingCelsius": 49.0},
        ],
    }))

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        snap = await client.heartbeat()

    # First CPU sensor wins over second; inlet only used as fallback.
    assert snap == HeartbeatSnapshot(
        online=True, power_state="On", cpu_temp_c=51.5, health="OK"
    )


@respx.mock
async def test_heartbeat_falls_back_to_inlet_temp_when_no_cpu_sensor() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]})
    )
    respx.get(f"{BMC}/redfish/v1/Systems/1").mock(return_value=Response(200, json={
        "PowerState": "Off",
        "Status": {"HealthRollup": "OK"},
        "Links": {"Chassis": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
    }))
    respx.get(f"{BMC}/redfish/v1/Chassis/1/Thermal").mock(return_value=Response(200, json={
        "Temperatures": [
            {"Name": "Inlet Temp", "ReadingCelsius": 25.0},
            {"Name": "Ambient", "ReadingCelsius": 24.0},
        ],
    }))

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        snap = await client.heartbeat()

    assert snap.cpu_temp_c == 25.0


# --- power_action -------------------------------------------------------

@respx.mock
async def test_power_action_maps_friendly_verb_to_redfish_resettype() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(200, json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]})
    )
    reset_route = respx.post(f"{BMC}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset").mock(
        return_value=Response(204)
    )

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        await client.power_action("cycle")

    assert reset_route.called
    body = reset_route.calls.last.request.read()
    assert b'"ResetType":"ForceRestart"' in body.replace(b" ", b"")


async def test_power_action_rejects_unknown_verb() -> None:
    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        with pytest.raises(RedfishError, match="unknown action"):
            await client.power_action("nuke")  # not in ACTION_TO_REDFISH


# --- virtual media: insert / eject --------------------------------------

@respx.mock
async def test_insert_virtual_media_picks_cd_slot_and_calls_insertmedia() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Managers").mock(
        return_value=Response(
            200, json={"Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.1"}]}
        )
    )
    respx.get(f"{BMC}/redfish/v1/Managers/iDRAC.1/VirtualMedia").mock(
        return_value=Response(200, json={
            "Members": [
                {"@odata.id": "/redfish/v1/Managers/iDRAC.1/VirtualMedia/RemovableDisk"},
                {"@odata.id": "/redfish/v1/Managers/iDRAC.1/VirtualMedia/CD"},
            ]
        })
    )
    respx.get(f"{BMC}/redfish/v1/Managers/iDRAC.1/VirtualMedia/RemovableDisk").mock(
        return_value=Response(200, json={"MediaTypes": ["USBStick"], "Inserted": False})
    )
    respx.get(f"{BMC}/redfish/v1/Managers/iDRAC.1/VirtualMedia/CD").mock(
        return_value=Response(200, json={"MediaTypes": ["CD", "DVD"], "Inserted": False})
    )
    insert_route = respx.post(
        f"{BMC}/redfish/v1/Managers/iDRAC.1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia"
    ).mock(return_value=Response(204))

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        slot = await client.insert_virtual_media("https://example.com/ubuntu.iso")

    assert slot == "/redfish/v1/Managers/iDRAC.1/VirtualMedia/CD"
    assert insert_route.called


@respx.mock
async def test_insert_virtual_media_pre_ejects_a_busy_slot() -> None:
    """Some firmware (iDRAC 9 < 4.40) refuses InsertMedia if the slot is
    busy. The client pre-ejects, tolerating eject failure."""
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Managers").mock(
        return_value=Response(200, json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]})
    )
    respx.get(f"{BMC}/redfish/v1/Managers/1/VirtualMedia").mock(
        return_value=Response(200, json={
            "Members": [{"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/CD"}]
        })
    )
    respx.get(f"{BMC}/redfish/v1/Managers/1/VirtualMedia/CD").mock(
        return_value=Response(200, json={"MediaTypes": ["CD"], "Inserted": True})
    )
    eject_route = respx.post(
        f"{BMC}/redfish/v1/Managers/1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia"
    ).mock(return_value=Response(204))
    insert_route = respx.post(
        f"{BMC}/redfish/v1/Managers/1/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia"
    ).mock(return_value=Response(204))

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        await client.insert_virtual_media("https://example.com/ubuntu.iso")

    assert eject_route.called, "pre-eject must fire when slot reports Inserted=true"
    assert insert_route.called


@respx.mock
async def test_eject_returns_count_of_actually_ejected_slots() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Managers").mock(
        return_value=Response(200, json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]})
    )
    respx.get(f"{BMC}/redfish/v1/Managers/1/VirtualMedia").mock(
        return_value=Response(200, json={
            "Members": [
                {"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/CD"},
                {"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/RemovableDisk"},
            ]
        })
    )
    respx.get(f"{BMC}/redfish/v1/Managers/1/VirtualMedia/CD").mock(
        return_value=Response(200, json={"MediaTypes": ["CD"], "Inserted": True})
    )
    respx.get(f"{BMC}/redfish/v1/Managers/1/VirtualMedia/RemovableDisk").mock(
        return_value=Response(200, json={"MediaTypes": ["USBStick"], "Inserted": False})
    )
    respx.post(
        f"{BMC}/redfish/v1/Managers/1/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia"
    ).mock(return_value=Response(204))

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        ejected = await client.eject_virtual_media()

    # Only one slot was mounted; only one ejected.
    assert ejected == 1


# --- BMC-error surfaces -------------------------------------------------

@respx.mock
async def test_get_surfaces_500_as_redfish_error() -> None:
    respx.post(f"{BMC}/redfish/v1/SessionService/Sessions").mock(
        return_value=Response(201, headers={"X-Auth-Token": "tok"})
    )
    respx.get(f"{BMC}/redfish/v1/Systems").mock(
        return_value=Response(500, text="iDRAC internal error")
    )

    async with RedfishClient(
        base_url=BMC, username="root", password="calvin"
    ) as client:
        with pytest.raises(RedfishError, match="HTTP 500"):
            await client.heartbeat()


# --- ACTION_TO_REDFISH contract ----------------------------------------

def test_action_map_covers_all_four_friendly_verbs() -> None:
    from bmc_adapters import ACTION_TO_REDFISH

    assert set(ACTION_TO_REDFISH.keys()) == {"on", "off", "off_hard", "cycle"}
    assert ACTION_TO_REDFISH["on"] == "On"
    assert ACTION_TO_REDFISH["off"] == "GracefulShutdown"
    assert ACTION_TO_REDFISH["off_hard"] == "ForceOff"
    assert ACTION_TO_REDFISH["cycle"] == "ForceRestart"
