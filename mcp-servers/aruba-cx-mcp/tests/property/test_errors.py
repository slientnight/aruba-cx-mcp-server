# Feature: aruba-cx-python-port, Property 10: Error classification correctness
"""Property test: For any HTTP status code in the range 400-599, the error
classifier should map it to one of the defined ErrorCode categories. For
API_ERROR responses, the resulting ArubaCxError should include the HTTP status
code and a sanitized response body.

**Validates: Requirements 7.1, 7.2**
"""

import os
from unittest.mock import MagicMock

import requests
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from aruba_client import ArubaCxClient
from models import ErrorCode


# --- Helpers ---


def _make_http_error(status_code: int, body: str = "") -> requests.HTTPError:
    """Create a mock requests.HTTPError with a given status code and body."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = body
    error = requests.HTTPError(response=mock_response)
    return error


def _make_client() -> ArubaCxClient:
    """Create an ArubaCxClient with no targets (avoids env var side effects)."""
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ.pop("ARUBA_CX_TARGETS", None)
        return ArubaCxClient()
    finally:
        if old is not None:
            os.environ["ARUBA_CX_TARGETS"] = old


# --- Strategies ---

# HTTP status codes in the 400-599 range (client + server errors)
_http_error_status = st.integers(min_value=400, max_value=599)

# Non-401 HTTP error status codes (4xx/5xx excluding 401)
_non_401_status = st.integers(min_value=400, max_value=599).filter(lambda x: x != 401)

# Response body text (safe printable text)
_response_body = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=200,
)

# Target name
_target_name = st.text(min_size=1, max_size=30)


# --- Property Tests ---


@settings(max_examples=100)
@given(status_code=_http_error_status, body=_response_body, target=_target_name)
def test_http_error_maps_to_defined_error_code(
    status_code: int, body: str, target: str
):
    """For any HTTP status code 400-599, _classify_error should map it to
    one of the defined ErrorCode categories."""
    client = _make_client()
    error = _make_http_error(status_code, body)

    result = client._classify_error(error, target)

    # Result must have a code from the defined ErrorCode enum
    assert result.code in list(ErrorCode), (
        f"Status {status_code} mapped to unknown code: {result.code}"
    )


@settings(max_examples=100)
@given(body=_response_body, target=_target_name)
def test_401_maps_to_auth_error(body: str, target: str):
    """HTTP 401 should always classify as AUTH_ERROR."""
    client = _make_client()
    error = _make_http_error(401, body)

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.AUTH_ERROR, (
        f"Expected AUTH_ERROR for 401, got {result.code}"
    )
    assert result.http_status == 401, (
        f"Expected http_status=401, got {result.http_status}"
    )


@settings(max_examples=100)
@given(status_code=_non_401_status, body=_response_body, target=_target_name)
def test_non_401_http_error_maps_to_api_error_with_status(
    status_code: int, body: str, target: str
):
    """Non-401 HTTP 4xx/5xx errors should classify as API_ERROR with
    http_status set to the status code."""
    client = _make_client()
    error = _make_http_error(status_code, body)

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.API_ERROR, (
        f"Expected API_ERROR for status {status_code}, got {result.code}"
    )
    assert result.http_status == status_code, (
        f"Expected http_status={status_code}, got {result.http_status}"
    )


@settings(max_examples=100)
@given(target=_target_name)
def test_connection_error_maps_to_connection_error(target: str):
    """requests.ConnectionError should classify as CONNECTION_ERROR."""
    client = _make_client()
    error = requests.ConnectionError("Connection refused")

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.CONNECTION_ERROR, (
        f"Expected CONNECTION_ERROR, got {result.code}"
    )


@settings(max_examples=100)
@given(target=_target_name)
def test_ssl_error_maps_to_ssl_error(target: str):
    """requests.exceptions.SSLError should classify as SSL_ERROR."""
    client = _make_client()
    error = requests.exceptions.SSLError("SSL certificate verify failed")

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.SSL_ERROR, (
        f"Expected SSL_ERROR, got {result.code}"
    )


@settings(max_examples=100)
@given(target=_target_name)
def test_timeout_maps_to_timeout_error(target: str):
    """requests.Timeout should classify as TIMEOUT_ERROR."""
    client = _make_client()
    error = requests.Timeout("Request timed out")

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.TIMEOUT_ERROR, (
        f"Expected TIMEOUT_ERROR, got {result.code}"
    )


@settings(max_examples=100)
@given(
    msg=st.text(min_size=1, max_size=50),
    target=_target_name,
)
def test_value_error_maps_to_itsm_error(msg: str, target: str):
    """ValueError should classify as ITSM_ERROR."""
    client = _make_client()
    error = ValueError(msg)

    result = client._classify_error(error, target)

    assert result.code == ErrorCode.ITSM_ERROR, (
        f"Expected ITSM_ERROR for ValueError, got {result.code}"
    )
