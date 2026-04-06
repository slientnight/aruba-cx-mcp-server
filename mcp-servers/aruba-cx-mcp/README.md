# Aruba CX MCP Server

A Python MCP server for managing Aruba CX switches via the AOS-CX REST API. Exposes 16 tools (11 read + 5 write) over stdio transport using FastMCP.

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
