"""Shared transports used by multiple adapters.

Currently:

- `ssh` — asyncssh-based persistent connection with per-host
  command multiplexing. Used by the RACADM adapter and any
  future SSH-CLI wrappers.
"""
from .ssh import AsyncSSHCLIClient, SSHCreds

__all__ = ["AsyncSSHCLIClient", "SSHCreds"]
