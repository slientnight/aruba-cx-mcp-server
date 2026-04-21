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
def get_interfaces(target: str, interface: str = "", detail: str = "config") -> str:
    """Get interfaces from an Aruba CX switch. Without interface param returns all interfaces summary. With interface param returns detailed info. detail: 'config' (default) for config only, 'stats' to include statistics, 'full' for both."""
    def _extract_vlan(idata: dict):
        """Extract VLAN ID from vlan_tag or applied_vlan_tag."""
        vt = idata.get("vlan_tag")
        if vt is not None:
            if isinstance(vt, dict):
                # {"100": "/rest/..."} — take first key
                keys = [k for k in vt if str(k).isdigit()]
                return int(keys[0]) if keys else None
            if isinstance(vt, (int, float)):
                return int(vt)
            if isinstance(vt, str):
                if vt.isdigit():
                    return int(vt)
                # Handle URI reference like "/rest/v10.13/system/vlans/989"
                parts = vt.rstrip("/").split("/")
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
        # Fallback: applied_vlan_tag
        avt = idata.get("applied_vlan_tag", {})
        if isinstance(avt, dict):
            keys = [k for k in avt if str(k).isdigit()]
            if keys:
                return int(keys[0])
        elif isinstance(avt, str):
            parts = avt.rstrip("/").split("/")
            if parts and parts[-1].isdigit():
                return int(parts[-1])
        return None

    try:
        if interface:
            encoded = interface.replace("/", "%2F")
            data = client.get(target, f"/system/interfaces/{encoded}?depth=2")
            vlan_id = _extract_vlan(data)
            # Check for trunk VLANs
            trunks = data.get("vlan_trunks", {})
            trunk_vlans = sorted(int(k) for k in trunks if str(k).isdigit()) if isinstance(trunks, dict) and trunks else []
            result = {
                "name": data.get("name", interface),
                "admin_state": data.get("admin_state", data.get("admin", "unknown")),
                "link_state": data.get("link_state", "unknown"),
                "speed": str(data.get("speed", data.get("link_speed", "unknown"))),
                "description": data.get("description"),
                "duplex": data.get("duplex"),
                "vlan_id": vlan_id,
                "vlan_mode": data.get("vlan_mode"),
            }
            if trunk_vlans:
                result["trunk_vlans"] = trunk_vlans
            if detail in ("stats", "full"):
                result["statistics"] = data.get("statistics")
        else:
            data = client.get(target, "/system/interfaces?depth=2")
            # Fetch Port data to get VLAN assignments (CX stores VLANs on Port, not Interface)
            try:
                cfg_data = client.get(target, "/fullconfigs/running-config")
                port_data = cfg_data.get("Port", {})
            except Exception:
                port_data = {}
            # Build a lookup from port name to VLAN info
            port_vlan_map = {}
            for pname, pdata in port_data.items():
                if isinstance(pdata, dict):
                    port_name = pdata.get("name", pname.replace("%2F", "/"))
                    pvlan = _extract_vlan(pdata)
                    pvlan_mode = pdata.get("vlan_mode")
                    ptrunks = pdata.get("vlan_trunks", {})
                    ptrunk_vlans = sorted(int(k) for k in ptrunks if str(k).isdigit()) if isinstance(ptrunks, dict) and ptrunks else []
                    port_vlan_map[port_name] = {
                        "vlan_id": pvlan,
                        "vlan_mode": pvlan_mode,
                        "trunk_vlans": ptrunk_vlans,
                    }
                    # Also store with URL-decoded key for matching
                    decoded_key = pname.replace("%2F", "/")
                    if decoded_key != port_name:
                        port_vlan_map[decoded_key] = port_vlan_map[port_name]
            result = []
            for name, iface_data in data.items():
                if isinstance(iface_data, dict):
                    iface_name = iface_data.get("name", name)
                    # Try Interface data first, then fall back to Port data
                    vlan_id = _extract_vlan(iface_data)
                    vlan_mode = iface_data.get("vlan_mode")
                    trunks = iface_data.get("vlan_trunks", {})
                    trunk_vlans = sorted(int(k) for k in trunks if str(k).isdigit()) if isinstance(trunks, dict) and trunks else []
                    # Merge from Port data if Interface didn't have it
                    pinfo = port_vlan_map.get(iface_name) or port_vlan_map.get(name.replace("%2F", "/"), {})
                    if vlan_id is None and pinfo.get("vlan_id") is not None:
                        vlan_id = pinfo["vlan_id"]
                    if vlan_mode is None and pinfo.get("vlan_mode") is not None:
                        vlan_mode = pinfo["vlan_mode"]
                    if not trunk_vlans and pinfo.get("trunk_vlans"):
                        trunk_vlans = pinfo["trunk_vlans"]
                    entry = {
                        "name": iface_name,
                        "admin_state": iface_data.get("admin_state", iface_data.get("admin", "unknown")),
                        "link_state": iface_data.get("link_state", "unknown"),
                        "speed": str(iface_data.get("speed", iface_data.get("link_speed", "unknown"))),
                        "description": iface_data.get("description"),
                        "duplex": iface_data.get("duplex"),
                        "mtu": iface_data.get("mtu"),
                    }
                    if vlan_id is not None:
                        entry["vlan_id"] = vlan_id
                    if vlan_mode is not None:
                        entry["vlan_mode"] = vlan_mode
                    if trunk_vlans:
                        entry["trunk_vlans"] = trunk_vlans
                    result.append(entry)
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
        # Build config payload — only include fields being changed
        config = {}
        if admin_state: config["admin_state"] = admin_state
        if description: config["description"] = description
        if speed: config["speed"] = speed
        if duplex: config["duplex"] = duplex
        if vlan:
            # AOS-CX REST API expects vlan_tag as a URI reference to the VLAN
            # Also set vlan_mode to access for static VLAN assignment
            target_obj = client._targets.get(target)
            api_ver = target_obj.api_version if target_obj else "v10.13"
            config["vlan_tag"] = f"/rest/{api_ver}/system/vlans/{vlan}"
            config["vlan_mode"] = "access"
        # PATCH config (merge with existing, don't replace)
        client.patch(target, f"/system/interfaces/{encoded}", config)
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


