# Feature: aruba-cx-python-port, Property 1: Target configuration round-trip
"""Property test: For any valid ArubaCxTarget, serializing to JSON dict and
parsing back produces an equivalent target with identical field values,
including defaults for omitted optional fields.

**Validates: Requirements 2.1, 2.3**
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from models import ArubaCxTarget


# --- Strategies ---

# Non-empty text for required string fields (name, host, username, password)
_nonempty_text = st.text(min_size=1, max_size=50)

# Port range 1-65535
_valid_port = st.integers(min_value=1, max_value=65535)

# API version strings
_api_version = st.text(min_size=1, max_size=20)

# Boolean for verify_ssl
_verify_ssl = st.booleans()


def aruba_cx_target_strategy():
    """Build a strategy that generates valid ArubaCxTarget objects."""
    return st.builds(
        ArubaCxTarget,
        name=_nonempty_text,
        host=_nonempty_text,
        username=_nonempty_text,
        password=_nonempty_text,
        port=_valid_port,
        api_version=_api_version,
        verify_ssl=_verify_ssl,
    )


# --- Property Test ---


@settings(max_examples=100)
@given(target=aruba_cx_target_strategy())
def test_target_config_round_trip(target: ArubaCxTarget):
    """Serializing an ArubaCxTarget to dict (JSON format) and parsing it back
    should produce an equivalent target with the same field values."""
    # 1. Serialize to dict (simulating JSON format used in ARUBA_CX_TARGETS)
    serialized = target.model_dump()

    # 2. Round-trip through JSON to simulate real env var parsing
    json_str = json.dumps(serialized)
    parsed_dict = json.loads(json_str)

    # 3. Parse back into ArubaCxTarget
    restored = ArubaCxTarget(**parsed_dict)

    # 4. Assert all fields match
    assert restored.name == target.name
    assert restored.host == target.host
    assert restored.username == target.username
    assert restored.password == target.password
    assert restored.port == target.port
    assert restored.api_version == target.api_version
    assert restored.verify_ssl == target.verify_ssl


@settings(max_examples=100)
@given(target=aruba_cx_target_strategy())
def test_target_config_round_trip_with_optional_omission(target: ArubaCxTarget):
    """When optional fields are omitted from the serialized dict, parsing back
    should apply the correct defaults (port=443, api_version='v10.13',
    verify_ssl=True)."""
    # 1. Serialize to dict with only required fields
    required_only = {
        "name": target.name,
        "host": target.host,
        "username": target.username,
        "password": target.password,
    }

    # 2. Round-trip through JSON
    json_str = json.dumps(required_only)
    parsed_dict = json.loads(json_str)

    # 3. Parse back into ArubaCxTarget
    restored = ArubaCxTarget(**parsed_dict)

    # 4. Assert required fields match
    assert restored.name == target.name
    assert restored.host == target.host
    assert restored.username == target.username
    assert restored.password == target.password

    # 5. Assert defaults are applied for omitted optional fields
    assert restored.port == 443
    assert restored.api_version == "v10.13"
    assert restored.verify_ssl is True


# Feature: aruba-cx-python-port, Property 2: Target validation rejects invalid configs
"""Property test: For any target configuration dict where a required field
(name, host, username, password) is missing, or where port is outside the
range 1-65535, ArubaCxTarget model validation should raise a validation error.

