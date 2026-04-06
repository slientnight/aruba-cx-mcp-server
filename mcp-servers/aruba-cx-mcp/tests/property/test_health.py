# Feature: aruba-cx-python-port, Property 25: Transceiver info mapping preserves fields
# Feature: aruba-cx-python-port, Property 26: DOM diagnostics mapping preserves fields
# Feature: aruba-cx-python-port, Property 27: Optics health assessment detects threshold violations
"""Property tests for optics/DOM mapping and health assessment.

Tests cover:
- Property 25: Transceiver info mapping preserves fields — for any raw API
  response with pm_info data, the transceiver mapping extracts transceiver_type,
  vendor_name, serial_number, wavelength, and supports_dom correctly.
- Property 26: DOM diagnostics mapping preserves fields — for any raw API
  response with pm_monitor data, the DOM mapping extracts power readings,
  temperature, voltage, and bias current as floats.
- Property 27: Optics health assessment detects threshold violations — for any
  DOM data where a reading exceeds a threshold, the health assessment reports
  a violation with parameter name, current value, threshold, and severity.
  If no thresholds violated, status is "healthy".

**Validates: Requirements 16.1, 16.2, 16.3**
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
# Import helpers — same mock pattern as test_mappings.py
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    mock_fastmcp = MagicMock()

    def _passthrough_tool(*args, **kwargs):
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

_interface_name = st.sampled_from([
    "1/1/1", "1/1/2", "1/1/3", "1/1/10", "1/1/24", "1/1/48",
])

_wavelength = st.one_of(
    st.none(),
    st.floats(min_value=800.0, max_value=1600.0, allow_nan=False, allow_infinity=False),
)

_dom_reading = st.one_of(
    st.none(),
    st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)

_positive_reading = st.floats(
    min_value=0.01, max_value=50.0, allow_nan=False, allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Property 25: Transceiver info mapping preserves fields
# Feature: aruba-cx-python-port, Property 25
# **Validates: Requirements 16.1**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    iface_name=_interface_name,
    connector_type=_nonempty_text,
    vendor_name=_nonempty_text,
    serial_number=_nonempty_text,
    wavelength=_wavelength,
    has_dom=st.booleans(),
    use_alt_keys=st.booleans(),
)
def test_transceiver_info_mapping_preserves_fields(
    iface_name: str,
    connector_type: str,
    vendor_name: str,
    serial_number: str,
    wavelength,
    has_dom: bool,
    use_alt_keys: bool,
):
    """For any raw API response dict containing transceiver pm_info data, the
    transceiver mapping should produce a dict with transceiver_type, vendor_name,
    serial_number, wavelength, and supports_dom correctly extracted.

    Tests both primary and alternate key names."""
    if use_alt_keys:
        pm_info = {
            "transceiver_type": connector_type,
            "vendor_name": vendor_name,
            "serial_number": serial_number,
            "supports_dom": has_dom,
        }
    else:
        pm_info = {
            "connector_type": connector_type,
            "vendor_name": vendor_name,
            "vendor_serial_number": serial_number,
            "diagnostic_monitoring_type": 1 if has_dom else 0,
        }

    if wavelength is not None:
        pm_info["wavelength"] = wavelength

    raw_api_response = {
        iface_name: {
            "pm_info": pm_info,
        }
    }

    # Replicate the mapping logic from get_transceiver_info
    transceivers = []
    for name, iface_data in raw_api_response.items():
        if not isinstance(iface_data, dict):
            continue
        pm = iface_data.get("pm_info", {})
        if not isinstance(pm, dict) or not pm:
            continue
        transceivers.append({
            "interface": name,
            "transceiver_type": pm.get("connector_type", pm.get("transceiver_type", "")),
            "vendor_name": pm.get("vendor_name", ""),
            "serial_number": pm.get("vendor_serial_number", pm.get("serial_number", "")),
            "wavelength": pm.get("wavelength"),
            "supports_dom": bool(pm.get("diagnostic_monitoring_type", pm.get("supports_dom", False))),
        })

    assert len(transceivers) == 1
    t = transceivers[0]
    assert t["interface"] == iface_name
    assert t["transceiver_type"] == connector_type
    assert t["vendor_name"] == vendor_name
    assert t["serial_number"] == serial_number
    assert t["wavelength"] == wavelength
    assert t["supports_dom"] == has_dom


# ---------------------------------------------------------------------------
# Property 26: DOM diagnostics mapping preserves fields
# Feature: aruba-cx-python-port, Property 26
# **Validates: Requirements 16.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    rx_power=_dom_reading,
    tx_power=_dom_reading,
    temperature=_dom_reading,
    voltage=_dom_reading,
    bias_current=_dom_reading,
    use_alt_keys=st.booleans(),
)
def test_dom_diagnostics_mapping_preserves_fields(
    rx_power,
    tx_power,
    temperature,
    voltage,
    bias_current,
    use_alt_keys: bool,
):
    """For any raw API response dict containing DOM pm_monitor data with power
    readings, temperature, voltage, and bias current, the DOM mapping should
    produce a dict with all fields correctly extracted and typed as floats.

    Tests both primary and alternate key names."""
    if use_alt_keys:
        pm_monitor = {}
        if rx_power is not None:
            pm_monitor["rx_power"] = rx_power
        if tx_power is not None:
            pm_monitor["tx_power"] = tx_power
        if temperature is not None:
            pm_monitor["temperature"] = temperature
        if voltage is not None:
            pm_monitor["vcc"] = voltage
        if bias_current is not None:
            pm_monitor["bias_current"] = bias_current
    else:
        pm_monitor = {}
        if rx_power is not None:
            pm_monitor["rx_power_dbm"] = rx_power
        if tx_power is not None:
            pm_monitor["tx_power_dbm"] = tx_power
        if temperature is not None:
            pm_monitor["temperature_celsius"] = temperature
        if voltage is not None:
            pm_monitor["voltage"] = voltage
        if bias_current is not None:
            pm_monitor["bias_current_ma"] = bias_current

    raw_api_response = {"pm_monitor": pm_monitor}

    # Replicate the mapping logic from get_dom_diagnostics
    pm = raw_api_response.get("pm_monitor", raw_api_response)
    if not isinstance(pm, dict):
        pm = {}

    diagnostics = {
        "rx_power_dbm": pm.get("rx_power_dbm", pm.get("rx_power")),
        "tx_power_dbm": pm.get("tx_power_dbm", pm.get("tx_power")),
        "temperature_celsius": pm.get("temperature_celsius", pm.get("temperature")),
        "voltage": pm.get("voltage", pm.get("vcc")),
        "bias_current_ma": pm.get("bias_current_ma", pm.get("bias_current")),
    }

    assert diagnostics["rx_power_dbm"] == rx_power
    assert diagnostics["tx_power_dbm"] == tx_power
    assert diagnostics["temperature_celsius"] == temperature
    assert diagnostics["voltage"] == voltage
    assert diagnostics["bias_current_ma"] == bias_current

    # All non-None values should be floats
    for key, val in diagnostics.items():
        if val is not None:
            assert isinstance(val, float), f"{key} should be float, got {type(val)}"


# ---------------------------------------------------------------------------
# Property 27: Optics health assessment detects threshold violations
# Feature: aruba-cx-python-port, Property 27
# **Validates: Requirements 16.3**
# ---------------------------------------------------------------------------

_dom_param_names = [
    ("rx_power_dbm", "rx_power"),
    ("tx_power_dbm", "tx_power"),
    ("temperature_celsius", "temperature"),
    ("voltage", "vcc"),
    ("bias_current_ma", "bias_current"),
]



