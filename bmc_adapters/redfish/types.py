"""Shared dataclass types for the Redfish client.

Every type here is intentionally a frozen-ish dataclass — no
methods, no validation — so callers can pickle, JSON-encode, or
log them without surprises. Fields are `None` whenever the vendor
firmware doesn't surface the data (most BMCs partially implement
the Redfish schema). Callers should treat `None` as "no signal"
rather than "zero."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HeartbeatSnapshot:
    """One poll cycle's worth of state for a Redfish device.

    `cpu_temp_c` is None when no thermal reading was discoverable; this
    is normal on some firmware (older Supermicro, certain OpenBMC builds).
    Callers should treat None as "no signal" rather than "zero."
    """
    online: bool
    power_state: str | None      # "On" / "Off" / "PoweringOn" / "PoweringOff" / ...
    cpu_temp_c: float | None     # CPU socket temp; falls back to chassis inlet if no CPU sensor
    health: str | None           # "OK" / "Warning" / "Critical"


# --- System identification ------------------------------------------------


@dataclass
class SystemInfo:
    """High-level identification of the managed server.

    Pulled from `/redfish/v1/Systems/{id}` + the first chassis +
    the first manager (BMC). Vendor differences:
      - iDRAC populates AssetTag from the BIOS asset-tag field.
      - iLO 4 sometimes leaves Model empty; we fall back to the
        Chassis Model.
      - Supermicro often has a richer Oem block; we don't decode
        it (use the raw Oem if you need vendor-specifics).
    """
    manufacturer: str | None     # "Dell Inc." / "HPE" / "Supermicro"
    model: str | None            # "PowerEdge R7525" / "ProLiant DL380 Gen10" / ...
    serial_number: str | None    # service tag (Dell) / serial (most)
    asset_tag: str | None        # operator-set asset tag, often blank
    sku: str | None
    host_name: str | None        # configured OS-side hostname (not the BMC name)
    uuid: str | None             # SMBIOS UUID; identifies the system across reinstalls
    bios_version: str | None     # "2.15.1" — string format is vendor-defined
    bmc_firmware_version: str | None
    bmc_model: str | None        # "iDRAC9" / "iLO 5" / "BMC" — sometimes blank


# --- Sensors --------------------------------------------------------------


@dataclass
class TemperatureReading:
    """A single thermal sensor reading.

    `reading_c` is None when the sensor is present but currently
    unreadable (common on cold-boot for ambient sensors). The
    `upper_critical_c` and `upper_non_critical_c` thresholds are
    None when the BMC doesn't publish them — most do, some
    Supermicro variants don't.
    """
    name: str
    reading_c: float | None
    upper_non_critical_c: float | None
    upper_critical_c: float | None
    status: str | None           # "OK" / "Warning" / "Critical"
    physical_context: str | None # "CPU" / "Intake" / "Exhaust" / "SystemBoard" / ...


@dataclass
class FanReading:
    name: str
    reading_rpm: int | None
    reading_percent: int | None  # some BMCs report PWM% instead of RPM
    status: str | None
    upper_non_critical_rpm: int | None
    lower_non_critical_rpm: int | None


@dataclass
class PowerSupplyReading:
    """One PSU. `input_voltage_v` is line voltage; `power_output_w`
    is what the PSU is currently delivering to the chassis.
    `power_capacity_w` is the PSU's rated output (e.g. 750)."""
    name: str
    model: str | None
    serial_number: str | None
    status: str | None
    power_capacity_w: int | None
    power_output_w: int | None
    input_voltage_v: float | None
    input_power_w: int | None
    line_input_voltage_type: str | None  # "AC120V" / "AC240V" / "DC380V"
    redundancy_status: str | None        # "OK" / "Failed" / None


@dataclass
class PowerMetrics:
    """Chassis-level aggregate power. Pulled from
    /Chassis/{id}/Power.PowerControl[0]. Most BMCs surface this
    even when they refuse to expose individual PSU output."""
    consumed_w: int | None       # current draw
    average_w: int | None        # configurable averaging interval
    min_w: int | None
    max_w: int | None
    limit_w: int | None          # power cap if set; None = no cap


# --- Boot management ------------------------------------------------------


@dataclass
class BootConfig:
    """Current boot configuration. `boot_source_override_target` is
    the one-time override that's still pending; cleared after the
    next boot. `boot_order` is the persistent list (Redfish 1.5+).
    Vendor variance: iDRAC supports `Once` + `Continuous` override
    modes, iLO 4 only supports `Once`. We surface both knobs;
    set_next_boot() defaults to `Once`."""
    boot_source_override_enabled: str | None  # "Disabled" / "Once" / "Continuous"
    boot_source_override_target: str | None   # "None" / "Pxe" / "Hdd" / "Cd" / "UsbStick" / "BiosSetup"
    boot_source_override_mode: str | None     # "Legacy" / "UEFI"
    boot_order: list[str] = field(default_factory=list)  # ordered persistent boot devices


# --- Network --------------------------------------------------------------


@dataclass
class NetworkInfo:
    """BMC management-interface network configuration. This is the
    BMC's own NIC (used to reach the iDRAC/iLO web UI etc.), NOT
    the host OS NICs.

    Some firmware exposes multiple EthernetInterfaces; we return
    the first one. If you need all of them, call get_managers() +
    walk the EthernetInterfaces collection directly."""
    hostname: str | None
    fqdn: str | None
    mac_address: str | None
    ipv4_address: str | None
    ipv4_gateway: str | None
    ipv4_subnet_mask: str | None
    ipv4_origin: str | None      # "DHCP" / "Static"
    ipv6_address: str | None
    dns_servers: list[str] = field(default_factory=list)
    ntp_servers: list[str] = field(default_factory=list)


