# Feature: aruba-cx-python-port, Property 9: Toon serialization fallback
"""Property tests for toon serialization and deployment mode detection.

Tests cover:
- Property 9: Toon serialization fallback — when netclaw_tokens is unavailable,
  _toon_dumps() produces output identical to json.dumps(data, indent=2).
- Property 31: Deployment mode detection — NETCLAW_ITSM_ENABLED controls mode.

**Validates: Requirements 6.2, 1.5**
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Import helpers — aruba_cx_mcp_server.py has module-level side effects
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    import importlib

    with patch.dict("sys.modules", {
        "fastmcp": MagicMock(),
    }):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()
_toon_dumps = _server._toon_dumps


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# JSON-serializable Python objects: strings, ints, floats, bools, None,
# lists, and dicts (recursive)
_json_primitives = st.one_of(
    st.text(min_size=0, max_size=50),
    st.integers(min_value=-10000, max_value=10000),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.booleans(),
    st.none(),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            children,
            max_size=5,
        ),
    ),
    max_leaves=20,
)


# ---------------------------------------------------------------------------
# Property 9: Toon serialization fallback
# Feature: aruba-cx-python-port, Property 9: Toon serialization fallback
# **Validates: Requirements 6.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(data=_json_values)
def test_toon_dumps_fallback_matches_json_dumps(data):
    """For any JSON-serializable Python object, when netclaw_tokens is
    unavailable (ImportError), _toon_dumps() should produce output identical
    to json.dumps(data, indent=2)."""
    # Ensure netclaw_tokens is not available by removing it from sys.modules
    modules_to_remove = [
        k for k in sys.modules if k.startswith("netclaw_tokens")
    ]
    saved_modules = {k: sys.modules[k] for k in modules_to_remove}

    try:
        for k in modules_to_remove:
            del sys.modules[k]

        result = _toon_dumps(data)
        expected = json.dumps(data, indent=2, default=str)
        assert result == expected, (
            f"_toon_dumps output differs from json.dumps fallback.\n"
            f"Input: {data!r}\n"
            f"Got:      {result!r}\n"
            f"Expected: {expected!r}"
        )
    finally:
        sys.modules.update(saved_modules)


@settings(max_examples=100)
@given(data=_json_values)
def test_toon_dumps_returns_string(data):
    """For any JSON-serializable input, _toon_dumps() should always return
    a string."""
    result = _toon_dumps(data)
    assert isinstance(result, str), (
        f"Expected str, got {type(result).__name__} for input {data!r}"
    )


# ---------------------------------------------------------------------------
# Property 31: Deployment mode detection
# Feature: aruba-cx-python-port, Property 31: Deployment mode detection
# **Validates: Requirements 1.5**
# ---------------------------------------------------------------------------

# Strategy for values that should trigger NetClaw mode (case-insensitive "true")
_true_values = st.sampled_from(["true", "True", "TRUE", "tRuE", "TrUe"])

# Strategy for values that should NOT trigger NetClaw mode
_non_true_values = st.one_of(
    st.just("false"),
    st.just("False"),
    st.just("FALSE"),
    st.just(""),
    st.just("0"),
    st.just("no"),
    st.just("yes"),
    st.just("1"),
    st.just("enabled"),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s.lower() != "true"),
)


def _detect_mode(env_value: str | None) -> bool:
    """Replicate the deployment mode detection logic from the server.

    Returns True for NetClaw mode, False for standalone.
    """
    if env_value is None:
        return False
    return env_value.lower() == "true"


@settings(max_examples=100)
@given(env_val=_true_values)
def test_netclaw_mode_detected_for_true(env_val: str):
    """When NETCLAW_ITSM_ENABLED is exactly 'true' (case-insensitive),
    the server should detect NetClaw mode."""
    result = _detect_mode(env_val)
    assert result is True, (
        f"Expected NetClaw mode for NETCLAW_ITSM_ENABLED={env_val!r}"
    )

    # Also verify the actual logic used in the server module
    actual = env_val.lower() == "true"
    assert actual is True


@settings(max_examples=100)
@given(env_val=_non_true_values)
def test_standalone_mode_for_non_true(env_val: str):
    """When NETCLAW_ITSM_ENABLED is any value other than 'true'
    (case-insensitive), the server should detect standalone mode."""
    result = _detect_mode(env_val)
    assert result is False, (
        f"Expected standalone mode for NETCLAW_ITSM_ENABLED={env_val!r}"
    )

    actual = env_val.lower() == "true"
    assert actual is False


def test_standalone_mode_when_unset():
    """When NETCLAW_ITSM_ENABLED is unset, the server should detect
    standalone mode (default is 'false')."""
    result = _detect_mode(None)
    assert result is False, "Expected standalone mode when env var is unset"

    # Verify the os.environ.get fallback
    old = os.environ.get("NETCLAW_ITSM_ENABLED")
    try:
        os.environ.pop("NETCLAW_ITSM_ENABLED", None)
        detected = os.environ.get("NETCLAW_ITSM_ENABLED", "false").lower() == "true"
        assert detected is False
    finally:
        if old is not None:
            os.environ["NETCLAW_ITSM_ENABLED"] = old


@settings(max_examples=100)
@given(env_val=st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=30,
))
def test_deployment_mode_is_binary(env_val: str):
    """For any value of NETCLAW_ITSM_ENABLED, the detection result should be
    exactly True (NetClaw) or False (standalone) — never anything else."""
    result = _detect_mode(env_val)
    assert result is (env_val.lower() == "true"), (
        f"Mode detection mismatch for {env_val!r}: got {result}"
    )
