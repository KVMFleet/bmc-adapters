# Changelog

All notable changes to `kvmfleet-bmc-adapters` are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/).

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
