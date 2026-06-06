"""PiKVM ATX adapter — power control for a PiKVM-connected target.

PiKVM is a remote KVM-over-IP device built on a Raspberry Pi. Its
`kvmd` daemon exposes an HTTP API for ATX power control (front-panel
power button + reset button driven by GPIO relays).

This adapter wraps `/api/atx/power` so a PiKVM appears in
`bmc_adapters` under the same shape as a BMC. Useful when KVM Fleet
manages mixed targets: real servers via Redfish + lab boxes via
PiKVM through one orchestrator.

Auth model: kvmd accepts either `X-KVMD-User` + `X-KVMD-Passwd`
headers (default since kvmd 3.x) or HTTP Basic auth (older firmware).
Defaults to TLS-verify off because every shipping PiKVM has a
self-signed cert until the operator installs Let's Encrypt.
"""
from .client import PiKVMClient

__all__ = ["PiKVMClient"]
