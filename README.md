# aruba-cx-mcp-server

A Python MCP (Model Context Protocol) server that bridges AI assistants to Aruba CX switches via their REST API. Manage interfaces, VLANs, optics, firmware, and more — directly from your AI-powered IDE or chat client.

Built with [FastMCP](https://github.com/jlowin/fastmcp), [Pydantic](https://docs.pydantic.dev/), and [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing.

## Features

- **16 tools** (11 read + 5 write) covering the full AOS-CX REST API
- **System** — system info, serial numbers, uptime, firmware, VSF member details with per-member serials and roles, memory utilization
- **Interfaces** — list, inspect, configure (admin state, speed, duplex, VLAN, description)
- **VLANs** — list, create, delete
- **Configuration** — running/startup config, write memory, checkpoints
- **Routing** — routing table, ARP table
- **LLDP** — neighbor discovery
- **MAC address table** — with VLAN and MAC filters
- **Optics/DOM** — transceiver info, per-lane DOM diagnostics, health assessment with threshold violation detection
- **ISSU** — readiness check, firmware staging, upgrade, rollback timer, confirmation
- **Firmware** — upload from local file, download from HTTP, boot bank info
- **VSF** — topology and member information
- **Multi-switch** — manage multiple switches from a single server instance

## Installation

```bash
git clone https://github.com/slientnight/aruba-cx-mcp-server.git
cd aruba-cx-mcp-server
pip install -r mcp-servers/aruba-cx-mcp/requirements.txt
```

## Configuration

Switches can be configured two ways:

### Option 1: Config file (recommended)

Create `aruba-cx-config.json` (see `aruba-cx-config.example.json`):

```json
{
  "targets": [
    {
      "name": "core-switch-1",
      "host": "10.0.1.1",
      "username": "admin",
      "password": "your-password",
      "port": 443,
      "verify_ssl": false
    },
    {
      "name": "access-switch-2",
      "host": "10.0.1.2",
      "username": "admin",
      "password": "your-password"
    }
  ],
  "timeout": 30
}
```

The server looks for `aruba-cx-config.json` in the current directory. Set `ARUBA_CX_CONFIG` to use a different path.

### Option 2: Environment variable

```bash
export ARUBA_CX_TARGETS='[{"name":"my-switch","host":"10.0.0.1","username":"admin","password":"your-password","verify_ssl":false}]'
```

If both are set, `ARUBA_CX_TARGETS` takes priority.

Each target requires `name`, `host`, `username`, `password`. Optional fields: `port` (default 443), `api_version` (default "v10.13"), `verify_ssl` (default true).

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARUBA_CX_TARGETS` | `[]` | JSON array of target switch definitions |
| `ARUBA_CX_CONFIG` | | Path to JSON config file |
| `ARUBA_CX_TIMEOUT` | `30` | Request timeout in seconds |
| `ITSM_ENABLED` | `false` | Enable ITSM gate for write operations |
| `ITSM_LAB_MODE` | `false` | Format-only CR validation (skip external checks) |

## Usage with MCP clients

### Kiro / VS Code

Add to `.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "aruba-cx-mcp": {
      "command": "python3",
      "args": ["-u", "mcp-servers/aruba-cx-mcp/aruba_cx_mcp_server.py"],
      "env": {
        "ARUBA_CX_CONFIG": "/path/to/aruba-cx-config.json"
      }
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "aruba-cx-mcp": {
      "command": "python3",
      "args": ["-u", "/path/to/aruba-cx-mcp-server/mcp-servers/aruba-cx-mcp/aruba_cx_mcp_server.py"],
      "env": {
        "ARUBA_CX_CONFIG": "/path/to/aruba-cx-config.json"
      }
    }
  }
}
```

## Available tools

### Read tools (11)

| Tool | Description |
|------|-------------|
| `get_system` | System info + health (hostname, firmware, serial, platform, uptime, MAC, VSF member serials/roles, memory) |
| `get_interfaces` | All interfaces summary, or one detailed (pass `interface` param) |
| `get_vlans` | All VLANs with ID, name, status |
| `get_config` | Running or startup config (pass `config_type`) |
| `get_routing` | Routes or ARP table (pass `table="routes"` or `"arp"`) |
| `get_lldp_neighbors` | LLDP neighbor details |
| `get_mac_address_table` | MAC table with optional VLAN/MAC filters |
| `get_optics` | Transceiver info, DOM diagnostics, or health (pass `detail`) |
| `get_issu_info` | ISSU readiness, status, progress |
| `get_firmware` | Firmware versions, boot bank, transfer progress |
| `get_vsf_topology` | VSF stack topology and members |

### Write tools (5)

| Tool | Description |
|------|-------------|
| `configure_interface` | Set admin state, description, speed, duplex, VLAN |
| `manage_vlan` | Create or delete a VLAN (pass `action`) |
| `save_config` | Write memory or create checkpoint (pass `action`) |
| `manage_issu` | Initiate, set rollback timer, or confirm (pass `action`) |
| `manage_firmware` | Upload or download firmware (pass `action`) |

Write tools accept an optional `change_request_number` parameter. When `ITSM_ENABLED=true`, a valid CR (format: `CHG` + digits) is required.

## Testing

76 property-based tests validate 32 correctness properties using Hypothesis:

```bash
cd mcp-servers/aruba-cx-mcp
python -m pytest tests/ -v
```

## Requirements

- Python >= 3.10
- Aruba CX switch running AOS-CX with REST API enabled (tested on FL.10.16 / 6300 VSF)
- Network access from the MCP server to the switch management interface

## License

MIT
