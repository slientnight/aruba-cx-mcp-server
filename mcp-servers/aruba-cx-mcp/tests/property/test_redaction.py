# Feature: aruba-cx-python-port, Property 8: Credential redaction across all outputs
"""Property test: For any string containing embedded passwords, tokens, or PEM
certificate blocks, the redaction function should remove all sensitive content.

**Validates: Requirements 5.3, 7.3, 20.2**
"""

import re
import string

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from aruba_client import ArubaCxClient


# --- Strategies ---

# Printable text that won't contain double-quotes (to avoid breaking JSON patterns)
_safe_value = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters='"\\',
    ),
    min_size=1,
    max_size=50,
)

# Non-empty token values (no whitespace, since Bearer token regex matches \S+)
_token_value = st.text(
    alphabet=string.ascii_letters + string.digits + "-_.",
    min_size=1,
    max_size=50,
)

# Surrounding context text
_context_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters='"\\',
    ),
    min_size=0,
    max_size=30,
)

# PEM body content (base64-like)
_pem_body = st.text(
    alphabet=string.ascii_letters + string.digits + "+/=\n",
    min_size=1,
    max_size=100,
)


# --- Property Tests ---


@settings(max_examples=100)
@given(password=_safe_value, prefix=_context_text, suffix=_context_text)
def test_password_values_are_redacted(password: str, prefix: str, suffix: str):
    """Strings with embedded "password": "somevalue" patterns should have
    the password value replaced with "***" after redaction."""
    assume(len(password) > 0)

    text = f'{prefix}"password": "{password}"{suffix}'
    result = ArubaCxClient._redact(text)

    # The original "password": "value" pattern should NOT appear in the output
    assert f'"password": "{password}"' not in result.lower() or password == "***", (
        f'Pattern "password": "{password}" was not redacted from output: {result}'
    )
    # The redacted placeholder should be present
    assert '"password": "***"' in result.lower() or '"password":"***"' in result.lower(), (
        f"Expected redacted password placeholder in output: {result}"
    )


@settings(max_examples=100)
@given(token=_token_value, prefix=_context_text, suffix=_context_text)
def test_bearer_tokens_are_redacted(token: str, prefix: str, suffix: str):
    """Strings with Bearer tokens should have the token value replaced
    with "***" after redaction."""
    assume(len(token) > 0)

    text = f"{prefix}Bearer {token}{suffix}"
    result = ArubaCxClient._redact(text)

    # The original token value should not appear after "Bearer" in the output
    assert f"Bearer {token}" not in result, (
        f"Bearer token '{token}' was not redacted from output: {result}"
    )
    # The redacted placeholder should be present
    assert "Bearer ***" in result or "bearer ***" in result.lower(), (
        f"Expected 'Bearer ***' in output: {result}"
    )


@settings(max_examples=100)
@given(body=_pem_body, prefix=_context_text, suffix=_context_text)
def test_pem_blocks_are_redacted(body: str, prefix: str, suffix: str):
    """Strings with PEM certificate blocks should be replaced with
    [REDACTED CERTIFICATE] after redaction."""
    pem_block = f"-----BEGIN CERTIFICATE-----{body}-----END CERTIFICATE-----"
    text = f"{prefix}{pem_block}{suffix}"
    result = ArubaCxClient._redact(text)

    # The PEM block should not appear in the output
    assert "-----BEGIN CERTIFICATE-----" not in result, (
        f"PEM BEGIN marker was not redacted from output: {result}"
    )
    assert "-----END CERTIFICATE-----" not in result, (
        f"PEM END marker was not redacted from output: {result}"
    )
    # The redacted placeholder should be present
    assert "[REDACTED CERTIFICATE]" in result, (
        f"Expected '[REDACTED CERTIFICATE]' in output: {result}"
    )


@settings(max_examples=100)
@given(
    password=_safe_value,
    token=_token_value,
    pem_body=_pem_body,
)
def test_all_sensitive_content_removed_from_combined_string(
    password: str, token: str, pem_body: str
):
    """When a string contains multiple types of sensitive data (password,
    Bearer token, PEM block), all should be redacted in a single pass."""
    assume(len(password) > 0)
    assume(len(token) > 0)

    pem_block = f"-----BEGIN CERTIFICATE-----{pem_body}-----END CERTIFICATE-----"
    text = (
        f'Login: "password": "{password}" '
        f"Auth: Bearer {token} "
        f"Cert: {pem_block}"
    )
    result = ArubaCxClient._redact(text)

    # The original "password": "value" pattern should be gone
    assert f'"password": "{password}"' not in result.lower() or password == "***", (
        f'Password pattern still present in: {result}'
    )
    # Bearer token should be gone
    assert f"Bearer {token}" not in result, (
        f"Bearer token '{token}' still present in: {result}"
    )
    # PEM block should be gone
    assert "-----BEGIN CERTIFICATE-----" not in result, (
        f"PEM block still present in: {result}"
    )
    assert "-----END CERTIFICATE-----" not in result, (
        f"PEM block still present in: {result}"
    )
