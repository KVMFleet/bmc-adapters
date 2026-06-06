# Changelog

All notable changes to `kvmfleet-bmc-adapters` are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/).

## [0.5.0] — 2026-06-05

Tier 2 expansion: PiKVM ATX, IPMI Serial-over-LAN, and two more
PDU vendors.

### Added — PiKVM adapter

- `bmc_adapters.pikvm.PiKVMClient` — async client for PiKVM's
  `kvmd` ATX endpoint (`/api/atx`, `/api/atx/power?action=...`).
  Friendly verbs `on / off / off_hard / cycle / reboot` collapse
  to kvmd's ATX action set.
- Default-credential finding for the documented `admin/admin`.
- Useful when KVM Fleet (or any orchestrator) needs to address a
  PiKVM-managed target under the same shape as a BMC.

### Added — IPMI Serial-over-LAN

- `bmc_adapters.ipmi.sol_session(client)` — async context manager
  that wraps `pyghmi.ipmi.console.Console` callbacks behind an
  async iterator + send coroutine pair.
- Always sends Deactivate Payload on exit (Supermicro X10 SoL
  hang fix).

### Added — PDU vendors

- `bmc_adapters.pdu.TrippLitePDUClient` — Tripp Lite WEBCARDLX
  (TRIPPLITE-PRODUCTS-MIB).
- `bmc_adapters.pdu.CyberPowerPDUClient` — CyberPower
  PDU15Mxxx/20Mxxx/30Mxxx (CyberPower-MIB).

### Deferred from this release

- JetKVM adapter — held until we have hardware to test against
  (per the project's no-overpromising rule).

## [0.4.0] — 2026-06-05

Multi-protocol expansion. The library is no longer Redfish-only — it
now covers the operator-facing surface across every relevant out-of-
band path.

### Added — IPMI

- `bmc_adapters.ipmi.IPMIClient` — async wrapper around `pyghmi`
  (Apache 2.0). Mirrors `RedfishClient` shape: `power_action`,
  `chassis_status`, `sensors`, `fru`, `sel_entries`, `sel_clear`.
- Secure defaults: refuses IPMI 1.5, refuses cipher suites
  0/1/2/6/7/8/11/12, prefers cipher 17 (SHA-256) and falls back to
  cipher 3 (SHA-1).
- Default-credential detection — constant-time compare against a
  documented vendor/user/password table. The library never *probes*
  the BMC with a default cred; the check is local.
- Vendor fingerprinting via Get Device ID Manufacturer ID (IANA
  Enterprise number).
- Optional dependency — install with `[ipmi]` extra.

### Added — Smart PDU control

- `bmc_adapters.pdu.APCPDUClient` — APC AP86xx / AP88xx / AP89xx via
  SNMPv2c or SNMPv3 (PowerNet-MIB).
- `bmc_adapters.pdu.EatonPDUClient` — Eaton ePDU G4 via
  EATON-EPDU-MIB.
- `bmc_adapters.pdu.RaritanPDUClient` — Raritan PX2 / PX3 / PX4 (and
  rebranded Legrand PDUs) via JSON-RPC over HTTPS.
- `bmc_adapters.pdu.vendor_from_sysobjectid()` — vendor auto-detect
  from SNMP `sysObjectID` prefix.
- Refuses SNMPv2c by default unless `allow_snmpv2c=True` is passed;
  emits `PDU_SNMPV2C_PLAINTEXT` finding when accepted.
- Default-credential warnings for APC `apc/apc`, Raritan
  `admin/raritan`, Legrand `admin/legrand@1`.
- Optional dependency — install with `[pdu]` extra.

### Added — Wake-on-LAN

- `bmc_adapters.wake_on_lan()` — async wrapper around the stdlib
  socket sender; pure-stdlib, no dependency.
- `bmc_adapters.wake_on_lan_sync()` — synchronous variant.

### Added — Cross-protocol orchestration

- `bmc_adapters.BMC` — top-level orchestrator composing Redfish +
  IPMI + PDU adapters. Dispatches `power_action` to the first
  adapter that supports it. Inspired by `bmclib`'s registry
  pattern (github.com/bmc-toolbox/bmclib).
- `bmc_adapters.BMCFinding` — structured security observations
  emitted by adapters (cipher 0 accepted, default credentials
  matched, Pantsdown firmware window, etc.). JSON-serialisable
  via `.to_dict()`. Hooks into KVM Fleet's Merkle audit chain in
  the hosted product.
- `bmc_adapters.Feature` enum — taxonomy of operations an OOB
  path may support (POWER_STATE, OUTLET_CONTROL, SOL, SEL_READ,
  ...). Used by the orchestrator for feature dispatch.
- `bmc_adapters.matches_default_credential()` — constant-time
  detector for documented vendor/user/password defaults.
- `bmc_adapters.pantsdown_finding()` — Pantsdown / CVE-2019-6260
  fingerprint helper for AST2400/AST2500 BMC firmware.

### Changed

- README hero rewritten to reflect multi-protocol scope.
- Package `description` updated.
- `keywords` expanded.

### Migration notes

All v0.3.0 APIs (`RedfishClient` and friends) remain compatible.
No breaking changes. The new protocol modules are independent.

## [0.3.0] — 2026-06-05

Maximise-within-scope pass: expand `RedfishClient` from a platform-pull
minimum to the full operator surface a BMC adapter should reasonably
expose. No breaking changes — all v0.1.0 APIs remain.

### Added (23 methods)

- **Identity + topology** — `system_info`, `chassis_health`
- **Sensors** — `temperatures`, `fans`, `power_supplies`, `power_metrics`
- **Inventory** — `processors`, `memory_modules`, `drives`, `volumes`,
  `network_adapters`, `firmware_inventory`
- **Boot** — `boot_config`, `set_next_boot`, `set_boot_order`
- **System event log** — `sel_entries`, `clear_sel` (probes SEL,
  iDRAC Lifecycle, and iLO IML registries in turn)
- **BIOS / settings** — `bios_attributes`, `set_bios_attribute`
- **Power control extras** — `force_off`, `graceful_shutdown`,
  `graceful_restart`, `nmi`

All new methods follow the existing async + vendor-quirks-handling
pattern from v0.1.0; tests use `respx` to mock vendor responses.

## [0.1.0] — 2026-06-03

Initial public release. Extracted from the production KVM Fleet platform
code (in production since 2026-Q2) under Apache 2.0.

### Added

- `RedfishClient` — async Redfish client with:
  - SessionService auth + HTTP Basic-auth fallback for vendor firmware
    that returns 204/404/405/2xx-without-token on `/SessionService/Sessions`
  - Cached session token with auto-refresh + retry-on-401
  - Per-client TLS-verify toggle (defaults false because of factory
    self-signed certs)
  - Plaintext-or-callable password source (sync + async callable)
  - Power actions mapped from four friendly verbs (`on` / `off` /
    `off_hard` / `cycle`) to `ComputerSystem.Reset`
  - Virtual media insert / eject with vendor-quirks handling (pre-eject
    busy slots, fall back to first slot when MediaTypes missing)
- `HeartbeatSnapshot` dataclass for poll-cycle state
- `RedfishError` for protocol-level failures
- Test suite using `respx` to mock httpx — no live BMC required
- CI: ruff + mypy + pytest on Python 3.11 / 3.12
