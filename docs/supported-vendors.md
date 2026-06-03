# Supported vendors

The honest list of vendor + firmware version pairs we've tested
`kvmfleet-bmc-adapters` against. PRs adding a new pair are welcome —
see [contributing.md](contributing.md) for the test-fixture shape.

## Tested in production

These are running against real fleets through the KVM Fleet hosted
platform:

| Vendor | Firmware | Auth path | Notes |
|---|---|---|---|
| Dell iDRAC 9 | 4.40+ | SessionService | Standard Redfish-1.x compliance. Pre-eject works. |
| HPE iLO 5 | 2.30+ | SessionService | Standard. |
| HPE iLO 4 | 2.78 | Basic fallback | SessionService POST returns 401; falls back to Basic OK. |
| Supermicro X11 | 1.74+ | Basic fallback | SessionService returns 204 No Content. |
| Lenovo XCC2 | 22A+ | SessionService | Slot ordering quirk handled in `_pick_cd_slot`. |
| OpenBMC | mainline 2024-Q4+ | SessionService | Vanilla Redfish; works without quirks. |

## Tested in CI only (mocked, not live)

Same vendors as above — every test in `tests/test_redfish_client.py` runs
against `respx`-mocked HTTP responses that match the JSON shapes the
vendors actually return.

## Known partially-working vendors

If you've tried something not on this list and it worked / didn't work,
please open an issue. We list these honestly so users don't get burned.

| Vendor | Firmware | Status | Notes |
|---|---|---|---|
| Dell iDRAC 8 | 2.85 | Power + heartbeat OK; virtual media has slot-ordering bug | iDRAC 8 deprecates virtual media via Redfish; recommend upgrade to iDRAC 9 |
| Supermicro X10 | 3.x | Untested but should work | Older Redfish; expect Basic-auth fallback |

## Not supported

| Vendor | Why |
|---|---|
| ASUS ASMB | No public Redfish implementation we've tested against |
| Asrock | Same |
| Older HPE Lights-Out 100 | No Redfish support |
| IPMI-only devices | Use `ipmitool` or a dedicated IPMI client; we may add `bmc_adapters.ipmi` if there's demand |

## What we mean by "supported"

A vendor is in the "tested in production" table when **all four core
operations** work against it across at least three different physical
BMCs in real fleets:

1. `heartbeat()` returns expected power/thermal/health
2. `power_action("cycle")` cycles the host
3. `insert_virtual_media(url)` mounts an ISO
4. `eject_virtual_media()` ejects cleanly

A vendor is in "partially-working" when some operations work but others
don't. The library will raise `RedfishError` clearly when a specific
operation fails; you can catch and skip.

## Adding a new vendor

We accept vendor additions with a single PR including:

1. Real response fixtures for the vendor's `/redfish/v1`,
   `/redfish/v1/Systems/1`, `/redfish/v1/Chassis/1/Thermal`,
   `/redfish/v1/Managers/1/VirtualMedia` (sanitised — no real credentials
   or hostnames).
2. A test in `tests/` that mocks those fixtures and verifies all four
   core operations.
3. A row added to this doc.

See `tests/test_redfish_client.py` for the fixture style.
