# kvmfleet-bmc-adapters

Production-grade async Python client for multi-vendor BMC Redfish access.
Powers the hosted access-governance platform at [kvmfleet.io](https://kvmfleet.io)
— released under Apache 2.0 so anyone building tooling against
mixed-vendor BMC fleets can reuse the pieces that took us a year to harden.

## What this is

A small async library that lets you do four things across **Dell iDRAC,
HPE iLO, Supermicro IPMI/BMC, Lenovo XCC, and OpenBMC** via the DMTF
Redfish standard:

- **Read state** — power, thermal, health rollup
- **Power actions** — on, off (graceful), off-hard, cycle
- **Virtual media** — mount / eject an ISO over the network
- **Session lifecycle** — handles SessionService + Basic-auth fallback,
  token refresh, retry-on-401

It is deliberately small. No CLI, no CRD, no opinions about how you
store secrets — just the protocol layer. Pair it with whatever fits
your stack.

## What this is NOT

- Not a full Redfish client. We map a useful subset that real BMC fleets
  actually use. If you need
  `/redfish/v1/Systems/{id}/Memory/{id}/Metrics`, this library won't
  surface it for you — go write that PR.
- Not an IPMI client. Some Supermicro / Lenovo gear still wants IPMI
  for power-control corner cases; we recommend keeping `ipmitool`
  around for those. If there's demand we'll add a `bmc_adapters.ipmi`
  module under the same shape.
- Not a CLI. (Maybe soon — see "Coming soon" below.)
- Not certified Redfish-conformant. Real BMC firmware ships
  bugs; the library absorbs them rather than pretending the spec is
  the world.

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

        await client.power_action("cycle")
        await client.insert_virtual_media("https://my-iso-host/ubuntu.iso")

asyncio.run(main())
```

### Keeping secrets encrypted at rest

The `password` argument accepts a string OR a zero-arg callable (sync or
async) returning a string. If you keep BMC creds encrypted in your own
secret store, hand the library a getter:

```python
async def get_pw():
    return await my_vault.decrypt(blob)

async with RedfishClient(
    base_url=..., username=..., password=get_pw,
) as client:
    ...
```

The library calls the getter exactly once per login (not per request) and
never stores or logs the plaintext.

### TLS

`verify_tls` defaults to **false** because roughly 98% of factory-shipped
BMCs serve self-signed certs. The connection is still TLS-encrypted; we
just don't validate the leaf chain in that default. Flip to true once
you've pinned a real cert on the BMC:

```python
RedfishClient(..., verify_tls=True)
```

If you need certificate-pinning (SPKI-pin enforcement), the underlying
`httpx.AsyncClient` exposes the hooks; we may add a first-class
`verify_pin` argument if there's demand.

## Supported vendors

See [docs/supported-vendors.md](docs/supported-vendors.md) for the honest
list of vendor + firmware version pairs we've tested against. PRs adding
a new pair (with a representative response fixture) are welcome.

## Why does this exist

We needed multi-vendor BMC access for [KVM Fleet](https://kvmfleet.io)'s
hosted access-governance platform. The DMTF Redfish standard is the right
shape for the protocol layer, but real BMC firmware ships with vendor
quirks (SessionService returning 204 No Content, MediaTypes missing on
single-slot configurations, basic-auth-only when the spec says otherwise)
that no library we found absorbed cleanly. So we wrote our own.

Open-sourcing it because:

- The protocol layer isn't where our value lives. Our value is the
  hosted access governance, audit chain, EU-resident retention, and
  the operational SLA on top.
- Anyone building tooling against multi-vendor BMCs hits the same
  vendor-quirks pit we did. No need for everyone to re-hit it.
- Open code means hostile reviewers can verify the auth flow, the
  retry logic, the TLS defaults. That trust transfer matters more to us
  than gatekeeping.

This is the first in a series of OSS extractions from the KVM Fleet
platform. See [BUSINESS.md §N](https://github.com/KVMFleet/kvmfleet/blob/main/kvmfleet/BUSINESS.md)
for the doctrine.

## Coming soon

These are real plans, not roadmap theatre:

- **CLI tool** (`kvmfleet-bmc`) wrapping the library for one-shot ops.
  Useful by itself; doubles as a usability test of the library.
- **Vendor contribution template** for adding new BMC firmware to the
  test matrix without a maintainer review-cycle bottleneck.
- **IPMI module** (`bmc_adapters.ipmi`) if there's demand — Supermicro
  power-control on older firmware still benefits from IPMI.

If you'd find any of these useful right now, open an issue saying so.
Real demand is what we prioritise on.

## Contributing

Bug reports and patches welcome. The library is small enough that a
serious contribution can be reviewed in a day. See
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