**Validates: Requirements 2.2, 8.2**
"""

import pydantic


# --- Strategies for invalid configs ---

_required_fields = ["name", "host", "username", "password"]


def _valid_target_dict():
    """Return a fully valid target dict as a baseline."""
    return {
        "name": "switch1",
        "host": "10.0.0.1",
        "username": "admin",
        "password": "secret",
        "port": 443,
    }


@st.composite
def missing_required_field_strategy(draw):
    """Generate a target dict with one or more required fields removed."""
    base = _valid_target_dict()
    # Choose a non-empty subset of required fields to remove
    fields_to_remove = draw(
        st.lists(
            st.sampled_from(_required_fields),
            min_size=1,
            max_size=len(_required_fields),
            unique=True,
        )
    )
    for field in fields_to_remove:
        del base[field]
    return base


@st.composite
def invalid_port_strategy(draw):
    """Generate a target dict with port outside 1-65535 range."""
    base = _valid_target_dict()
    port = draw(
        st.one_of(
            st.integers(max_value=0),  # 0 and negative
            st.integers(min_value=65536),  # above max
        )
    )
    base["port"] = port
    return base


# --- Property Tests ---


@settings(max_examples=100)
@given(invalid_dict=missing_required_field_strategy())
def test_target_validation_rejects_missing_required_fields(invalid_dict: dict):
    """ArubaCxTarget should raise ValidationError when required fields are missing."""
    try:
        ArubaCxTarget(**invalid_dict)
        assert False, f"Expected ValidationError for dict missing required fields: {invalid_dict}"
    except pydantic.ValidationError:
        pass  # Expected


@settings(max_examples=100)
@given(invalid_dict=invalid_port_strategy())
def test_target_validation_rejects_invalid_port(invalid_dict: dict):
    """ArubaCxTarget should raise ValidationError when port is outside 1-65535."""
    try:
        ArubaCxTarget(**invalid_dict)
        assert False, f"Expected ValidationError for port={invalid_dict['port']}"
    except pydantic.ValidationError:
        pass  # Expected


# Feature: aruba-cx-python-port, Property 3: Invalid targets produce zero-target initialization
"""Property test: For any string that is not valid JSON, or is an empty string,
or is not set, loading targets from ARUBA_CX_TARGETS should result in zero
loaded targets without raising an exception. For any JSON array containing a
mix of valid and invalid target entries, only the valid entries should be loaded.

