# Aruba CX MCP Server

A Python MCP server for managing Aruba CX switches via the AOS-CX REST API. Exposes 16 tools (11 read + 5 write) over stdio transport using FastMCP.

Supports two deployment modes from a single codebase:

- **Standalone mode**: No NetClaw dependencies. ITSM gate disabled, JSON serialization, basic stderr logging. Write tools work without a Change Request number.
- **NetClaw mode**: Full NetClaw integrations. ITSM gate active, GAIT audit logging, toon serialization. Write operations require a valid ServiceNow Change Request number.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python aruba_cx_mcp_server.py
```

The server communicates over stdio (stdin/stdout) using the MCP protocol.

## Configuration

Switches can be configured two ways:

### Config file (recommended)

Create `aruba-cx-config.json` in the server directory or set `ARUBA_CX_CONFIG`:

```json
{
  "targets": [
    {
      "name": "core-switch-1",
      "host": "10.0.1.1",
      "username": "admin",
      "password": "secret",
      "port": 443,
      "verify_ssl": false
    }
  ],
  "timeout": 30
}
```

### Environment variable

```bash
export ARUBA_CX_TARGETS='[{"name":"my-switch","host":"10.0.0.1","username":"admin","password":"secret","verify_ssl":false}]'
```

If both are set, `ARUBA_CX_TARGETS` takes priority.

Required fields: `name`, `host`, `username`, `password`
Optional fields: `port` (default 443), `api_version` (default "v10.13"), `verify_ssl` (default true)

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARUBA_CX_TARGETS` | `[]` | JSON array of target switch definitions |
| `ARUBA_CX_CONFIG` | | Path to JSON config file |
| `ARUBA_CX_TIMEOUT` | `30` | Request timeout in seconds |
| `NETCLAW_ITSM_ENABLED` | `false` | Enable ITSM gate for write operations |
| `NETCLAW_LAB_MODE` | `false` | Skip ServiceNow API check (format-only CR validation) |

## Tools

### Read Tools (11)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_system` | `target` | System info + health (hostname, firmware, serial, platform, uptime, MAC, VSF member serials/roles, memory) |
| `get_interfaces` | `target`, `interface?` | All interfaces summary, or one detailed with statistics |
| `get_vlans` | `target` | All VLANs with ID, name, status |
| `get_config` | `target`, `config_type?` | Running (default) or startup configuration |
| `get_routing` | `target`, `table?`, `vrf?` | Routes (default) or ARP table |
| `get_lldp_neighbors` | `target`, `interface?` | LLDP neighbor details with optional interface filter |
| `get_mac_address_table` | `target`, `vlan_id?`, `mac_address?` | MAC table with optional VLAN/MAC filters |
| `get_optics` | `target`, `interface?`, `detail?` | Transceiver info (default), DOM diagnostics, or health assessment |
| `get_issu_info` | `target` | ISSU readiness, status, progress, blocking conditions |
| `get_firmware` | `target` | Firmware versions, boot bank, transfer progress |
| `get_vsf_topology` | `target` | VSF stack topology, member roles, link states |

### Write Tools (5)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `configure_interface` | `target`, `interface`, `admin_state?`, `description?`, `speed?`, `duplex?`, `vlan?` | Configure interface settings |
| `manage_vlan` | `target`, `action`, `vlan_id`, `name?` | Create or delete a VLAN |
| `save_config` | `target`, `action?`, `checkpoint_name?` | Write memory (default) or create checkpoint |
| `manage_issu` | `target`, `action`, `firmware_image?`, `timeout_seconds?` | Initiate, set rollback timer, or confirm ISSU |
| `manage_firmware` | `target`, `action`, `file_path?`, `url?` | Upload or download firmware |

Write tools accept an optional `change_request_number` parameter. When `NETCLAW_ITSM_ENABLED=true`, a valid ServiceNow CR (format: `CHG` + digits) is required.

## Deployment Modes

### Standalone

Set `NETCLAW_ITSM_ENABLED=false` (or leave unset):

- Write tools accept but do not require `change_request_number`
- Responses serialized as JSON
- Audit logs emitted to stderr as structured JSON

### NetClaw

Set `NETCLAW_ITSM_ENABLED=true`:

- Write tools require a valid ServiceNow Change Request number (`CHG` + digits)
- Responses serialized via toon for token optimization
- GAIT audit logging with baseline/verify for write operations
- Lab mode (`NETCLAW_LAB_MODE=true`) skips ServiceNow API check but still validates CR format
