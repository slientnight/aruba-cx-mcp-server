# TOOLS — MCP Server Tool Reference

## Connection Details

| MCP Server | Configuration |
|------------|---------------|
| Aruba CX MCP | `aruba-cx-config.json` file or `ARUBA_CX_TARGETS` env var |

## Aruba CX MCP Server

16 tools (11 read + 5 write) for Aruba CX switch management via AOS-CX REST API.

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
