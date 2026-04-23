# Aruba CX MCP Server

A Python MCP server for managing Aruba CX switches via the AOS-CX REST API. Exposes 19 tools (13 read + 6 write) over stdio transport using FastMCP.

## Tools

### Read Tools (13)

| Tool | Description |
|------|-------------|
| `get_system` | System info: hostname, firmware, platform, serial, uptime, CPU, memory, temperature, fans |
| `get_interfaces` | Interface list or detail. `detail`: `config` (default), `stats`, or `full` |
| `get_vlans` | List all VLANs with ID, name, and status |
| `get_config` | Running or startup configuration. `config_type`: `running` (default) or `startup` |
| `get_routing` | Routing table or ARP table. `table`: `routes` (default) or `arp` |
| `get_lldp_neighbors` | LLDP neighbor discovery, optionally filtered by interface |
| `get_mac_address_table` | MAC address table with optional VLAN and MAC filters |
| `get_optics` | Transceiver info, DOM diagnostics, or health assessment |
| `get_issu_info` | ISSU readiness, status, progress, and image versions |
| `get_firmware` | Firmware versions, boot bank info, and upload/download progress |
| `get_vsf_topology` | VSF stack topology with member details and link states |
| `get_stp` | STP status: global config, root bridge, per-port state/role, inconsistencies |
| `get_logs` | Event logs with filters: severity, time range, module, keyword search, limit |

### Write Tools (6)

| Tool | Description |
|------|-------------|
| `configure_interface` | Set admin state, description, speed, duplex, VLAN on an interface |
| `configure_port_access` | Configure port-level AAA: MAC-auth, 802.1X, client limits, auth precedence |
| `manage_vlan` | Create or delete a VLAN |
| `save_config` | Save running config to startup (`write_memory`) or create a named checkpoint |
| `manage_issu` | Initiate ISSU upgrade, set rollback timer, or confirm upgrade |
| `manage_firmware` | Upload firmware from file or download from URL |

### get_logs Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | string | required | Named switch target |
| `severity` | string | `""` | Filter: `emergency`, `alert`, `critical`, `error`, `warning`, `notice`, `info`, `debug` |
| `since` | string | `""` | Time filter: relative (`1h`, `30m`, `7d`) or ISO 8601 timestamp |
| `module` | string | `""` | Module filter (case-insensitive): `intfd`, `hpe-mstpd`, `hpe-restd`, `port-accessd`, etc. |
| `search` | string | `""` | Keyword substring filter on message text (case-insensitive) |
| `limit` | int | `50` | Max entries to return. Clamped to [1, 1000] |

### Tested Platforms

- Aruba 6300M (FL.10.16.1030) — 6-member VSF stack
- Aruba 8360 (FL.10.13.x)

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
| `ITSM_ENABLED` | `false` | Enable ITSM gate for write operations |
| `ITSM_LAB_MODE` | `false` | Format-only CR validation (skip external checks) |
