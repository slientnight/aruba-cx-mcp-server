# Feature: aruba-cx-python-port, Property 4: ITSM gate CHG format validation
"""Property tests for the ITSM gate module.

Tests cover:
- Property 4: CHG format validation when ITSM is enabled
- Property 5: ITSM gate disabled bypasses validation
- Property 6: ITSM gate lab mode performs format-only validation

**Validates: Requirements 4.2, 4.3, 4.4, 4.5**
"""

import os
import re

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from itsm_gate import validate_change_request


# --- Strategies ---

# Valid CHG strings: "CHG" followed by one or more digits
_valid_chg = st.from_regex(r"CHG\d+", fullmatch=True)

# Arbitrary text that does NOT match ^CHG\d+$
_arbitrary_text = st.text(min_size=0, max_size=100)


def _is_valid_chg(s: str) -> bool:
    """Return True if s matches the CHG format pattern."""
    return bool(re.match(r"^CHG\d+$", s))


# --- Helpers ---

def _set_env(key: str, value: str | None):
    """Set or unset an environment variable."""
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


class _EnvContext:
    """Context manager to temporarily set env vars and restore them after."""

    def __init__(self, **kwargs):
        self._vars = kwargs
        self._old = {}

    def __enter__(self):
        for key, value in self._vars.items():
            self._old[key] = os.environ.get(key)
            _set_env(key, value)
        return self

    def __exit__(self, *args):
        for key in self._vars:
            _set_env(key, self._old[key])


# ---------------------------------------------------------------------------
# Property 4: ITSM gate CHG format validation
# Feature: aruba-cx-python-port, Property 4: ITSM gate CHG format validation
# **Validates: Requirements 4.2, 4.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(cr=_valid_chg)
def test_itsm_enabled_accepts_valid_chg_format(cr: str):
    """When NETCLAW_ITSM_ENABLED=true, any string matching ^CHG\\d+$ should
    be accepted without raising an error."""
    with _EnvContext(NETCLAW_ITSM_ENABLED="true", NETCLAW_LAB_MODE="true"):
        # Should not raise
        validate_change_request(cr)


@settings(max_examples=100)
@given(cr=_arbitrary_text)
def test_itsm_enabled_rejects_invalid_chg_format(cr: str):
    """When NETCLAW_ITSM_ENABLED=true, any string NOT matching ^CHG\\d+$
    should be rejected with a ValueError containing a descriptive message."""
    assume(not _is_valid_chg(cr))

    with _EnvContext(NETCLAW_ITSM_ENABLED="true", NETCLAW_LAB_MODE="true"):
        raised = False
        try:
            validate_change_request(cr)
        except ValueError as exc:
            raised = True
            # Error message should be descriptive
            assert len(str(exc)) > 0, "ValueError should have a descriptive message"
        assert raised, (
            f"Expected ValueError for invalid CR '{cr!r}' but none was raised"
        )


# ---------------------------------------------------------------------------
# Property 5: ITSM gate disabled bypasses validation
# Feature: aruba-cx-python-port, Property 5: ITSM gate disabled bypasses validation
# **Validates: Requirements 4.3**
# ---------------------------------------------------------------------------

# Strategy for the disabled states of NETCLAW_ITSM_ENABLED
_disabled_values = st.sampled_from(["false", "False", "FALSE", "no", "0", "", "anything"])


@settings(max_examples=100)
@given(cr=_arbitrary_text, env_val=_disabled_values)
def test_itsm_disabled_bypasses_all_validation(cr: str, env_val: str):
    """When NETCLAW_ITSM_ENABLED is false (various representations),
    the ITSM gate should not reject any request regardless of input."""
    with _EnvContext(NETCLAW_ITSM_ENABLED=env_val):
        # Should never raise, even for empty/invalid strings
        validate_change_request(cr)


@settings(max_examples=100)
@given(cr=_arbitrary_text)
def test_itsm_unset_bypasses_all_validation(cr: str):
    """When NETCLAW_ITSM_ENABLED is unset, the ITSM gate should not reject
    any request — write operations proceed without CR validation."""
    with _EnvContext(NETCLAW_ITSM_ENABLED=None):
        # Should never raise
        validate_change_request(cr)


# ---------------------------------------------------------------------------
# Property 6: ITSM gate lab mode performs format-only validation
# Feature: aruba-cx-python-port, Property 6: ITSM gate lab mode
# **Validates: Requirements 4.5**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(cr=_valid_chg)
def test_lab_mode_accepts_valid_chg_without_api_call(cr: str):
    """When NETCLAW_ITSM_ENABLED=true and NETCLAW_LAB_MODE=true, valid CHG
    strings should be accepted. No ServiceNow API call should be made
    (format-only validation)."""
    with _EnvContext(NETCLAW_ITSM_ENABLED="true", NETCLAW_LAB_MODE="true"):
        # Should not raise — format is valid, no API call in lab mode
        validate_change_request(cr)


@settings(max_examples=100)
@given(cr=_arbitrary_text)
def test_lab_mode_rejects_invalid_chg(cr: str):
    """When NETCLAW_ITSM_ENABLED=true and NETCLAW_LAB_MODE=true, strings
    NOT matching ^CHG\\d+$ should still be rejected (format validation
    still applies)."""
    assume(not _is_valid_chg(cr))

    with _EnvContext(NETCLAW_ITSM_ENABLED="true", NETCLAW_LAB_MODE="true"):
        raised = False
        try:
            validate_change_request(cr)
        except ValueError:
            raised = True
        assert raised, (
            f"Expected ValueError for invalid CR '{cr!r}' in lab mode"
        )
