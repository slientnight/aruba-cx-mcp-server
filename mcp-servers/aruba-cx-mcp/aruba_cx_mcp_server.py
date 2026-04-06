"""Aruba CX MCP Server — FastMCP server exposing Aruba CX switch management tools.

Provides 16 MCP tools (11 read + 5 write) over stdio transport for managing
Aruba CX switches via the AOS-CX REST API.
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP

from aruba_client import ArubaCxClient, ArubaCxException
from itsm_gate import validate_change_request
from models import ArubaCxError, ErrorCode

# ---------------------------------------------------------------------------
# FastMCP server initialization
# ---------------------------------------------------------------------------
mcp = FastMCP("aruba-cx-mcp")

print("Aruba CX MCP server starting", file=sys.stderr)

# ---------------------------------------------------------------------------
# Module-level client instance
# ---------------------------------------------------------------------------
client = ArubaCxClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_dumps(data: Any) -> str:
    """Serialize data to JSON with indent=2."""
    return json.dumps(data, indent=2, default=str)


def _audit_log(operation: str, target: str, status: str, **kwargs) -> None:
    """Emit structured JSON audit log to stderr. Silently degrades on failure."""
    try:
        entry = {
            "operation": operation,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "target": target,
            "status": status,
        }
        # Add write-op fields if provided
        for key in ("change_request_number", "baseline", "verify"):
            if key in kwargs:
                entry[key] = kwargs[key]
        # Redact credentials
        log_str = ArubaCxClient._redact(json.dumps(entry))
        print(log_str, file=sys.stderr)
    except Exception:
        pass  # Silently degrade — never fail tool invocation


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_system(target: str) -> str:
    """Get system information and health status from an Aruba CX switch. Returns hostname, firmware, platform, serial, uptime, CPU, memory, temperature, and fan status."""
    try:
        # Get config data (hostname, mgmt settings)
        cfg_data = client.get(target, "/system")
        # Get status/runtime data (firmware, MAC, boot_time, uptime)
        try:
            status_data = client.get(target, "/system?selector=status&depth=2")
        except Exception:
            status_data = {}

        # Uptime: prefer ntp_status.uptime (seconds string), fall back to boot_time
        uptime = 0
        ntp_status = status_data.get("ntp_status", {})
        if isinstance(ntp_status, dict) and ntp_status.get("uptime"):
            try:
                uptime = int(ntp_status["uptime"])
            except (ValueError, TypeError):
                pass
        if not uptime:
            boot_time = status_data.get("boot_time", 0)
            if boot_time:
                import time
                uptime = max(0, int(time.time()) - int(boot_time))

        result = {
            "hostname": cfg_data.get("hostname", ""),
            "firmware_version": status_data.get("software_version", cfg_data.get("software_version", "")),
            "platform_name": status_data.get("platform_name", cfg_data.get("platform_name", "")),
            "serial_number": "",
            "uptime_seconds": uptime,
            "base_mac_address": status_data.get("system_mac", ""),
            "product_name": "",
        }

        # --- VSF member discovery and per-chassis serial/product info ---
        # 1. Get VSF member list with roles and memory utilization
        vsf_members = {}  # member_id -> {role, memory_utilization}
        try:
            vsf_data = client.get(target, "/system/vsf_members?depth=2&selector=status")
            if isinstance(vsf_data, dict):
                for mid, minfo in vsf_data.items():
                    if isinstance(minfo, dict):
                        vsf_members[str(mid)] = {
                            "role": minfo.get("role", ""),
                            "status": minfo.get("status", ""),
                            "memory_utilization": minfo.get("memory_utilization", {}),
                        }
        except Exception:
            pass

        # 2. Determine chassis member IDs
        # Prefer VSF member IDs (only real members, not empty slots)
        if vsf_members:
            chassis_ids = set(vsf_members.keys())
        else:
            chassis_ids = None  # will use all chassis from subsystems

        # 3. Bulk-query all subsystems for product_info in a single API call
        members = []
        conductor_serial = ""
        try:
            subsys_bulk = client.get(
                target, "/system/subsystems?depth=2&selector=status&attributes=product_info,name,state"
            )
            if isinstance(subsys_bulk, dict):
                for key, sub in subsys_bulk.items():
                    if not key.startswith("chassis,") or not isinstance(sub, dict):
                        continue
                    cid = key.split(",")[1]
                    # Skip if we have VSF member list and this chassis isn't in it
                    if chassis_ids is not None and cid not in chassis_ids:
                        continue
                    product_info = sub.get("product_info", {})
                    if not isinstance(product_info, dict):
                        continue
                    serial = product_info.get("serial_number", "")
                    product_name = product_info.get("product_name", "")
                    part_number = product_info.get("part_number", "")
                    # Skip empty placeholder slots
                    if not serial and not part_number:
                        continue
                    vsf_info = vsf_members.get(cid, {})
                    role = vsf_info.get("role", "")
                    member = {
                        "member_id": cid,
                        "serial_number": serial,
                        "part_number": part_number,
                        "product_name": product_name,
                    }
                    if role:
                        member["role"] = role
                    if role == "conductor" and serial:
                        conductor_serial = serial
                    members.append(member)
        except Exception:
            pass
        # Sort members by ID for consistent output
        members.sort(key=lambda m: int(m["member_id"]) if m["member_id"].isdigit() else 0)

        # Set top-level serial: conductor serial for VSF, or single member serial
        if conductor_serial:
            result["serial_number"] = conductor_serial
        elif len(members) == 1:
            result["serial_number"] = members[0].get("serial_number", "")
        # Set product_name from first member if available
        if members and not result["product_name"]:
            result["product_name"] = members[0].get("product_name", "")
        if len(members) > 1:
            result["members"] = members

        # --- Health metrics from VSF member data ---
        if vsf_members:
            # Aggregate memory from conductor (or first available)
            for mid, minfo in vsf_members.items():
                mem = minfo.get("memory_utilization", {})
                if isinstance(mem, dict) and mem.get("total_memory"):
                    result["memory_utilization"] = {
                        "current_usage_kb": mem.get("current_usage", 0),
                        "total_memory_kb": mem.get("total_memory", 0),
                    }
                    break

        _audit_log("get_system", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        _audit_log("get_system", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_system", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# Interface tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_interfaces(target: str, interface: str = "") -> str:
    """Get interfaces from an Aruba CX switch. Without interface param returns all interfaces summary. With interface param returns detailed info including statistics for that interface."""
    try:
        if interface:
            encoded = interface.replace("/", "%2F")
            data = client.get(target, f"/system/interfaces/{encoded}?depth=2")
            result = {
                "name": data.get("name", interface),
                "admin_state": data.get("admin_state", data.get("admin", "unknown")),
                "link_state": data.get("link_state", "unknown"),
                "speed": str(data.get("speed", data.get("link_speed", "unknown"))),
                "description": data.get("description"),
                "duplex": data.get("duplex"),
                "vlan_id": data.get("vlan_tag"),
                "statistics": data.get("statistics"),
            }
        else:
            data = client.get(target, "/system/interfaces?depth=2")
            result = []
            for name, iface_data in data.items():
                if isinstance(iface_data, dict):
                    result.append({
                        "name": name,
                        "admin_state": iface_data.get("admin_state", iface_data.get("admin", "unknown")),
                        "link_state": iface_data.get("link_state", "unknown"),
                        "speed": str(iface_data.get("speed", iface_data.get("link_speed", "unknown"))),
                        "description": iface_data.get("description"),
                        "duplex": iface_data.get("duplex"),
                        "mtu": iface_data.get("mtu"),
                    })
        _audit_log("get_interfaces", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        _audit_log("get_interfaces", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_interfaces", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def configure_interface(target: str, interface: str, admin_state: str = "", description: str = "", speed: str = "", duplex: str = "", vlan: int = 0, change_request_number: str = "") -> str:
    """Configure an interface on an Aruba CX switch. Write operation — requires change_request_number when ITSM is enabled."""
    try:
        validate_change_request(change_request_number)
        encoded = interface.replace("/", "%2F")
        # GET baseline
        baseline = client.get(target, f"/system/interfaces/{encoded}")
        # Build config payload
        config = {}
        if admin_state: config["admin_state"] = admin_state
        if description: config["description"] = description
        if speed: config["speed"] = speed
        if duplex: config["duplex"] = duplex
        if vlan: config["vlan_tag"] = vlan
        # PUT config
        client.put(target, f"/system/interfaces/{encoded}", config)
        # GET verify
        verify = client.get(target, f"/system/interfaces/{encoded}")
        _audit_log("configure_interface", target, "success", change_request_number=change_request_number, baseline=baseline, verify=verify)
        return _json_dumps({"status": "success", "interface": interface, "baseline": baseline, "verify": verify})
    except ValueError as exc:
        _audit_log("configure_interface", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("configure_interface", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("configure_interface", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# VLAN tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_vlans(target: str) -> str:
    """List all VLANs on an Aruba CX switch with ID, name, and status."""
    try:
        data = client.get(target, "/system/vlans?depth=2")
        vlans = []
        for vlan_key, vlan_data in data.items():
            if isinstance(vlan_data, dict):
                vlans.append({
                    "id": vlan_data.get("id", int(vlan_key) if vlan_key.isdigit() else 0),
                    "name": vlan_data.get("name", ""),
                    "status": vlan_data.get("oper_state", vlan_data.get("status", "unknown")),
                })
        _audit_log("get_vlans", target, "success")
        return _json_dumps(vlans)
    except ArubaCxException as exc:
        _audit_log("get_vlans", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_vlans", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def manage_vlan(target: str, action: str, vlan_id: int, name: str = "", change_request_number: str = "") -> str:
    """Create or delete a VLAN. action: 'create' (requires name) or 'delete'. Write operation."""
    try:
        validate_change_request(change_request_number)
        if action == "create":
            if not name:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="name is required for create action", target=target).model_dump())
            client.post(target, "/system/vlans", {"id": vlan_id, "name": name})
            _audit_log("manage_vlan", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "create", "vlan_id": vlan_id, "name": name})
        elif action == "delete":
            client.delete(target, f"/system/vlans/{vlan_id}")
            _audit_log("manage_vlan", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "delete", "vlan_id": vlan_id})
        else:
            return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=f"Unknown action '{action}'. Use 'create' or 'delete'", target=target).model_dump())
    except ValueError as exc:
        _audit_log("manage_vlan", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("manage_vlan", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("manage_vlan", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# Configuration management tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_config(target: str, config_type: str = "running") -> str:
    """Get switch configuration. config_type: 'running' (default) or 'startup'."""
    try:
        path = "/fullconfigs/startup-config" if config_type == "startup" else "/fullconfigs/running-config"
        data = client.get(target, path)
        _audit_log("get_config", target, "success")
        return _json_dumps(data)
    except ArubaCxException as exc:
        _audit_log("get_config", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_config", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def save_config(target: str, action: str = "write_memory", checkpoint_name: str = "", change_request_number: str = "") -> str:
    """Save or checkpoint configuration. action: 'write_memory' (default) saves running to startup, 'checkpoint' creates a named checkpoint (requires checkpoint_name). Write operation."""
    try:
        validate_change_request(change_request_number)
        if action == "checkpoint":
            if not checkpoint_name:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="checkpoint_name is required for checkpoint action", target=target).model_dump())
            client.post(target, "/fullconfigs/checkpoints", {"name": checkpoint_name})
            _audit_log("save_config", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "checkpoint", "checkpoint_name": checkpoint_name})
        else:
            client.put(target, "/fullconfigs/startup-config", {"from": "/fullconfigs/running-config"})
            _audit_log("save_config", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "write_memory", "message": "Running config saved to startup"})
    except ValueError as exc:
        _audit_log("save_config", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("save_config", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("save_config", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# Routing and ARP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_routing(target: str, table: str = "routes", vrf: str = "default") -> str:
    """Get routing or ARP table. table: 'routes' (default) for routing table, 'arp' for ARP table. Optional vrf for routes (default 'default')."""
    try:
        if table == "arp":
            data = client.get(target, "/system/vrfs/default/neighbors?depth=2")
            entries = []
            for key, entry_data in data.items():
                if isinstance(entry_data, dict):
                    entries.append({
                        "ip_address": entry_data.get("ip_address", entry_data.get("ip", key)),
                        "mac_address": entry_data.get("mac", entry_data.get("mac_address", "")),
                        "interface": entry_data.get("port", entry_data.get("interface", "")),
                        "state": entry_data.get("state"),
                    })
            _audit_log("get_routing", target, "success")
            return _json_dumps(entries)
        else:
            data = client.get(target, f"/system/vrfs/{vrf}/routes?depth=2")
            routes = []
            for route_key, route_data in data.items():
                if isinstance(route_data, dict):
                    routes.append({
                        "destination": route_data.get("prefix", route_data.get("destination", route_key)),
                        "next_hop": route_data.get("nexthop", route_data.get("next_hop", "")),
                        "protocol": route_data.get("route_type", route_data.get("protocol", "")),
                        "metric": route_data.get("distance", route_data.get("metric", 0)),
                    })
            _audit_log("get_routing", target, "success")
            return _json_dumps(routes)
    except ArubaCxException as exc:
        _audit_log("get_routing", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_routing", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# LLDP tool
# ---------------------------------------------------------------------------


@mcp.tool()
def get_lldp_neighbors(target: str, interface: str = "") -> str:
    """Get LLDP neighbors from an Aruba CX switch, optionally filtered by interface."""
    try:
        data = client.get(target, "/system/interfaces?depth=3&attributes=lldp_neighbors")
        neighbors = []
        for iface_name, iface_data in data.items():
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
        # Filter by interface if specified
        if interface:
            neighbors = [n for n in neighbors if n["local_interface"] == interface]
        _audit_log("get_lldp_neighbors", target, "success")
        return _json_dumps(neighbors)
    except ArubaCxException as exc:
        _audit_log("get_lldp_neighbors", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_lldp_neighbors", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# MAC address table tool
# ---------------------------------------------------------------------------


@mcp.tool()
def get_mac_address_table(target: str, vlan_id: int = 0, mac_address: str = "") -> str:
    """Get the MAC address table from an Aruba CX switch with optional VLAN and MAC filters."""
    try:
        # First get the list of VLANs to query
        if vlan_id:
            vlan_ids = [vlan_id]
        else:
            vlans_data = client.get(target, "/system/vlans?depth=1")
            vlan_ids = []
            for vlan_key in vlans_data:
                if str(vlan_key).isdigit():
                    vlan_ids.append(int(vlan_key))

        entries = []
        for vid in vlan_ids:
            try:
                # Query MAC entries per VLAN using wildcard pattern
                macs = client.get(target, f"/system/vlans/{vid}/macs?depth=2")
                if not isinstance(macs, dict):
                    continue
                for mac_key, mac_data in macs.items():
                    if not isinstance(mac_data, dict):
                        continue
                    # Extract port — on VSF stacks, port can be None with
                    # the actual port in desired_port instead
                    port_val = mac_data.get("port")
                    if port_val is None or port_val == "":
                        port_val = mac_data.get("desired_port", "")
                    # Port can be a dict like {"1/1/1": "/rest/v10.13/..."}
                    if isinstance(port_val, dict):
                        # Take the first key (interface name)
                        port_val = next(iter(port_val), "")
                    elif isinstance(port_val, str) and "/rest/" in port_val:
                        # URI reference — extract interface name
                        port_val = port_val.rsplit("/", 1)[-1].replace("%2F", "/")
                    # MAC address from the entry or parse from the key
                    # Key format is "from,mac_addr" e.g. "dynamic,aa:bb:cc:dd:ee:ff"
                    mac_addr = mac_data.get("mac_addr", "")
                    if not mac_addr and "," in mac_key:
                        mac_addr = mac_key.split(",", 1)[1]
                    elif not mac_addr:
                        mac_addr = mac_key
                    # Entry type from 'from' field or parsed from key
                    entry_type = mac_data.get("from", "")
                    if not entry_type and "," in mac_key:
                        entry_type = mac_key.split(",", 1)[0]
                    if not entry_type:
                        entry_type = "dynamic"
                    entries.append({
                        "mac_address": mac_addr,
                        "vlan_id": vid,
                        "port": str(port_val) if port_val else "",
                        "type": entry_type,
                        "age": mac_data.get("age"),
                    })
            except Exception:
                # Skip VLANs that don't have MAC entries or return errors
                continue
        # Apply MAC address filter
        if mac_address:
            mac_lower = mac_address.lower()
            entries = [e for e in entries if e["mac_address"].lower() == mac_lower]
        _audit_log("get_mac_address_table", target, "success")
        return _json_dumps(entries)
    except ArubaCxException as exc:
        _audit_log("get_mac_address_table", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_mac_address_table", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# Optics / DOM tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_optics(target: str, interface: str = "", detail: str = "info") -> str:
    """Get optics/transceiver data. detail: 'info' (default) for transceiver info, 'dom' for DOM diagnostics (requires interface), 'health' for threshold violation assessment. Optional interface filter."""
    try:
        if detail == "dom":
            if not interface:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="interface is required for dom detail", target=target).model_dump())
            encoded = interface.replace("/", "%2F")
            data = client.get(target, f"/system/interfaces/{encoded}?depth=2&attributes=pm_monitor")
            pm = data.get("pm_monitor", data)
            if not isinstance(pm, dict):
                pm = {}
            lanes = []
            lane_data = pm.get("lanes", pm.get("lane_readings", {}))
            if isinstance(lane_data, dict):
                for lane_key, lane_val in lane_data.items():
                    if isinstance(lane_val, dict):
                        lanes.append({
                            "lane": int(lane_key) if str(lane_key).isdigit() else 0,
                            "rx_power_dbm": lane_val.get("rx_power_dbm", lane_val.get("rx_power")),
                            "tx_power_dbm": lane_val.get("tx_power_dbm", lane_val.get("tx_power")),
                            "bias_current_ma": lane_val.get("bias_current_ma", lane_val.get("bias_current")),
                        })
            elif isinstance(lane_data, list):
                for idx, lane_val in enumerate(lane_data):
                    if isinstance(lane_val, dict):
                        lanes.append({
                            "lane": lane_val.get("lane", idx),
                            "rx_power_dbm": lane_val.get("rx_power_dbm", lane_val.get("rx_power")),
                            "tx_power_dbm": lane_val.get("tx_power_dbm", lane_val.get("tx_power")),
                            "bias_current_ma": lane_val.get("bias_current_ma", lane_val.get("bias_current")),
                        })
            result = {
                "interface": interface,
                "rx_power_dbm": pm.get("rx_power_dbm", pm.get("rx_power")),
                "tx_power_dbm": pm.get("tx_power_dbm", pm.get("tx_power")),
                "temperature_celsius": pm.get("temperature_celsius", pm.get("temperature")),
                "voltage": pm.get("voltage", pm.get("vcc")),
                "bias_current_ma": pm.get("bias_current_ma", pm.get("bias_current")),
                "lanes": lanes,
            }
            _audit_log("get_optics", target, "success")
            return _json_dumps(result)

        elif detail == "health":
            data = client.get(target, "/system/interfaces?depth=2&attributes=pm_monitor")
            results = []
            _DOM_PARAMS = [
                ("rx_power_dbm", "rx_power"), ("tx_power_dbm", "tx_power"),
                ("temperature_celsius", "temperature"), ("voltage", "vcc"),
                ("bias_current_ma", "bias_current"),
            ]
            for iface_name, iface_data in data.items():
                if not isinstance(iface_data, dict):
                    continue
                if interface and iface_name != interface:
                    continue
                pm = iface_data.get("pm_monitor", {})
                if not isinstance(pm, dict) or not pm:
                    continue
                violations = []
                for param_name, alt_name in _DOM_PARAMS:
                    current = pm.get(param_name, pm.get(alt_name))
                    if current is None:
                        continue
                    try:
                        current = float(current)
                    except (ValueError, TypeError):
                        continue
                    for suffix, severity in [("_high_alarm", "alarm"), ("_high_warning", "warning")]:
                        thresh = pm.get(f"{param_name}{suffix}", pm.get(f"{alt_name}{suffix}"))
                        if thresh is not None:
                            try:
                                if current > float(thresh):
                                    violations.append({"parameter": param_name, "current_value": current, "threshold": float(thresh), "severity": severity, "direction": "high"})
                            except (ValueError, TypeError):
                                pass
                    for suffix, severity in [("_low_alarm", "alarm"), ("_low_warning", "warning")]:
                        thresh = pm.get(f"{param_name}{suffix}", pm.get(f"{alt_name}{suffix}"))
                        if thresh is not None:
                            try:
                                if current < float(thresh):
                                    violations.append({"parameter": param_name, "current_value": current, "threshold": float(thresh), "severity": severity, "direction": "low"})
                            except (ValueError, TypeError):
                                pass
                results.append({"interface": iface_name, "status": "unhealthy" if violations else "healthy", "violations": violations})
            _audit_log("get_optics", target, "success")
            return _json_dumps(results)

        else:  # info
            data = client.get(target, "/system/interfaces?depth=2&attributes=pm_info")
            transceivers = []
            for iface_name, iface_data in data.items():
                if not isinstance(iface_data, dict):
                    continue
                pm = iface_data.get("pm_info", {})
                if not isinstance(pm, dict) or not pm:
                    continue
                transceivers.append({
                    "interface": iface_name,
                    "transceiver_type": pm.get("connector_type", pm.get("transceiver_type", "")),
                    "vendor_name": pm.get("vendor_name", ""),
                    "serial_number": pm.get("vendor_serial_number", pm.get("serial_number", "")),
                    "wavelength": pm.get("wavelength"),
                    "supports_dom": bool(pm.get("diagnostic_monitoring_type", pm.get("supports_dom", False))),
                })
            if interface:
                transceivers = [t for t in transceivers if t["interface"] == interface]
            _audit_log("get_optics", target, "success")
            return _json_dumps(transceivers)
    except ArubaCxException as exc:
        _audit_log("get_optics", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_optics", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# ISSU tools
# ---------------------------------------------------------------------------

# Canonical ISSU state normalization map
_ISSU_STATE_MAP = {
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


def _normalize_issu_state(raw_state: str) -> str:
    """Normalize a raw ISSU state string to a canonical value."""
    return _ISSU_STATE_MAP.get(raw_state.lower().strip(), "idle") if raw_state else "idle"


@mcp.tool()
def get_issu_info(target: str) -> str:
    """Get ISSU readiness, status, and progress from an Aruba CX switch. Returns readiness, blocking conditions, current state, percent complete, and image versions."""
    try:
        data = client.get(target, "/system/issu/status")
        raw_state = data.get("state", data.get("status", "idle"))
        normalized = _normalize_issu_state(str(raw_state))
        blocking = data.get("blocking_conditions", data.get("blockers", []))
        if isinstance(blocking, str):
            blocking = [blocking] if blocking else []
        result = {
            "ready": normalized == "idle",
            "state": normalized,
            "raw_state": str(raw_state),
            "percent_complete": data.get("percent_complete", data.get("progress", 0)),
            "current_phase": data.get("current_phase", data.get("phase", "")),
            "active_image": data.get("active_image", data.get("current_version", "")),
            "standby_image": data.get("standby_image", data.get("standby_version")),
            "blocking_conditions": blocking,
            "error_message": data.get("error_message", data.get("error")),
            "details": data.get("details", data.get("message", "")),
        }
        _audit_log("get_issu_info", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        _audit_log("get_issu_info", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_issu_info", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def manage_issu(target: str, action: str, firmware_image: str = "", timeout_seconds: int = 0, change_request_number: str = "") -> str:
    """Manage ISSU operations. action: 'initiate' (requires firmware_image), 'set_rollback_timer' (requires timeout_seconds), or 'confirm'. Write operation."""
    try:
        validate_change_request(change_request_number)
        if action == "initiate":
            if not firmware_image:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="firmware_image is required for initiate action", target=target).model_dump())
            client.post(target, "/system/issu/start", {"firmware_image": firmware_image})
            _audit_log("manage_issu", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "initiate", "message": f"ISSU initiated with image {firmware_image}"})
        elif action == "set_rollback_timer":
            if not timeout_seconds:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="timeout_seconds is required for set_rollback_timer action", target=target).model_dump())
            client.put(target, "/system/issu/rollback_timer", {"timeout_seconds": timeout_seconds})
            _audit_log("manage_issu", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "set_rollback_timer", "timeout_seconds": timeout_seconds})
        elif action == "confirm":
            client.post(target, "/system/issu/confirm", {})
            _audit_log("manage_issu", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "confirm", "message": "ISSU confirmed, rollback timer cancelled"})
        else:
            return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=f"Unknown action '{action}'. Use 'initiate', 'set_rollback_timer', or 'confirm'", target=target).model_dump())
    except ValueError as exc:
        _audit_log("manage_issu", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("manage_issu", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("manage_issu", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# Firmware tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_firmware(target: str) -> str:
    """Get firmware versions, boot bank info, and any active upload/download progress from an Aruba CX switch."""
    try:
        info = {}
        # Get firmware versions
        try:
            data = client.get(target, "/system/firmware")
            info.update({
                "current_version": data.get("current_version", data.get("active_version", "")),
                "primary_version": data.get("primary_version", data.get("primary_image", "")),
                "secondary_version": data.get("secondary_version", data.get("secondary_image", "")),
                "active_boot_bank": data.get("active_boot_bank", data.get("boot_bank", "")),
                "booted_image": data.get("booted_image", data.get("booted_from", "")),
            })
        except Exception:
            pass
        # Get transfer status
        try:
            status_data = client.get(target, "/system/firmware/status")
            info["transfer_status"] = {
                "status": status_data.get("status", status_data.get("state", "idle")),
                "progress": status_data.get("progress", status_data.get("percent_complete", 0)),
                "message": status_data.get("message", status_data.get("details", "")),
            }
        except Exception:
            info["transfer_status"] = {"status": "idle", "progress": 0, "message": ""}
        _audit_log("get_firmware", target, "success")
        return _json_dumps(info)
    except ArubaCxException as exc:
        _audit_log("get_firmware", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_firmware", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def manage_firmware(target: str, action: str, file_path: str = "", url: str = "", change_request_number: str = "") -> str:
    """Manage firmware transfers. action: 'upload' (requires file_path) or 'download' (requires url). Write operation."""
    try:
        validate_change_request(change_request_number)
        if action == "upload":
            if not file_path:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="file_path is required for upload action", target=target).model_dump())
            client.post(target, "/system/firmware/upload", {"file_path": file_path})
            _audit_log("manage_firmware", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "upload", "message": f"Firmware upload initiated from {file_path}"})
        elif action == "download":
            if not url:
                return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message="url is required for download action", target=target).model_dump())
            client.post(target, "/system/firmware/download", {"url": url})
            _audit_log("manage_firmware", target, "success", change_request_number=change_request_number)
            return _json_dumps({"status": "success", "action": "download", "message": f"Firmware download initiated from {url}"})
        else:
            return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=f"Unknown action '{action}'. Use 'upload' or 'download'", target=target).model_dump())
    except ValueError as exc:
        _audit_log("manage_firmware", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("manage_firmware", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("manage_firmware", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


# ---------------------------------------------------------------------------
# VSF tool
# ---------------------------------------------------------------------------


@mcp.tool()
def get_vsf_topology(target: str) -> str:
    """Get VSF (Virtual Switching Framework) topology from an Aruba CX switch including member details and link states."""
    try:
        data = client.get(target, "/system/vsf?depth=2")
        members = []
        member_data = data.get("members", data.get("vsf_members", data))
        if isinstance(member_data, dict):
            for member_key, member in member_data.items():
                if isinstance(member, dict):
                    members.append({
                        "member_id": member.get("id", int(member_key) if str(member_key).isdigit() else 0),
                        "role": member.get("role", ""),
                        "status": member.get("status", member.get("state", "")),
                        "model": member.get("model", member.get("platform", "")),
                        "serial_number": member.get("serial_number", ""),
                        "links": member.get("links", member.get("vsf_links", [])),
                    })
        elif isinstance(member_data, list):
            for member in member_data:
                if isinstance(member, dict):
                    members.append({
                        "member_id": member.get("id", 0),
                        "role": member.get("role", ""),
                        "status": member.get("status", member.get("state", "")),
                        "model": member.get("model", member.get("platform", "")),
                        "serial_number": member.get("serial_number", ""),
                        "links": member.get("links", member.get("vsf_links", [])),
                    })
        result = {
            "vsf_enabled": True,
            "members": members,
        }
        _audit_log("get_vsf_topology", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        # Check if the error indicates VSF not supported
        err = exc.error
        if err.http_status == 404 or "not supported" in (err.message or "").lower() or "not found" in (err.message or "").lower():
            _audit_log("get_vsf_topology", target, "success")
            return _json_dumps({"vsf_enabled": False, "message": "VSF is not supported or not enabled on this switch", "members": []})
        _audit_log("get_vsf_topology", target, "error")
        return _json_dumps(err.model_dump())
    except Exception as exc:
        error_msg = str(exc).lower()
        if "404" in error_msg or "not supported" in error_msg or "not found" in error_msg:
            _audit_log("get_vsf_topology", target, "success")
            return _json_dumps({"vsf_enabled": False, "message": "VSF is not supported or not enabled on this switch", "members": []})
        _audit_log("get_vsf_topology", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


if __name__ == "__main__":
    mcp.run(transport="stdio")
