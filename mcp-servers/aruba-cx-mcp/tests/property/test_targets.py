# Feature: aruba-cx-python-port, Property 30: Target listing completeness without credentials
"""Property test: For any set of configured targets, list_targets() should
return all targets with name, host, port, and should never include username,
password, or any credential field in the output.

**Validates: Requirements 20.1, 20.2**
"""

import json
import os

from hypothesis import given, settings
from hypothesis import strategies as st

from aruba_client import ArubaCxClient


# --- Strategies ---

_nonempty_text = st.text(min_size=1, max_size=50, alphabet=st.characters(
    blacklist_characters="\x00",
    blacklist_categories=("Cs",),
))
_valid_port = st.integers(min_value=1, max_value=65535)
_api_version = st.text(min_size=1, max_size=20, alphabet=st.characters(
    blacklist_characters="\x00",
    blacklist_categories=("Cs",),
))

# Credential-related field names that must never appear in list_targets output
_CREDENTIAL_FIELDS = {"username", "password", "token", "secret", "api_key", "credential"}


@st.composite
def unique_targets_strategy(draw):
    """Generate a list of 1-5 valid target dicts with unique names."""
    count = draw(st.integers(min_value=1, max_value=5))
    targets = []
    used_names = set()
    for i in range(count):
        # Ensure unique names by appending index
        base_name = draw(_nonempty_text)
        name = f"{base_name}-{i}"
        used_names.add(name)
        targets.append({
            "name": name,
            "host": draw(_nonempty_text),
            "username": draw(_nonempty_text),
            "password": draw(_nonempty_text),
            "port": draw(_valid_port),
            "api_version": draw(_api_version),
            "verify_ssl": draw(st.booleans()),
        })
    return targets


# --- Property Test ---


@settings(max_examples=100)
@given(targets=unique_targets_strategy())
def test_target_listing_completeness_without_credentials(targets: list[dict]):
    """For any set of configured targets, list_targets() should return all
    targets with name, host, port, and should never include username, password,
    or any credential field in the output."""
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ["ARUBA_CX_TARGETS"] = json.dumps(targets)
        client = ArubaCxClient()
        result = client.list_targets()

        # Build lookup from input targets by name
        input_by_name = {t["name"]: t for t in targets}

        # 1. All targets are returned
        assert len(result) == len(targets), (
            f"Expected {len(targets)} targets, got {len(result)}"
        )

        returned_names = {t["name"] for t in result}
        expected_names = {t["name"] for t in targets}
        assert returned_names == expected_names, (
            f"Expected names {expected_names}, got {returned_names}"
        )

        # 2. Each returned target has name, host, port
        for entry in result:
            assert "name" in entry, f"Missing 'name' in target entry: {entry}"
            assert "host" in entry, f"Missing 'host' in target entry: {entry}"
            assert "port" in entry, f"Missing 'port' in target entry: {entry}"

            # Verify values match input
            source = input_by_name[entry["name"]]
            assert entry["host"] == source["host"], (
                f"Host mismatch for {entry['name']}: "
                f"expected {source['host']!r}, got {entry['host']!r}"
            )
            assert entry["port"] == source["port"], (
                f"Port mismatch for {entry['name']}: "
                f"expected {source['port']}, got {entry['port']}"
            )

            # 3. No credential fields in output
            entry_keys = set(entry.keys())
            leaked = entry_keys & _CREDENTIAL_FIELDS
            assert not leaked, (
                f"Credential fields {leaked} found in target listing "
                f"for {entry['name']}: {entry}"
            )

            # Also check values — credentials should not appear as values
            input_username = source["username"]
            input_password = source["password"]
            for key, value in entry.items():
                if isinstance(value, str):
                    assert value != input_password or key in ("name", "host", "api_version"), (
                        f"Password value leaked in field '{key}' "
                        f"for target {entry['name']}"
                    )
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old