**Validates: Requirements 2.4, 2.5**
"""

import os

from hypothesis import assume

from aruba_client import ArubaCxClient


# --- Strategies for invalid JSON ---

# Strings that are definitely not valid JSON (exclude strings that happen to be
# valid JSON arrays like "[]")
@st.composite
def invalid_json_strategy(draw):
    """Generate strings that are not valid JSON or are valid JSON but not arrays.

    Excludes null characters since os.environ cannot contain them.
    """
    choice = draw(st.integers(min_value=0, max_value=3))
    if choice == 0:
        # Random text that isn't valid JSON (no null bytes for env var compat)
        s = draw(
            st.text(
                alphabet=st.characters(blacklist_characters="\x00"),
                min_size=1,
                max_size=100,
            )
        )
        try:
            result = json.loads(s)
            # If it parses as a list, it could be a valid array — skip it
            assume(not isinstance(result, list))
        except (json.JSONDecodeError, ValueError):
            pass  # Good — it's invalid JSON
        return s
    elif choice == 1:
        # Truncated JSON
        return draw(st.sampled_from(["{", "[", '{"name":', '[{"name": "x"']))
    elif choice == 2:
        # Non-array valid JSON (object, number, string, bool, null)
        return draw(
            st.sampled_from(
                ['{"key": "value"}', "42", '"hello"', "true", "null"]
            )
        )
    else:
        # Garbage bytes
        return draw(st.text(alphabet="!@#$%^&*()_+-=<>?/\\|~`", min_size=1, max_size=50))


# A valid target dict for mixing tests
def _make_valid_entry(name: str = "switch1") -> dict:
    return {
        "name": name,
        "host": "10.0.0.1",
        "username": "admin",
        "password": "secret",
        "port": 443,
    }


# Invalid target entries (missing required fields, bad port, wrong types)
@st.composite
def invalid_entry_strategy(draw):
    """Generate a dict that will fail ArubaCxTarget validation."""
    choice = draw(st.integers(min_value=0, max_value=4))
    if choice == 0:
        # Missing 'name'
        return {"host": "10.0.0.1", "username": "admin", "password": "secret"}
    elif choice == 1:
        # Missing 'host'
        return {"name": "sw", "username": "admin", "password": "secret"}
    elif choice == 2:
        # Missing 'password'
        return {"name": "sw", "host": "10.0.0.1", "username": "admin"}
    elif choice == 3:
        # Port out of range
        return {
            "name": "sw",
            "host": "10.0.0.1",
            "username": "admin",
            "password": "secret",
            "port": draw(st.one_of(st.integers(max_value=0), st.integers(min_value=65536))),
        }
    else:
        # Not a dict at all
        return draw(st.one_of(st.text(max_size=20), st.integers(), st.none()))


@st.composite
def mixed_entries_strategy(draw):
    """Generate a JSON array with a mix of valid and invalid target entries.

    Returns (json_string, expected_valid_count, expected_valid_names).
    """
    # Generate 1-5 valid entries with unique names
    num_valid = draw(st.integers(min_value=0, max_value=5))
    valid_entries = []
    valid_names = set()
    for i in range(num_valid):
        name = f"valid-switch-{i}"
        valid_entries.append(_make_valid_entry(name))
        valid_names.add(name)

    # Generate 1-5 invalid entries
    num_invalid = draw(st.integers(min_value=1, max_value=5))
    invalid_entries = [draw(invalid_entry_strategy()) for _ in range(num_invalid)]

    # Combine and shuffle
    all_entries = valid_entries + invalid_entries
    shuffled = draw(st.permutations(all_entries))

    return json.dumps(list(shuffled)), num_valid, valid_names


# --- Property Tests ---


@settings(max_examples=100)
@given(bad_json=invalid_json_strategy())
def test_zero_target_invalid_json(bad_json: str):
    """Loading targets from ARUBA_CX_TARGETS with invalid JSON should result
    in zero loaded targets without raising an exception."""
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ["ARUBA_CX_TARGETS"] = bad_json
        client = ArubaCxClient()
        targets = client.list_targets()
        assert len(targets) == 0, (
            f"Expected zero targets for invalid JSON input {bad_json!r}, "
            f"got {len(targets)}"
        )
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old


@settings(max_examples=100)
@given(empty_val=st.sampled_from(["", " ", "  ", "\t", "\n", " \n\t "]))
def test_zero_target_empty_string(empty_val: str):
    """Loading targets from ARUBA_CX_TARGETS with empty string should result
    in zero loaded targets without raising an exception."""
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ["ARUBA_CX_TARGETS"] = empty_val
        client = ArubaCxClient()
        targets = client.list_targets()
        assert len(targets) == 0, (
            f"Expected zero targets for empty/whitespace input {empty_val!r}, "
            f"got {len(targets)}"
        )
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old


@settings(max_examples=100)
@given(data=st.data())
def test_zero_target_unset_env(data):
    """Loading targets when ARUBA_CX_TARGETS is not set should result
    in zero loaded targets without raising an exception."""
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ.pop("ARUBA_CX_TARGETS", None)
        client = ArubaCxClient()
        targets = client.list_targets()
        assert len(targets) == 0, (
            f"Expected zero targets when env var is unset, got {len(targets)}"
        )
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old


@settings(max_examples=100)
@given(mixed=mixed_entries_strategy())
def test_mixed_valid_invalid_entries(mixed):
    """For a JSON array containing a mix of valid and invalid target entries,
    only the valid entries should be loaded."""
    json_str, expected_count, expected_names = mixed
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ["ARUBA_CX_TARGETS"] = json_str
        client = ArubaCxClient()
        targets = client.list_targets()
        assert len(targets) == expected_count, (
            f"Expected {expected_count} valid targets, got {len(targets)} "
            f"from input: {json_str}"
        )
        loaded_names = {t["name"] for t in targets}
        assert loaded_names == expected_names, (
            f"Expected target names {expected_names}, got {loaded_names}"
        )
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old
