"""RACADM output parsers.

Per the deep-research brief: there is no universal RACADM parser.
The output for `get`/`set` is structured key=value blocks; everything
else is per-command. We ship parsers only for the 10-command core.

The parsers are deliberately permissive — Dell quietly changes
`getsysinfo` field ordering between iDRAC9 4.x and 7.x. Keys callers
need are lowercase-stripped to insulate against case churn.
"""
from __future__ import annotations

import re

_ERROR_RE = re.compile(r"^ERROR\s*:?\s*(RAC\d+)?\s*(.*)$", re.MULTILINE)


def detect_error(out: str) -> tuple[str, str] | None:
    """Return (rac_code, message) if RACADM reports an error, else None."""
    m = _ERROR_RE.search(out)
    if m is None:
        return None
    return (m.group(1) or "", m.group(2).strip())


def parse_key_value_block(text: str) -> dict[str, str]:
    """Parse `key = value` / `key: value` blocks (getsysinfo, etc.)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        m = re.match(r"([^=:]+?)\s*[=:]\s*(.*)$", line)
        if m is None:
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        out[key] = val
    return out


_POWER_STATE_RE = re.compile(
    r"Server\s+power\s+status\s*[:=]\s*(\w+)", re.IGNORECASE
)


def parse_power_status(text: str) -> str:
    """Map `racadm serveraction powerstatus` output to {on,off,unknown}."""
    m = _POWER_STATE_RE.search(text)
    if m is None:
        # Some firmware emits the literal word on its own line.
        for line in text.splitlines():
            s = line.strip().lower()
            if s in ("on", "off"):
                return s
        return "unknown"
    state = m.group(1).strip().lower()
    if state in ("on", "off"):
        return state
    return "unknown"


def parse_version(text: str) -> dict[str, str]:
    """Parse `racadm getversion` — returns firmware components."""
    return parse_key_value_block(text)
