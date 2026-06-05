# kvmfleet-bmc-adapters

Async Python client for multi-vendor BMC Redfish access.

Used in production by the hosted access-governance platform at
[kvmfleet.io](https://kvmfleet.io). Released under Apache 2.0 so
anyone building tooling against mixed-vendor BMC fleets can reuse
the pieces.

## What this is

An async Python library that covers the Redfish operations a BMC
operator reaches for, across **Dell iDRAC, HPE iLO, Supermicro
IPMI/BMC, Lenovo XCC, and OpenBMC** via the DMTF Redfish standard.

**Heartbeat + identity**
- `heartbeat()` — power state + first temp + health rollup
- `detect_vendor()` — vendor from Oem keys / Manager `@odata.id`
- `system_info()` — manufacturer, model, serial, asset tag, host
  name, UUID, BIOS + BMC firmware versions

**Sensors**
- `temperatures()` — full thermal-sensor list with thresholds
- `fans()` — RPM / PWM% per fan
- `power_supplies()` — PSU model, capacity, input/output, status
- `power_metrics()` — chassis-aggregated consumed / avg / min / max

**Hardware inventory**
- `processor_inventory()` — CPU sockets
- `memory_inventory()` — DIMMs
- `drive_inventory(max_drives=None)` — physical drives across all
  storage controllers
- `volume_inventory()` — RAID / logical volumes (read-only)
- `network_adapter_inventory()` — host NICs
- `firmware_inventory()` — firmware versions per component

**Boot management**
- `boot_config()` — current one-time + persistent boot setup
- `set_next_boot(target, mode="UEFI")` — one-time override
- `set_boot_order(devices)` — persistent boot order

**Power + virtual media + serial**
- `power_action("on" / "off" / "off_hard" / "cycle" / "reboot")`
- `insert_virtual_media(url)` / `eject_virtual_media()`
- `nmi_trigger()` — kernel-panic dump trigger

**Logs**
- `sel_entries(limit=100)` — System Event Log / Lifecycle Log /
  IML — tries the standard SEL path first, falls back per vendor
- `clear_sel()` — clear the log

**Network**
- `network_info()` — BMC NIC config + NTP + DNS

**Chassis control + recovery**
- `chassis_health()` — per-subsystem health rollup
- `indicator_led(state)` — chassis locator LED
- `reset_bmc()` — soft-reset the management controller

**Read-only inventory**
- `bmc_users()` — list BMC accounts
- `license_info()` — best-effort vendor license detection
  (iDRAC Express/Enterprise/Datacenter, iLO Standard/Advanced)

Plus session lifecycle handling — SessionService + Basic-auth
fallback, token refresh, retry-on-401 — so the auth quirks don't
bleed into your code.

## What this is NOT

- **Not a full Redfish client.** We map the operations BMC
  operators reach for. If you need something we don't expose
  (vendor-specific Oem actions on resources we don't enumerate),
  go write that PR — the library is small enough to extend.
- **Not an IPMI client.** Some Supermicro / Lenovo gear still
  wants IPMI for power-control corner cases; we recommend keeping
  `ipmitool` around for those. If there's demand we'll add a
  `bmc_adapters.ipmi` module under the same shape.
- **Not a CLI.** (Maybe soon — see "Coming soon" below.)
- **Not certified Redfish-conformant.** Real BMC firmware ships
  bugs; the library absorbs them rather than pretending the spec
  is the world.
- **No firmware updates, no BIOS attribute CRUD, no RAID
  configuration, no BMC-user CRUD.** Each is vendor-quirk-hell
  with a different per-vendor shape; lumping them in would change
  the library's shape. They are intentionally out of scope.

## Install

```bash
pip install kvmfleet-bmc-adapters
```

Requires Python 3.11+.

## Quick start

```python
import asyncio
from bmc_adapters import RedfishClient

async def main():
    async with RedfishClient(
        base_url="https://idrac.example.com",
        username="root",
        password="calvin",
    ) as client:
        snap = await client.heartbeat()
        print(snap)
        # HeartbeatSnapshot(online=True, power_state='On', cpu_temp_c=51.5, health='OK')

        info = await client.system_info()
        print(info.manufacturer, info.model, info.bios_version)

        for t in await client.temperatures():
            if t.status not in ("OK", None):
                print(f"unhealthy sensor: {t.name} = {t.reading_c}C ({t.status})")

        for d in await client.drive_inventory():
            if d.failure_predicted:
                print(f"drive failure predicted: {d.name} ({d.model})")

        await client.power_action("cycle")
        await client.insert_virtual_media("https://my-iso-host/ubuntu.iso")

asyncio.run(main())
```

### Keeping secrets encrypted at rest

The `password` argument accepts a string OR a zero-arg callable
(sync or async) returning a string. If you keep BMC creds encrypted
in your own secret store, hand the library a getter:

```python
async def get_pw():
    return await my_vault.decrypt(blob)

async with RedfishClient(
    base_url=..., username=..., password=get_pw,
) as client:
    ...
```

The library calls the getter exactly once per login (not per
request) and never stores or logs the plaintext.

### TLS

`verify_tls` defaults to **false** because roughly 98% of
factory-shipped BMCs serve self-signed certs. The connection is
still TLS-encrypted; we just don't validate the leaf chain in that
default. Flip to true once you've pinned a real cert on the BMC:

```python
RedfishClient(..., verify_tls=True)
```

If you need certificate-pinning (SPKI-pin enforcement), the
underlying `httpx.AsyncClient` exposes the hooks; we may add a
first-class `verify_pin` argument if there's demand.

## Supported vendors

See [docs/supported-vendors.md](docs/supported-vendors.md) for the
honest list of vendor + firmware version pairs we've tested
against. The bulk of the test fleet is iDRAC 7/8/9; iLO 4/5 and
Supermicro are tested less heavily; Lenovo XCC and OpenBMC are
fixture-tested only. PRs adding a new pair (with a representative
response fixture) are welcome.

## Honest caveats

- **`None` means "no signal", not "zero".** Vendors partially
  implement the Redfish schema; the dataclass types return `None`
  for any field the firmware leaves blank. Don't compare against
  `0` for sensor readings.
- **`predicted_life_left_percent` is unreliable on consumer
  SSDs.** Vendor support varies wildly; enterprise drives are
  honest, consumer drives often lie or omit the field.
- **`firmware_inventory()` shape differs by vendor.** iDRAC
  enumerates 30+ components (BIOS, BMC, each NIC, each drive,
  PSU FW); iLO is similar; Supermicro tends to expose fewer.
  Don't assume a fixed component set.
- **`license_info()` is best-effort.** iDRAC and iLO surface
  this through different Oem trees; other vendors usually
  return mostly-empty. The absence of license info doesn't
  mean the system is unlicensed.

## Why does this exist

We needed multi-vendor BMC access for [KVM Fleet](https://kvmfleet.io)'s
hosted access-governance platform. The DMTF Redfish standard is
the right shape for the protocol layer, but real BMC firmware
ships with vendor quirks (SessionService returning 204 No Content,
MediaTypes missing on single-slot configurations, basic-auth-only
when the spec says otherwise) that no library we found absorbed
cleanly. So we wrote our own.

Open-sourcing it because:

- The protocol layer isn't where our value lives. Our value is
  the hosted access governance, audit chain, EU-resident
  retention, and the operational SLA on top.
- Anyone building tooling against multi-vendor BMCs hits the same
  vendor-quirks pit we did. No need for everyone to re-hit it.
- Open code means hostile reviewers can verify the auth flow, the
  retry logic, the TLS defaults. That trust transfer matters more
  to us than gatekeeping.

This is one of a series of OSS extractions from the KVM Fleet
platform. See [BUSINESS.md §N](https://github.com/KVMFleet/kvmfleet/blob/main/kvmfleet/BUSINESS.md)
for the doctrine.

## Comparison

- **Sushy** (OpenStack / `openstack/sushy`): the reference
  multi-vendor Redfish client in Python (~15k LoC). Shaped for
  OpenStack Ironic. If you're standing up Ironic, use Sushy.
- **HPE python-ilorest-library**: vendor-specific (iLO). Use it
  if you only run iLO and you want the full iLO surface
  including features we don't cover (firmware updates, BIOS
  attributes).
- **DMTF Redfish-Tacklebox**: reference scripts + toolkit from
  DMTF itself. A reference, not a library to vendor.
- **check_redfish** (bb-Ricardo): monitoring plugin. Solves a
  different shape — output to a monitoring system, not a Python
  library you import.

`bmc-adapters` sits in the gap: smaller and operator-shaped, not
OpenStack-shaped; multi-vendor, not iLO-only; library, not
monitoring plugin or script collection.

## Coming soon

These are real plans, not roadmap theatre:

- **CLI tool** (`kvmfleet-bmc`) wrapping the library for
  one-shot ops. Useful by itself; doubles as a usability test
  of the library.
- **Vendor contribution template** for adding new BMC firmware
  to the test matrix without a maintainer review-cycle
  bottleneck.
- **IPMI module** (`bmc_adapters.ipmi`) if there's demand —
  Supermicro power-control on older firmware still benefits
  from IPMI.
- **Event subscription API** — Redfish supports RedfishEvent
  (HTTP push of BMC alerts). Bigger ergonomic shape (callback
  URL, signed verification) so it sits in a follow-up.

If you'd find any of these useful right now, open an issue
saying so. Real demand is what we prioritise on.

## Contributing

Bug reports and patches welcome. The library is small enough that
a serious contribution can be reviewed in a day. See
[docs/contributing.md](docs/contributing.md) for the local dev setup.

## License

Apache 2.0 — see [LICENSE](LICENSE). Copyright 2026 KVM Fleet.

## Links

- [KVM Fleet](https://kvmfleet.io) — the hosted platform this came from
- [audit-verify](https://github.com/KVMFleet/audit-verify) — the OSS
  audit-chain verifier (BSL 1.1)
- [agent](https://github.com/KVMFleet/agent) — the OSS device agent
  (Apache 2.0)
- [mcp](https://github.com/KVMFleet/mcp) — the read-only MCP server
  for AI assistants (MIT)