# --- System Event Log -----------------------------------------------------


@dataclass
class SelEntry:
    """One System Event Log entry. Different vendors call this
    different things:
      - iDRAC: Lifecycle Log (and a separate SEL on older firmware)
      - iLO:   Integrated Management Log (IML)
      - Supermicro / OpenBMC: SEL via standard Redfish LogService

    We pull from `/Systems/{id}/LogServices/Sel/Entries` first; if
    that's empty/missing, we try `/Managers/{id}/LogServices/Lclog`
    (iDRAC) and `/Managers/{id}/LogServices/IML` (iLO).
    """
    id: str
    created: datetime | None
    severity: str | None         # "OK" / "Warning" / "Critical"
    message: str
    message_id: str | None       # vendor-defined event id e.g. "SEL.0001"
    sensor_type: str | None
    entry_code: str | None


# --- Firmware inventory --------------------------------------------------


@dataclass
class FirmwareComponent:
    """One firmware-tracked component. Inventory pulled from
    /UpdateService/FirmwareInventory. Vendors disagree on what
    counts as a component: iDRAC exposes 30+ entries (BIOS, BMC,
    each NIC, each drive, PSU firmware, RAID controller); iLO is
    similar; Supermicro tends to expose fewer.

    `updatable` is best-effort — Redfish's Updateable flag is
    optional and not all vendors set it correctly. Don't trust it
    as a definitive answer."""
    id: str
    name: str
    version: str | None
    manufacturer: str | None
    release_date: datetime | None
    software_id: str | None      # vendor PnP / hardware identifier
    updatable: bool | None


# --- BMC users (read-only) -----------------------------------------------


@dataclass
class BmcUser:
    """One BMC account. Read-only — we do NOT support user CRUD in
    this library (different shape, vendor-quirk hell). For
    inventory + audit purposes only."""
    id: str
    user_name: str
    role_id: str | None          # "Administrator" / "Operator" / "ReadOnly" — values vary by vendor
    enabled: bool | None
    locked: bool | None


# --- License (vendor Oem) ------------------------------------------------


@dataclass
class LicenseInfo:
    """Licensing state. Vendor-specific:
      - iDRAC: 'Express' / 'Enterprise' / 'Datacenter'
      - iLO:   'iLO Standard' / 'iLO Advanced' / 'iLO Essentials'
      - Supermicro: usually 'OOB' license
    Empty / None on vendors that don't expose this. We do
    best-effort detection from the Oem block + LicenseService when
    available; treat the result as informational."""
    vendor: str | None           # "Dell" / "HPE" / "Supermicro"
    license_type: str | None     # vendor's marketing name for the SKU
    license_key_fingerprint: str | None  # never the full key
    expires_at: datetime | None
    features: list[str] = field(default_factory=list)


# --- Hardware inventory --------------------------------------------------


@dataclass
class ProcessorInfo:
    """One CPU socket. Cores / threads / vendor / model. Not all
    BMCs populate `instruction_set` or `manufacturer`; common on
    AMD-based systems where only the brand string is exposed."""
    id: str
    socket: str | None           # "CPU 1" / "Proc 1" / "P1"
    model: str | None
    manufacturer: str | None
    instruction_set: str | None  # "x86-64" / "ARM-A64"
    max_speed_mhz: int | None
    total_cores: int | None
    total_threads: int | None
    status: str | None


@dataclass
class MemoryModule:
    """One DIMM. Size, channel, speed, manufacturer, serial."""
    id: str
    name: str | None             # "DIMM A1" / "DIMM_P1_A1"
    capacity_mib: int | None
    operating_speed_mhz: int | None
    rated_speed_mhz: int | None
    manufacturer: str | None
    part_number: str | None
    serial_number: str | None
    memory_type: str | None      # "DDR4" / "DDR5"
    channel: str | None
    status: str | None


@dataclass
class StorageDrive:
    """One physical storage drive. Pulled from Storage / Drives.
    Don't trust `predicted_life_left_percent` on consumer SSDs —
    vendor support varies wildly. Enterprise drives are honest."""
    id: str
    name: str | None
    model: str | None
    manufacturer: str | None
    serial_number: str | None
    capacity_bytes: int | None
    media_type: str | None       # "SSD" / "HDD"
    protocol: str | None         # "SAS" / "SATA" / "NVMe"
    rotation_speed_rpm: int | None
    predicted_life_left_percent: int | None  # vendor support varies
    status: str | None
    failure_predicted: bool | None


@dataclass
class StorageVolume:
    """One logical volume (RAID array, LVM, etc.). Read-only —
    we don't expose volume CRUD (vendor-quirk hell). Useful for
    inventory and reporting."""
    id: str
    name: str | None
    raid_type: str | None        # "RAID0" / "RAID1" / "RAID10" / ...
    capacity_bytes: int | None
    block_size_bytes: int | None
    status: str | None
    drives: list[str] = field(default_factory=list)  # drive @odata.id refs


@dataclass
class NetworkAdapter:
    """One host-side network adapter (NOT the BMC NIC — use
    NetworkInfo for that). Pulled from
    /Chassis/{id}/NetworkAdapters."""
    id: str
    name: str | None
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    part_number: str | None
    firmware_version: str | None
    port_count: int | None
    status: str | None


# --- Chassis health ------------------------------------------------------


@dataclass
class HealthRollup:
    """Aggregate health across the chassis sub-components. Each
    sub-component is None when the BMC doesn't separately surface
    it (common on entry-level systems)."""
    overall: str | None          # "OK" / "Warning" / "Critical"
    system: str | None
    processor: str | None
    memory: str | None
    storage: str | None
    power: str | None
    thermal: str | None
    network: str | None
    bmc: str | None
