"""Dell RACADM adapter — SSH-shell wrapper around `racadm` on iDRAC.

Scope (v0.6.0):
  - Power: on / off / cycle / hardreset / graceshutdown / powerstatus
  - Inventory: getsysinfo, getversion, getsvctag
  - Logs: getsel
  - BMC mgmt: racreset

10 commands. Intentional cut from the ~80-command RACADM surface
to limit per-firmware parsing churn. Wider coverage lands in
v0.7.0 if customers ask for it.

Why this exists alongside Redfish: Redfish on iDRAC9 (4.00+)
covers ~90% of operator needs, but RACADM is still the only path
for some workflows:

- SCP-based bulk config import/export
- Lifecycle Controller log filtering by category
- `racreset` soft-reset when Redfish is wedged
- iDRAC6 / iDRAC7 (Redfish is absent or partial)
- iDRAC9 firmware below 4.00

Use the orchestrator pattern (`bmc_adapters.BMC`) to fall back from
Redfish → RACADM when Redfish times out.
"""
from .client import RACADMClient, RACADMError
from .parsers import parse_key_value_block

__all__ = ["RACADMClient", "RACADMError", "parse_key_value_block"]
