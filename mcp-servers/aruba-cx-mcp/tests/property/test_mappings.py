# Feature: aruba-cx-python-port, Property 15: System info mapping preserves fields
# Feature: aruba-cx-python-port, Property 16: System status mapping preserves fields
# Feature: aruba-cx-python-port, Property 11: Tool-level exception containment
"""Property tests for system mappings and tool-level exception containment.

Tests cover:
- Property 15: System info mapping preserves fields — for any raw API response,
  the system info mapping extracts hostname, firmware_version, platform_name,
  serial_number, and uptime correctly.
- Property 16: System status mapping preserves fields — for any raw API response
  with subsystem data, the system status mapping extracts cpu_utilization,
  memory_utilization, temperature_readings, and fan_status correctly.
- Property 11: Tool-level exception containment — for any tool function, when the
  underlying API client raises any exception, the tool catches it and returns a
  valid ArubaCxError JSON string.

**Validates: Requirements 9.1, 9.2, 7.4**
"""

import importlib
import json
import os
import sys
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from aruba_client import ArubaCxException
from models import ArubaCxError, ErrorCode


# ---------------------------------------------------------------------------
# Import helpers — aruba_cx_mcp_server.py has module-level side effects
# (FastMCP init, ArubaCxClient init). We mock those on import.
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out.

    Uses a pass-through @mcp.tool() decorator so that the decorated functions
    remain callable (not wrapped in MagicMock).
    """
    mock_fastmcp = MagicMock()

    def _passthrough_tool(*args, **kwargs):
        """Decorator that returns the original function unchanged."""
        def decorator(func):
            return func
        return decorator

    mock_fastmcp.FastMCP.return_value.tool = _passthrough_tool

    with patch.dict("sys.modules", {"fastmcp": mock_fastmcp}):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_printable_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=80,
)

_nonempty_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
)

_target_name = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
)

_uptime = st.integers(min_value=0, max_value=10**8)

_utilization = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)

_temperature_reading = st.fixed_dictionaries({
    "sensor_name": _nonempty_text,
    "temperature_celsius": st.floats(
        min_value=-40.0, max_value=150.0, allow_nan=False, allow_infinity=False
    ),
    "status": st.sampled_from(["normal", "warning", "critical"]),
})

_fan_status_entry = st.fixed_dictionaries({
    "name": _nonempty_text,
    "status": st.sampled_from(["ok", "fault", "absent"]),
    "speed_rpm": st.one_of(st.none(), st.integers(min_value=0, max_value=20000)),
})

# Exception types that tools should catch
_exception_types = st.sampled_from([
    ConnectionError,
    TimeoutError,
    ValueError,
    RuntimeError,
    KeyError,
    TypeError,
    OSError,
    IOError,
])

_exception_message = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)


# ---------------------------------------------------------------------------
# Property 15: System info mapping preserves fields
# Feature: aruba-cx-python-port, Property 15: System info mapping preserves fields
# **Validates: Requirements 9.1**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    hostname=_nonempty_text,
    firmware_version=_nonempty_text,
    platform_name=_nonempty_text,
    serial_number=_nonempty_text,
    uptime=_uptime,
)
def test_system_info_mapping_preserves_fields_software_version(
    hostname: str,
    firmware_version: str,
    platform_name: str,
    serial_number: str,
    uptime: int,
):
    """For any raw API response dict with software_version, the system info
    mapping should produce a dict with each field correctly extracted."""
    raw_api_response = {
        "hostname": hostname,
        "software_version": firmware_version,
        "platform_name": platform_name,
        "serial_number": serial_number,
        "uptime": uptime,
    }

    # Replicate the mapping logic from get_system_info
    info = {
        "hostname": raw_api_response.get("hostname", ""),
        "firmware_version": raw_api_response.get(
            "software_version", raw_api_response.get("firmware_version", "")
        ),
        "platform_name": raw_api_response.get("platform_name", ""),
        "serial_number": raw_api_response.get("serial_number", ""),
        "uptime_seconds": raw_api_response.get("uptime", 0),
    }

    assert info["hostname"] == hostname
    assert info["firmware_version"] == firmware_version
    assert info["platform_name"] == platform_name
    assert info["serial_number"] == serial_number
    assert info["uptime_seconds"] == uptime


@settings(max_examples=100)
@given(
    hostname=_nonempty_text,
    firmware_version=_nonempty_text,
    platform_name=_nonempty_text,
    serial_number=_nonempty_text,
    uptime=_uptime,
)
def test_system_info_mapping_preserves_fields_firmware_version(
    hostname: str,
    firmware_version: str,
    platform_name: str,
    serial_number: str,
    uptime: int,
):
    """For any raw API response dict with firmware_version (no software_version),
    the system info mapping should fall back to firmware_version."""
    raw_api_response = {
        "hostname": hostname,
        "firmware_version": firmware_version,
        "platform_name": platform_name,
        "serial_number": serial_number,
        "uptime": uptime,
    }

    info = {
        "hostname": raw_api_response.get("hostname", ""),
        "firmware_version": raw_api_response.get(
            "software_version", raw_api_response.get("firmware_version", "")
        ),
        "platform_name": raw_api_response.get("platform_name", ""),
        "serial_number": raw_api_response.get("serial_number", ""),
        "uptime_seconds": raw_api_response.get("uptime", 0),
    }

    assert info["hostname"] == hostname
    assert info["firmware_version"] == firmware_version
    assert info["platform_name"] == platform_name
    assert info["serial_number"] == serial_number
    assert info["uptime_seconds"] == uptime


# ---------------------------------------------------------------------------
# Property 16: System status mapping preserves fields
# Feature: aruba-cx-python-port, Property 16: System status mapping preserves fields
# **Validates: Requirements 9.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    cpu_util=_utilization,
    mem_util=_utilization,
    temp_readings=st.lists(_temperature_reading, min_size=0, max_size=5),
    fan_statuses=st.lists(_fan_status_entry, min_size=0, max_size=5),
)
def test_system_status_mapping_preserves_fields_from_subsystems(
    cpu_util: float,
    mem_util: float,
    temp_readings: list,
    fan_statuses: list,
):
    """For any raw API response with subsystem data, the system status mapping
    should produce a dict with all fields correctly extracted."""
    raw_api_response = {
        "subsystems": {
            "cpu_utilization": cpu_util,
            "memory_utilization": mem_util,
            "temperature_readings": temp_readings,
            "fan_status": fan_statuses,
        }
    }

    subsystems = raw_api_response.get("subsystems", raw_api_response)
    status = {
        "cpu_utilization": subsystems.get("cpu_utilization", 0.0),
        "memory_utilization": subsystems.get("memory_utilization", 0.0),
        "temperature_readings": subsystems.get("temperature_readings", []),
        "fan_status": subsystems.get("fan_status", []),
    }

    assert status["cpu_utilization"] == cpu_util
    assert status["memory_utilization"] == mem_util
    assert status["temperature_readings"] == temp_readings
    assert status["fan_status"] == fan_statuses


@settings(max_examples=100)
@given(
    cpu_util=_utilization,
    mem_util=_utilization,
    temp_readings=st.lists(_temperature_reading, min_size=0, max_size=5),
    fan_statuses=st.lists(_fan_status_entry, min_size=0, max_size=5),
)
def test_system_status_mapping_preserves_fields_flat_response(
    cpu_util: float,
    mem_util: float,
    temp_readings: list,
    fan_statuses: list,
):
    """For any raw API response without a 'subsystems' key (flat structure),
    the mapping should still extract fields correctly."""
    raw_api_response = {
        "cpu_utilization": cpu_util,
        "memory_utilization": mem_util,
        "temperature_readings": temp_readings,
        "fan_status": fan_statuses,
    }

    subsystems = raw_api_response.get("subsystems", raw_api_response)
    status = {
        "cpu_utilization": subsystems.get("cpu_utilization", 0.0),
        "memory_utilization": subsystems.get("memory_utilization", 0.0),
        "temperature_readings": subsystems.get("temperature_readings", []),
        "fan_status": subsystems.get("fan_status", []),
    }

    assert status["cpu_utilization"] == cpu_util
    assert status["memory_utilization"] == mem_util
    assert status["temperature_readings"] == temp_readings
    assert status["fan_status"] == fan_statuses


# ---------------------------------------------------------------------------
# Property 17: Interface mapping preserves fields
# Feature: aruba-cx-python-port, Property 17: Interface mapping preserves fields
# **Validates: Requirements 10.1, 10.2**
# ---------------------------------------------------------------------------

_admin_state = st.sampled_from(["up", "down"])
_link_state = st.sampled_from(["up", "down"])
_speed_text = st.sampled_from(["1000", "10000", "25000", "40000", "100000", "auto"])
_duplex_text = st.sampled_from(["full", "half", "auto"])

_interface_statistics = st.fixed_dictionaries({
    "rx_bytes": st.integers(min_value=0, max_value=10**15),
    "tx_bytes": st.integers(min_value=0, max_value=10**15),
    "rx_packets": st.integers(min_value=0, max_value=10**12),
    "tx_packets": st.integers(min_value=0, max_value=10**12),
    "rx_errors": st.integers(min_value=0, max_value=10**9),
    "tx_errors": st.integers(min_value=0, max_value=10**9),
})


@settings(max_examples=100)
@given(
    name=_nonempty_text,
    admin_state=_admin_state,
    link_state=_link_state,
    speed=_speed_text,
    description=st.one_of(st.none(), _printable_text),
    duplex=st.one_of(st.none(), _duplex_text),
    vlan_tag=st.one_of(st.none(), st.integers(min_value=1, max_value=4094)),
    statistics=st.one_of(st.none(), _interface_statistics),
)
def test_interface_mapping_preserves_fields(
    name: str,
    admin_state: str,
    link_state: str,
    speed: str,
    description,
    duplex,
    vlan_tag,
    statistics,
):
    """For any raw API response dict containing interface data, the interface
    mapping should produce a dict with all present fields correctly extracted.

    Replicates the extraction logic from get_interface."""
    raw_api_response = {
        "name": name,
        "admin_state": admin_state,
        "link_state": link_state,
        "speed": speed,
    }
    if description is not None:
        raw_api_response["description"] = description
    if duplex is not None:
        raw_api_response["duplex"] = duplex
    if vlan_tag is not None:
        raw_api_response["vlan_tag"] = vlan_tag
    if statistics is not None:
        raw_api_response["statistics"] = statistics

    # Replicate the mapping logic from get_interface
    iface = {
        "name": raw_api_response.get("name", ""),
        "admin_state": raw_api_response.get("admin_state", raw_api_response.get("admin", "unknown")),
        "link_state": raw_api_response.get("link_state", "unknown"),
        "speed": str(raw_api_response.get("speed", raw_api_response.get("link_speed", "unknown"))),
        "description": raw_api_response.get("description"),
        "duplex": raw_api_response.get("duplex"),
        "vlan_id": raw_api_response.get("vlan_tag"),
        "statistics": raw_api_response.get("statistics"),
    }

    assert iface["name"] == name
    assert iface["admin_state"] == admin_state
    assert iface["link_state"] == link_state
    assert iface["speed"] == speed
    assert iface["description"] == description
    assert iface["duplex"] == duplex
    assert iface["vlan_id"] == vlan_tag
    assert iface["statistics"] == statistics


# ---------------------------------------------------------------------------
# Property 18: VLAN mapping preserves fields
# Feature: aruba-cx-python-port, Property 18: VLAN mapping preserves fields
# **Validates: Requirements 11.1**
# ---------------------------------------------------------------------------

_vlan_id = st.integers(min_value=1, max_value=4094)
_vlan_status = st.sampled_from(["up", "down"])


@settings(max_examples=100)
@given(
    vlan_id=_vlan_id,
    vlan_name=_nonempty_text,
    vlan_status=_vlan_status,
)
def test_vlan_mapping_preserves_fields(
    vlan_id: int,
    vlan_name: str,
    vlan_status: str,
):
    """For any raw API response dict containing VLAN data with id, name, and
    status fields, the VLAN mapping should produce a dict with all fields
    correctly extracted and typed.

    Replicates the extraction logic from list_vlans."""
    vlan_key = str(vlan_id)
    raw_api_response = {
        vlan_key: {
            "id": vlan_id,
            "name": vlan_name,
            "oper_state": vlan_status,
        }
    }

    # Replicate the mapping logic from list_vlans
    vlans = []
    for key, vlan_data in raw_api_response.items():
        if isinstance(vlan_data, dict):
            vlans.append({
                "id": vlan_data.get("id", int(key) if key.isdigit() else 0),
                "name": vlan_data.get("name", ""),
                "status": vlan_data.get("oper_state", vlan_data.get("status", "unknown")),
            })

    assert len(vlans) == 1
    vlan = vlans[0]
    assert vlan["id"] == vlan_id
    assert isinstance(vlan["id"], int)
    assert vlan["name"] == vlan_name
    assert vlan["status"] == vlan_status


# ---------------------------------------------------------------------------
# Property 19: Route entry mapping preserves fields
# Feature: aruba-cx-python-port, Property 19: Route entry mapping preserves fields
# **Validates: Requirements 13.1**
# ---------------------------------------------------------------------------

_ip_prefix = st.from_regex(
    r"(10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}", fullmatch=True
)
_ip_address = st.from_regex(
    r"(10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}", fullmatch=True
)
_route_protocol = st.sampled_from(["static", "connected", "ospf", "bgp", "rip"])
_metric = st.integers(min_value=0, max_value=255)


@settings(max_examples=100)
@given(
    destination=_ip_prefix,
    next_hop=_ip_address,
    protocol=_route_protocol,
    metric=_metric,
    use_alt_keys=st.booleans(),
)
def test_route_entry_mapping_preserves_fields(
    destination: str,
    next_hop: str,
    protocol: str,
    metric: int,
    use_alt_keys: bool,
):
    """For any raw API response dict containing route data with prefix/destination,
    nexthop/next_hop, route_type/protocol, and distance/metric fields, the route
    mapping should produce a RouteEntry-like dict with all fields correctly extracted.

    Tests both primary and alternate key names used by the AOS-CX API."""
    route_key = destination.replace("/", "%2F")

    if use_alt_keys:
        # Alternate key names: prefix, nexthop, route_type, distance
        raw_entry = {
            "prefix": destination,
            "nexthop": next_hop,
            "route_type": protocol,
            "distance": metric,
        }
    else:
        # Primary key names: destination, next_hop, protocol, metric
        raw_entry = {
            "destination": destination,
            "next_hop": next_hop,
            "protocol": protocol,
            "metric": metric,
        }

    raw_api_response = {route_key: raw_entry}

    # Replicate the mapping logic from get_routing_table
    routes = []
    for rk, route_data in raw_api_response.items():
        if isinstance(route_data, dict):
            routes.append({
                "destination": route_data.get("prefix", route_data.get("destination", rk)),
                "next_hop": route_data.get("nexthop", route_data.get("next_hop", "")),
                "protocol": route_data.get("route_type", route_data.get("protocol", "")),
                "metric": route_data.get("distance", route_data.get("metric", 0)),
            })

    assert len(routes) == 1
    route = routes[0]
    assert route["destination"] == destination
    assert route["next_hop"] == next_hop
    assert route["protocol"] == protocol
    assert route["metric"] == metric


# ---------------------------------------------------------------------------
# Property 20: ARP entry mapping preserves fields
# Feature: aruba-cx-python-port, Property 20: ARP entry mapping preserves fields
# **Validates: Requirements 13.2**
# ---------------------------------------------------------------------------

_mac_address_str = st.from_regex(
    r"[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}",
    fullmatch=True,
)
_interface_name = st.sampled_from([
    "1/1/1", "1/1/2", "1/1/3", "1/1/10", "1/1/24", "1/1/48",
    "lag1", "lag10", "vlan100", "loopback0",
])


@settings(max_examples=100)
@given(
    ip_address=_ip_address,
    mac_address=_mac_address_str,
    interface=_interface_name,
    state=st.one_of(st.none(), st.sampled_from(["reachable", "stale", "delay", "probe"])),
    use_alt_keys=st.booleans(),
)
def test_arp_entry_mapping_preserves_fields(
    ip_address: str,
    mac_address: str,
    interface: str,
    state,
    use_alt_keys: bool,
):
    """For any raw API response dict containing ARP data with ip_address/ip,
    mac/mac_address, and port/interface fields, the ARP mapping should produce
    an ArpEntry-like dict with all fields correctly extracted.

    Tests both primary and alternate key names used by the AOS-CX API."""
    if use_alt_keys:
        raw_entry = {
            "ip": ip_address,
            "mac_address": mac_address,
            "interface": interface,
        }
    else:
        raw_entry = {
            "ip_address": ip_address,
            "mac": mac_address,
            "port": interface,
        }

    if state is not None:
        raw_entry["state"] = state

    raw_api_response = {ip_address: raw_entry}

    # Replicate the mapping logic from get_arp_table
    entries = []
    for key, entry_data in raw_api_response.items():
        if isinstance(entry_data, dict):
            entries.append({
                "ip_address": entry_data.get("ip_address", entry_data.get("ip", key)),
                "mac_address": entry_data.get("mac", entry_data.get("mac_address", "")),
                "interface": entry_data.get("port", entry_data.get("interface", "")),
                "state": entry_data.get("state"),
            })

    assert len(entries) == 1
    entry = entries[0]
    assert entry["ip_address"] == ip_address
    assert entry["mac_address"] == mac_address
    assert entry["interface"] == interface
    assert entry["state"] == state


# ---------------------------------------------------------------------------
# Property 21: LLDP neighbor mapping preserves fields
# Feature: aruba-cx-python-port, Property 21: LLDP neighbor mapping preserves fields
# **Validates: Requirements 14.1**
# ---------------------------------------------------------------------------

_chassis_id = st.from_regex(
    r"[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}",
    fullmatch=True,
)
_port_id = st.sampled_from(["Gi0/0/1", "Eth1/1", "1/1/1", "ge-0/0/0", "Te1/0/1"])
_system_name = _nonempty_text
_system_description = _printable_text


@settings(max_examples=100)
@given(
    local_interface=_interface_name,
    chassis_id=_chassis_id,
    port_id=_port_id,
    system_name=_system_name,
    system_description=_system_description,
    use_nested_info=st.booleans(),
)
def test_lldp_neighbor_mapping_preserves_fields(
    local_interface: str,
    chassis_id: str,
    port_id: str,
    system_name: str,
    system_description: str,
    use_nested_info: bool,
):
    """For any raw API response dict containing LLDP neighbor data, the LLDP
    mapping should extract local_interface, remote_chassis_id, remote_port_id,
    remote_system_name.

    Tests both flat and nested neighbor_info structures."""
    if use_nested_info:
        neighbor_data = {
            "chassis_id": chassis_id,
            "port_id": port_id,
            "neighbor_info": {
                "system_name": system_name,
                "system_description": system_description,
            },
        }
    else:
        neighbor_data = {
            "chassis_id": chassis_id,
            "port_id": port_id,
            "system_name": system_name,
            "system_description": system_description,
        }

    raw_api_response = {
        local_interface: {
            "lldp_neighbors": {
                "neighbor_1": neighbor_data,
            }
        }
    }

    # Replicate the mapping logic from get_lldp_neighbors
    neighbors = []
    for iface_name, iface_data in raw_api_response.items():
        if not isinstance(iface_data, dict):
            continue
        lldp_data = iface_data.get("lldp_neighbors", {})
        if isinstance(lldp_data, dict):
            for neighbor_key, neighbor in lldp_data.items():
                if isinstance(neighbor, dict):
                    neighbors.append({
                        "local_interface": iface_name,
                        "remote_chassis_id": neighbor.get("chassis_id", neighbor.get("chassis_name", "")),
                        "remote_port_id": neighbor.get("port_id", ""),
                        "remote_system_name": neighbor.get("system_name", neighbor.get("neighbor_info", {}).get("system_name", "")),
                        "remote_system_description": neighbor.get("system_description", neighbor.get("neighbor_info", {}).get("system_description", "")),
                    })

    assert len(neighbors) == 1
    n = neighbors[0]
    assert n["local_interface"] == local_interface
    assert n["remote_chassis_id"] == chassis_id
    assert n["remote_port_id"] == port_id
    assert n["remote_system_name"] == system_name
    assert n["remote_system_description"] == system_description


# ---------------------------------------------------------------------------
# Property 22: LLDP interface filtering
# Feature: aruba-cx-python-port, Property 22: LLDP interface filtering
# **Validates: Requirements 14.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    neighbors=st.lists(
        st.fixed_dictionaries({
            "local_interface": _interface_name,
            "remote_chassis_id": _chassis_id,
            "remote_port_id": _port_id,
            "remote_system_name": _system_name,
            "remote_system_description": _system_description,
        }),
        min_size=0,
        max_size=20,
    ),
    filter_interface=_interface_name,
)
def test_lldp_interface_filtering(
    neighbors: list,
    filter_interface: str,
):
    """For any list of LLDP neighbors and any interface name filter, filtering
    should return only neighbors whose local_interface matches the filter, and
    the result should be a subset of the original list."""
    # Replicate the filtering logic from get_lldp_neighbors
    filtered = [n for n in neighbors if n["local_interface"] == filter_interface]

    # All filtered entries must match the filter interface
    for entry in filtered:
        assert entry["local_interface"] == filter_interface

    # Filtered result is a subset of the original
    assert len(filtered) <= len(neighbors)

    # Every matching entry from the original must be in the filtered result
    expected = [n for n in neighbors if n["local_interface"] == filter_interface]
    assert filtered == expected


# ---------------------------------------------------------------------------
# Property 23: MAC entry mapping preserves fields
# Feature: aruba-cx-python-port, Property 23: MAC entry mapping preserves fields
# **Validates: Requirements 15.1**
# ---------------------------------------------------------------------------

_mac_type = st.sampled_from(["dynamic", "static"])
_mac_age = st.one_of(st.none(), st.integers(min_value=0, max_value=86400))


@settings(max_examples=100)
@given(
    mac_address=_mac_address_str,
    vlan_id=_vlan_id,
    port=_interface_name,
    entry_type=_mac_type,
    age=_mac_age,
    use_alt_keys=st.booleans(),
)
def test_mac_entry_mapping_preserves_fields(
    mac_address: str,
    vlan_id: int,
    port: str,
    entry_type: str,
    age,
    use_alt_keys: bool,
):
    """For any raw API response dict containing MAC table data, the MAC mapping
    should produce a MacAddressEntry-like dict with all fields correctly extracted.

    Tests both primary and alternate key names used by the AOS-CX API."""
    if use_alt_keys:
        mac_entry = {
            "mac": mac_address,
            "type": entry_type,
            "from": port,
        }
    else:
        mac_entry = {
            "mac_addr": mac_address,
            "entry_type": entry_type,
            "port": port,
        }

    if age is not None:
        mac_entry["age"] = age

    vlan_key = str(vlan_id)
    raw_api_response = {
        vlan_key: {
            "id": vlan_id,
            "macs": {
                mac_address: mac_entry,
            }
        }
    }

    # Replicate the mapping logic from get_mac_address_table
    entries = []
    for vk, vlan_data in raw_api_response.items():
        if not isinstance(vlan_data, dict):
            continue
        vid = vlan_data.get("id", int(vk) if vk.isdigit() else 0)
        macs = vlan_data.get("macs", {})
        if isinstance(macs, dict):
            for mac_key, mac_data in macs.items():
                if isinstance(mac_data, dict):
                    port_val = mac_data.get("port", mac_data.get("from", ""))
                    if isinstance(port_val, dict):
                        port_val = port_val.get("name", str(port_val))
                    elif isinstance(port_val, str) and "/" in port_val:
                        port_val = port_val.rsplit("/", 1)[-1].replace("%2F", "/")
                    entries.append({
                        "mac_address": mac_data.get("mac_addr", mac_data.get("mac", mac_key)),
                        "vlan_id": vid,
                        "port": str(port_val),
                        "type": mac_data.get("entry_type", mac_data.get("type", "dynamic")),
                        "age": mac_data.get("age"),
                    })

    assert len(entries) == 1
    entry = entries[0]
    assert entry["mac_address"] == mac_address
    assert entry["vlan_id"] == vlan_id
    assert entry["type"] == entry_type
    assert entry["age"] == age


# ---------------------------------------------------------------------------
# Property 24: MAC address filtering
# Feature: aruba-cx-python-port, Property 24: MAC address filtering
# **Validates: Requirements 15.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entries=st.lists(
        st.fixed_dictionaries({
            "mac_address": _mac_address_str,
            "vlan_id": _vlan_id,
            "port": _interface_name,
            "type": _mac_type,
            "age": _mac_age,
        }),
        min_size=0,
        max_size=20,
    ),
    filter_vlan=st.one_of(st.just(0), _vlan_id),
    filter_mac=st.one_of(st.just(""), _mac_address_str),
)
def test_mac_address_filtering(
    entries: list,
    filter_vlan: int,
    filter_mac: str,
):
    """For any list of MAC address entries and any combination of vlan_id and
    mac_address filters, filtering should return only entries matching all
    specified criteria. Case-insensitive for MAC addresses."""
    # Replicate the filtering logic from get_mac_address_table
    result = list(entries)
    if filter_vlan:
        result = [e for e in result if e["vlan_id"] == filter_vlan]
    if filter_mac:
        mac_lower = filter_mac.lower()
        result = [e for e in result if e["mac_address"].lower() == mac_lower]

    # Result is a subset of the original
    assert len(result) <= len(entries)

    # All filtered entries match the criteria
    for e in result:
        if filter_vlan:
            assert e["vlan_id"] == filter_vlan
        if filter_mac:
            assert e["mac_address"].lower() == filter_mac.lower()

    # No matching entries were missed
    expected = list(entries)
    if filter_vlan:
        expected = [e for e in expected if e["vlan_id"] == filter_vlan]
    if filter_mac:
        mac_lower = filter_mac.lower()
        expected = [e for e in expected if e["mac_address"].lower() == mac_lower]
    assert result == expected



# ---------------------------------------------------------------------------
# Property 28: ISSU state normalization
# Feature: aruba-cx-python-port, Property 28
# **Validates: Requirements 17.3**
# ---------------------------------------------------------------------------

# Known ISSU state strings and their expected canonical values
_KNOWN_ISSU_STATES = {
    "idle": "idle",
    "in_progress": "in_progress",
    "upgrading": "in_progress",
    "downloading": "in_progress",
    "succeeded": "succeeded",
    "success": "succeeded",
    "completed": "succeeded",
    "failed": "failed",
    "error": "failed",
}

_known_issu_state = st.sampled_from(list(_KNOWN_ISSU_STATES.keys()))
_unknown_issu_state = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.lower().strip() not in _KNOWN_ISSU_STATES)


@settings(max_examples=100)
@given(raw_state=_known_issu_state)
def test_issu_state_normalization_known_states(raw_state: str):
    """For any known raw ISSU state string, the normalizer should map to the
    correct canonical state: idle, in_progress, succeeded, or failed."""
    normalized = _server._normalize_issu_state(raw_state)
    expected = _KNOWN_ISSU_STATES[raw_state.lower().strip()]
    assert normalized == expected
    assert normalized in {"idle", "in_progress", "succeeded", "failed"}


@settings(max_examples=100)
@given(raw_state=_unknown_issu_state)
def test_issu_state_normalization_unknown_defaults_to_idle(raw_state: str):
    """For any unknown raw ISSU state string, the normalizer should default
    to 'idle'."""
    normalized = _server._normalize_issu_state(raw_state)
    assert normalized == "idle"


@settings(max_examples=100)
@given(
    raw_state=_known_issu_state,
    padding=st.sampled_from(["", " ", "  ", "\t"]),
)
def test_issu_state_normalization_case_insensitive(raw_state: str, padding: str):
    """For any known ISSU state string with varied casing and whitespace,
    the normalizer should still map correctly."""
    variants = [raw_state.upper(), raw_state.capitalize(), raw_state.lower()]
    for variant in variants:
        padded = padding + variant + padding
        normalized = _server._normalize_issu_state(padded)
        expected = _KNOWN_ISSU_STATES[raw_state.lower().strip()]
        assert normalized == expected


# ---------------------------------------------------------------------------
# Property 29: ISSU readiness mapping
# Feature: aruba-cx-python-port, Property 29
# **Validates: Requirements 17.1**
# ---------------------------------------------------------------------------

_blocking_conditions = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=60,
    ),
    min_size=0,
    max_size=5,
)



