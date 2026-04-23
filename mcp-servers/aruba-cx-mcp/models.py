"""Pydantic data models for the Aruba CX MCP server.

Defines target configuration, error responses, and domain response models
for all tool categories (system, interfaces, VLANs, routing, LLDP, MAC table,
optics/DOM, ISSU, and audit logging).
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Core Models ---


class ErrorCode(str, Enum):
    """Error classification categories for Aruba CX API interactions."""

    CONNECTION_ERROR = "CONNECTION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    SSL_ERROR = "SSL_ERROR"
    API_ERROR = "API_ERROR"
    ITSM_ERROR = "ITSM_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"


class ArubaCxTarget(BaseModel):
    """Target switch configuration with validation.

    Required fields: name, host, username, password.
    Port must be in range 1-65535.
    """

    name: str
    host: str
    username: str
    password: str
    port: int = Field(default=443, ge=1, le=65535)
    api_version: str = "v10.13"
    verify_ssl: bool = True


class ArubaCxError(BaseModel):
    """Structured error response returned by tools on failure."""

    code: ErrorCode
    message: str
    target: Optional[str] = None
    details: Optional[str] = None
    http_status: Optional[int] = None


# --- System Models ---


class SystemInfo(BaseModel):
    """System information returned by get_system_info."""

    hostname: str
    firmware_version: str
    platform_name: str
    serial_number: str
    uptime_seconds: int


class TemperatureReading(BaseModel):
    """A single temperature sensor reading."""

    sensor_name: str
    temperature_celsius: float
    status: str


class FanStatusEntry(BaseModel):
    """A single fan status entry."""

    name: str
    status: str
    speed_rpm: Optional[int] = None


class SystemStatus(BaseModel):
    """System health status returned by get_system_status."""

    cpu_utilization: float
    memory_utilization: float
    temperature_readings: list[TemperatureReading]
    fan_status: list[FanStatusEntry]


# --- Interface Models ---


class InterfaceStatistics(BaseModel):
    """Interface traffic statistics."""

    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int


class NetworkInterface(BaseModel):
    """Network interface information returned by list_interfaces / get_interface."""

    name: str
    admin_state: str
    link_state: str
    speed: str
    description: Optional[str] = None
    duplex: Optional[str] = None
    vlan_id: Optional[int] = None
    statistics: Optional[InterfaceStatistics] = None


# --- VLAN Models ---


class Vlan(BaseModel):
    """VLAN entry returned by list_vlans."""

    id: int
    name: str
    status: str


# --- Routing / ARP Models ---


class RouteEntry(BaseModel):
    """A single routing table entry."""

    destination: str
    next_hop: str
    protocol: str
    metric: int


class ArpEntry(BaseModel):
    """A single ARP table entry."""

    ip_address: str
    mac_address: str
    interface: str
    state: Optional[str] = None


# --- LLDP Models ---


class LldpNeighbor(BaseModel):
    """An LLDP neighbor entry."""

    local_interface: str
    remote_chassis_id: str
    remote_port_id: str
    remote_system_name: str
    remote_system_description: str


# --- MAC Address Table Models ---


class MacAddressEntry(BaseModel):
    """A single MAC address table entry."""

    mac_address: str
    vlan_id: int
    port: str
    type: str
    age: Optional[int] = None


# --- Optics / DOM Models ---


class TransceiverInfo(BaseModel):
    """Transceiver information returned by get_transceiver_info."""

    transceiver_type: str
    vendor_name: str
    serial_number: str
    wavelength: Optional[float] = None
    supports_dom: bool


class LaneDomReading(BaseModel):
    """Per-lane DOM diagnostic reading."""

    lane: int
    rx_power_dbm: Optional[float] = None
    tx_power_dbm: Optional[float] = None
    bias_current_ma: Optional[float] = None


class DomDiagnostics(BaseModel):
    """DOM diagnostic readings returned by get_dom_diagnostics."""

    rx_power_dbm: Optional[float] = None
    tx_power_dbm: Optional[float] = None
    temperature_celsius: Optional[float] = None
    voltage: Optional[float] = None
    bias_current_ma: Optional[float] = None
    lanes: list[LaneDomReading] = []


# --- ISSU Models ---


class IssuStatus(BaseModel):
    """ISSU operation status returned by get_issu_status."""

    status: str
    percent_complete: int = Field(ge=0, le=100)
    current_phase: str
    active_image: str
    standby_image: Optional[str] = None
    error_message: Optional[str] = None


# --- Audit Log Models ---


class AuditLogEntry(BaseModel):
    """Structured audit log entry."""

    operation: str
    timestamp: str  # UTC ISO 8601
    target: str
    status: str
    change_request_number: Optional[str] = None
    baseline: Optional[dict] = None
    verify: Optional[dict] = None


# --- Log Models ---


class LogEntry(BaseModel):
    """A single event log entry from an Aruba CX switch."""

    timestamp: str
    severity: str
    module: str
    message: str