@mcp.tool()
def configure_port_access(
    target: str,
    port: str,
    mode: str = "",
    port_access_config: str = "",
    change_request_number: str = "",
) -> str:
    """Configure port-access AAA on an Aruba CX switch port. Write operation.

    This is a general-purpose tool for any port-level AAA configuration on AOS-CX.
    It patches the /system/interfaces/{port} REST endpoint.

    Parameters:
        target: Switch target name.
        port: Port name (e.g. '1/1/9').
        mode: Optional preset. Currently supported:
            - 'mac-radius': Removes static VLAN, sets auth-precedence mac-auth→dot1x,
              enables mac-auth with reauth, sets client-limit 256.
        port_access_config: JSON string of arbitrary Port attributes to PATCH.
            Accepts any field the AOS-CX REST API supports on /system/interfaces/{port}.
            Common fields:
            - aaa_auth_precedence: {"1":"mac-auth","2":"dot1x"}
            - port_access_auth_configurations: {"mac-auth":{"auth_enable":true,"reauth_enable":true},
              "dot1x":{"auth_enable":true,"reauth_period":3600,"quiet_period":60,"max_retries":3,"tx_period":30}}
            - port_access_clients_limit: 256
            - port_access_role: "/rest/v10.13/system/roles/my-role"
            - vlan_tag: null  (removes static VLAN)
            - vlan_mode: null
            - port_access_auth_configurations.dot1x.eap_method: "eap-peap"
            - port_access_local_override: {"critical_vlan":100,"auth_fail_vlan":999,"guest_vlan":50}
            When mode is set, port_access_config fields are merged on top of the preset
            (your overrides win).
        change_request_number: ITSM change request number (if required).
    """
    try:
        validate_change_request(change_request_number)
        encoded = port.replace("/", "%2F")

        # AOS-CX REST API manages port-access AAA config on the Interface
        # resource at /system/interfaces/{name}, not /system/ports/.
        # GET baseline for audit
        baseline = client.get(target, f"/system/interfaces/{encoded}")

        # --- Build the patch payload ---
        patch = {}

        # Apply preset if requested
        if mode == "mac-radius":
            # Remove static VLAN
            if "vlan_tag" in baseline:
                patch["vlan_tag"] = None
            if "vlan_mode" in baseline:
                patch["vlan_mode"] = None
            # Auth precedence: mac-auth first, dot1x second
            patch["aaa_auth_precedence"] = {"1": "mac-auth", "2": "dot1x"}
            # MAC-auth with reauth
            patch["port_access_auth_configurations"] = {
                "mac-auth": {"auth_enable": True, "reauth_enable": True},
            }
            # Client limit
            patch["port_access_clients_limit"] = 256

        # Merge user-supplied config on top (overrides preset values)
        if port_access_config:
            try:
                user_config = json.loads(port_access_config)
            except json.JSONDecodeError as e:
                return _json_dumps(ArubaCxError(
                    code=ErrorCode.API_ERROR,
                    message=f"Invalid JSON in port_access_config: {e}",
                    target=target,
                ).model_dump())
            if not isinstance(user_config, dict):
                return _json_dumps(ArubaCxError(
                    code=ErrorCode.API_ERROR,
                    message="port_access_config must be a JSON object",
                    target=target,
                ).model_dump())
            # Deep-merge: for nested dicts (like port_access_auth_configurations),
            # merge rather than replace so preset + user fields coexist
            for key, val in user_config.items():
                if key in patch and isinstance(patch[key], dict) and isinstance(val, dict):
                    patch[key].update(val)
                else:
                    patch[key] = val

        if not patch:
            return _json_dumps(ArubaCxError(
                code=ErrorCode.API_ERROR,
                message="Nothing to configure. Provide mode and/or port_access_config.",
                target=target,
            ).model_dump())

        # --- Apply the changes ---
        # Port-access auth configurations (mac-auth, dot1x) are sub-resources
        # that must be configured via their own endpoint, not inline on the
        # interface PATCH. Split them out.
        auth_configs = patch.pop("port_access_auth_configurations", None)

        # Step 1: PATCH interface-level attributes (auth precedence, client
        # limit, vlan removal, etc.)
        if patch:
            client.patch(target, f"/system/interfaces/{encoded}", patch)

        # Step 2: Configure auth methods via sub-resource endpoint
        if auth_configs:
            for method_name, method_config in auth_configs.items():
                method_config["authentication_method"] = method_name
                # Try PUT first (update), fall back to POST (create)
                try:
                    client.put(
                        target,
                        f"/system/interfaces/{encoded}/port_access_auth_configurations/{method_name}",
                        method_config,
                    )
                except ArubaCxException as put_exc:
                    if put_exc.error.http_status == 404:
                        client.post(
                            target,
                            f"/system/interfaces/{encoded}/port_access_auth_configurations",
                            method_config,
                        )
                    else:
                        raise

        # GET final state for verification
        verify = client.get(target, f"/system/interfaces/{encoded}")

        # Build a focused verify response with AAA-relevant fields
        aaa_fields = [
            "aaa_auth_precedence", "port_access_auth_configurations",
            "port_access_clients_limit", "port_access_role",
            "port_access_local_override", "vlan_tag", "vlan_mode",
        ]
        verify_summary = {k: verify.get(k) for k in aaa_fields if verify.get(k) is not None}

        _audit_log("configure_port_access", target, "success",
                   change_request_number=change_request_number,
                   baseline={k: baseline.get(k) for k in aaa_fields if baseline.get(k) is not None},
                   verify=verify_summary)
        return _json_dumps({
            "status": "success",
            "port": port,
            "applied": patch,
            "baseline": {k: baseline.get(k) for k in aaa_fields if baseline.get(k) is not None},
            "verify": verify_summary,
        })
    except ValueError as exc:
        _audit_log("configure_port_access", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.ITSM_ERROR, message=str(exc), target=target).model_dump())
    except ArubaCxException as exc:
        _audit_log("configure_port_access", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("configure_port_access", target, "error")
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
            # AOS-CX REST API: PUT /fullconfigs/startup-config with source
            # as query parameter ?from=<full-url>
            target_obj = client._targets.get(target)
            api_ver = target_obj.api_version if target_obj else "v10.13"
            from_param = f"/rest/{api_ver}/fullconfigs/running-config"
            client.put(target, f"/fullconfigs/startup-config?from={from_param}", None)
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
        neighbors = []
        if interface:
            # Query single interface directly
            encoded = interface.replace("/", "%2F")
            lldp_data = client.get(target, f"/system/interfaces/{encoded}/lldp_neighbors?depth=2")
            if isinstance(lldp_data, dict):
                for nk, nv in lldp_data.items():
                    if isinstance(nv, dict):
                        ni = nv.get("neighbor_info", {})
                        neighbors.append({
                            "local_interface": interface,
                            "remote_chassis_id": nv.get("chassis_id", ""),
                            "remote_port_id": nv.get("port_id", ""),
                            "remote_system_name": ni.get("chassis_name", ""),
                            "remote_system_description": ni.get("chassis_description", ""),
                            "remote_mgmt_ip": ni.get("mgmt_ip_list", ""),
                            "remote_port_description": ni.get("port_description", ""),
                        })
        else:
            # Find interfaces with LLDP neighbors via statistics
            stats = client.get(target, "/system/interfaces?depth=2&attributes=lldp_statistics")
            ifaces_with_neighbors = []
            for iname, idata in stats.items():
                if not isinstance(idata, dict):
                    continue
                ls = idata.get("lldp_statistics", {})
                if isinstance(ls, dict) and ls.get("lldp_insert", 0) > 0:
                    ifaces_with_neighbors.append(iname)
            # Query each interface with neighbors
            for iname in ifaces_with_neighbors:
                try:
                    encoded = iname.replace("/", "%2F")
                    lldp_data = client.get(target, f"/system/interfaces/{encoded}/lldp_neighbors?depth=2")
                    if isinstance(lldp_data, dict):
                        for nk, nv in lldp_data.items():
                            if isinstance(nv, dict):
                                ni = nv.get("neighbor_info", {})
                                neighbors.append({
                                    "local_interface": iname,
                                    "remote_chassis_id": nv.get("chassis_id", ""),
                                    "remote_port_id": nv.get("port_id", ""),
                                    "remote_system_name": ni.get("chassis_name", ""),
                                    "remote_system_description": ni.get("chassis_description", ""),
                                    "remote_mgmt_ip": ni.get("mgmt_ip_list", ""),
                                    "remote_port_description": ni.get("port_description", ""),
                                })
                except Exception:
                    continue
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
            data = client.get(target, f"/system/interfaces/{encoded}?depth=3&attributes=pm_monitor&selector=status")
            pm = data.get("pm_monitor", data)
            if not isinstance(pm, dict):
                pm = {}

            # Detect QSFP layout: per-lane data as numeric keys + "common"
            common = pm.get("common", {})
            if not isinstance(common, dict):
                common = {}
            lanes = []
            for key, val in pm.items():
                if not str(key).isdigit() or not isinstance(val, dict):
                    continue
                lane = {
                    "lane": int(key),
                    "rx_power_mw": val.get("rx_power"),
                    "tx_power_mw": val.get("tx_power"),
                    "tx_bias_ma": val.get("tx_bias"),
                    "rx_los": val.get("rx_los_state"),
                    "tx_fault": val.get("tx_fault_state"),
                }
                # Include alarm/warning flags
                for param in ("rx_power", "tx_power", "tx_bias"):
                    for level in ("high_alarm", "high_warning", "low_alarm", "low_warning"):
                        flag = val.get(f"{param}_{level}")
                        if flag is True:
                            lane[f"{param}_{level}"] = True
                lanes.append(lane)
            lanes.sort(key=lambda l: l["lane"])

            # If no per-lane data found, try flat SFP layout
            if not lanes:
                flat_lane = {}
                rx = pm.get("rx_power_dbm", pm.get("rx_power"))
                tx = pm.get("tx_power_dbm", pm.get("tx_power"))
                bias = pm.get("bias_current_ma", pm.get("tx_bias", pm.get("bias_current")))
                if rx is not None or tx is not None:
                    flat_lane = {"lane": 0, "rx_power_mw": rx, "tx_power_mw": tx, "tx_bias_ma": bias}
                    lanes.append(flat_lane)

            # Common/module-level data
            result = {
                "interface": interface,
                "temperature_celsius": common.get("temperature", pm.get("temperature_celsius", pm.get("temperature"))),
                "voltage": common.get("vcc", pm.get("voltage", pm.get("vcc"))),
                "lanes": lanes,
            }
            # Include thresholds if available
            thresholds = {}
            for param in ("rx_power", "tx_power", "tx_bias", "temperature", "vcc"):
                for level in ("high_alarm_threshold", "high_warning_threshold", "low_alarm_threshold", "low_warning_threshold"):
                    key = f"{param}_{level}"
                    val = common.get(key)
                    if val is not None:
                        thresholds[key] = val
            if thresholds:
                result["thresholds"] = thresholds
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
                entry = {
                    "interface": iface_name,
                    "transceiver_type": pm.get("xcvr_desc", pm.get("connector", "")),
                    "description": pm.get("long_xcvr_desc", ""),
                    "vendor_name": pm.get("vendor_name", ""),
                    "serial_number": pm.get("vendor_serial_number", pm.get("serial_number", "")),
                    "part_number": pm.get("proprietary_product_number", pm.get("vendor_part_number", "")),
                    "formfactor": pm.get("formfactor", ""),
                    "connector": pm.get("external_connector", ""),
                    "max_speed": pm.get("max_speed", ""),
                    "wavelength": pm.get("wavelength"),
                    "supports_dom": bool(pm.get("dom_supported", pm.get("diagnostic_monitoring_type", False))),
                }
                # Add cable-specific fields if present
                if pm.get("cable_length"):
                    entry["cable_length"] = pm.get("cable_length")
                    entry["cable_technology"] = pm.get("cable_technology", "")
                transceivers.append(entry)
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
        data = client.get(target, "/system/issu?depth=2")
        state = data.get("software_update_state", "unknown")
        confirmed = data.get("software_update_confirmed", False)
        prev_version = data.get("previous_software_version", "")
        rollback_timer = data.get("software_update_rollback_timer", 0)
        rollback_enabled = data.get("software_update_rollback_timer_enabled", False)

        # Parse upgrade history
        history = []
        hist_data = data.get("software_update_history", {})
        if isinstance(hist_data, dict):
            for hk, hv in sorted(hist_data.items()):
                if isinstance(hv, dict):
                    history.append({
                        "from_version": hv.get("from_version", ""),
                        "target_version": hv.get("target_version", ""),
                        "status": hv.get("status", ""),
                        "start_time": hv.get("start_time", ""),
                        "end_time": hv.get("end_time", ""),
                    })

        # Parse progress steps
        progress = []
        prog_data = data.get("software_update_progress", {})
        if isinstance(prog_data, dict):
            for pk, pv in sorted(prog_data.items()):
                if isinstance(pv, dict):
                    progress.append({
                        "step": pv.get("operation_name", ""),
                        "status": pv.get("operation_status", ""),
                    })

        # Validation status
        validation = data.get("software_update_validation_status", {})

        result = {
            "state": state,
            "confirmed": confirmed,
            "previous_version": prev_version,
            "rollback_timer": rollback_timer,
            "rollback_timer_enabled": rollback_enabled,
            "validation": validation if isinstance(validation, dict) else {},
            "history": history,
            "progress": progress,
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
        # Get software images and version from system status
        data = client.get(target, "/system?selector=status&attributes=software_images,software_version,software_info")
        sw_images = data.get("software_images", {})
        if not isinstance(sw_images, dict):
            sw_images = {}
        result = {
            "current_version": data.get("software_version", ""),
            "primary_version": sw_images.get("primary_image_version", ""),
            "secondary_version": sw_images.get("secondary_image_version", ""),
            "default_image": sw_images.get("default_image", ""),
            "primary_image_date": sw_images.get("primary_image_date", ""),
            "secondary_image_date": sw_images.get("secondary_image_date", ""),
            "primary_image_size": sw_images.get("primary_image_size", ""),
            "secondary_image_size": sw_images.get("secondary_image_size", ""),
        }
        # Get transfer/download status
        try:
            dl_data = client.get(target, "/system/downloads?depth=1")
            if isinstance(dl_data, dict) and dl_data:
                result["transfer_status"] = dl_data
            else:
                result["transfer_status"] = {"status": "idle"}
        except Exception:
            result["transfer_status"] = {"status": "idle"}
        _audit_log("get_firmware", target, "success")
        return _json_dumps(result)
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
        # Try vsf_members first (6300/6400 platforms)
        vsf_data = None
        try:
            vsf_data = client.get(target, "/system/vsf_members?depth=2&selector=status")
        except Exception:
            pass

        # Fallback: try /system/vsf (8xxx platforms)
        if not vsf_data or not isinstance(vsf_data, dict):
            try:
                data = client.get(target, "/system/vsf?depth=2")
                member_data = data.get("members", data.get("vsf_members", data))
                members = []
                if isinstance(member_data, dict):
                    for mk, mv in member_data.items():
                        if isinstance(mv, dict):
                            members.append({
                                "member_id": mv.get("id", int(mk) if str(mk).isdigit() else 0),
                                "role": mv.get("role", ""),
                                "status": mv.get("status", mv.get("state", "")),
                                "serial_number": mv.get("serial_number", ""),
                            })
                _audit_log("get_vsf_topology", target, "success")
                return _json_dumps({"vsf_enabled": True, "members": members})
            except Exception:
                _audit_log("get_vsf_topology", target, "success")
                return _json_dumps({"vsf_enabled": False, "message": "VSF is not supported or not enabled on this switch", "members": []})

        # Parse vsf_members response
        members = []
        for mid, minfo in vsf_data.items():
            if not isinstance(minfo, dict):
                continue
            members.append({
                "member_id": int(mid) if str(mid).isdigit() else mid,
                "role": minfo.get("role", ""),
                "status": minfo.get("status", ""),
            })
        members.sort(key=lambda m: m["member_id"] if isinstance(m["member_id"], int) else 0)

        # Get topology info from system status
        topology_type = ""
        split_state = ""
        try:
            sys_status = client.get(target, "/system?selector=status&attributes=vsf_status")
            vsf_status = sys_status.get("vsf_status", {})
            if isinstance(vsf_status, dict):
                topology_type = vsf_status.get("topology_type", "")
                split_state = vsf_status.get("stack_split_state", "")
        except Exception:
            pass

        result = {
            "vsf_enabled": True,
            "topology_type": topology_type,
            "split_state": split_state,
            "members": members,
        }
        _audit_log("get_vsf_topology", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        _audit_log("get_vsf_topology", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_vsf_topology", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


@mcp.tool()
def get_stp(target: str, interface: str = "") -> str:
    """Get STP status from an Aruba CX switch. Returns global STP config, root bridge info, per-port STP state/role, and any inconsistencies (BPDU guard, loop guard, etc). Optional interface filter."""
    try:
        # Global STP status
        sys_data = client.get(target, "/system?selector=status&attributes=stp_status,stp_intialized")
        stp_status = sys_data.get("stp_status", {})
        stp_enabled = sys_data.get("stp_intialized", False)

        # Get STP instances
        instances_data = client.get(target, "/system/stp_instances?depth=2")
        instances = []
        for inst_key, inst in instances_data.items():
            if not isinstance(inst, dict):
                continue
            instances.append({
                "instance": inst_key,
                "bridge_id": inst.get("bridge_identifier", ""),
                "designated_root": inst.get("designated_root", ""),
                "root_path_cost": inst.get("root_path_cost", 0),
                "root_port": inst.get("root_port", ""),
                "priority": inst.get("priority", 0),
                "topology_change_count": inst.get("topology_change_count", 0),
                "topology_unstable": inst.get("topology_unstable", False),
            })

        # Get per-port STP state for each instance
        ports = []
        for inst_key in instances_data:
            try:
                port_data = client.get(target, f"/system/stp_instances/{inst_key}/stp_instance_ports?depth=2")
                for pname, pinfo in port_data.items():
                    if not isinstance(pinfo, dict):
                        continue
                    if interface and pname != interface:
                        continue
                    inconsistent = pinfo.get("port_inconsistent", {})
                    has_issue = any(v is True for v in inconsistent.values()) if isinstance(inconsistent, dict) else False
                    stats = pinfo.get("statistics", {})
                    entry = {
                        "interface": pname,
                        "instance": inst_key,
                        "port_role": pinfo.get("port_role", ""),
                        "port_state": pinfo.get("port_state", ""),
                        "designated_root": pinfo.get("designated_root", ""),
                        "designated_bridge": pinfo.get("designated_bridge", ""),
                    }
                    if has_issue:
                        entry["inconsistencies"] = {k: v for k, v in inconsistent.items() if v is True}
                    if stats.get("BPDUs_Rx", 0) > 0 or stats.get("BPDUs_Tx", 0) > 0:
                        entry["bpdus_rx"] = stats.get("BPDUs_Rx", 0)
                        entry["bpdus_tx"] = stats.get("BPDUs_Tx", 0)
                    ports.append(entry)
            except Exception:
                continue

        result = {
            "stp_enabled": stp_enabled,
            "global_status": stp_status,
            "instances": instances,
            "ports": ports,
        }
        _audit_log("get_stp", target, "success")
        return _json_dumps(result)
    except ArubaCxException as exc:
        _audit_log("get_stp", target, "error")
        return _json_dumps(exc.error.model_dump())
    except Exception as exc:
        _audit_log("get_stp", target, "error")
        return _json_dumps(ArubaCxError(code=ErrorCode.API_ERROR, message=str(exc), target=target).model_dump())


if __name__ == "__main__":
    mcp.run(transport="stdio")
