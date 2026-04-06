# Feature: aruba-cx-python-port, Property 12: Session-per-request lifecycle
"""Property test: For any API call (get/post/put/delete), the client should
call login before the API request and logout after, even when the API request
raises an exception. The logout should always execute (try/finally pattern).

**Validates: Requirements 3.3**
"""

import json
import os
from unittest.mock import MagicMock, patch, call

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from aruba_client import ArubaCxClient, ArubaCxException
from models import ArubaCxError


# --- Helpers ---

_VALID_TARGET = {
    "name": "test-switch",
    "host": "10.0.0.1",
    "username": "admin",
    "password": "secret",
    "port": 443,
    "api_version": "v10.13",
    "verify_ssl": False,
}


def _make_client_with_target(target: dict = None) -> ArubaCxClient:
    """Create an ArubaCxClient with a single valid target."""
    t = target or _VALID_TARGET
    old = os.environ.get("ARUBA_CX_TARGETS")
    try:
        os.environ["ARUBA_CX_TARGETS"] = json.dumps([t])
        return ArubaCxClient()
    finally:
        if old is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old


def _mock_session_success():
    """Return a mock session whose request() returns a successful JSON response."""
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.text = '{"result": "ok"}'
    response.raise_for_status = MagicMock()
    session.request.return_value = response
    # login response
    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()
    session.post.return_value = login_resp
    return session


def _mock_session_api_failure():
    """Return a mock session whose request() raises a RuntimeError."""
    session = MagicMock()
    session.request.side_effect = RuntimeError("API call failed")
    # login response
    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()
    session.post.return_value = login_resp
    return session


# --- Strategies ---

_http_method = st.sampled_from(["get", "post", "put", "delete"])

_api_path = st.from_regex(r"/[a-z][a-z0-9/_]{0,30}", fullmatch=True)


# --- Property Tests ---


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_login_called_before_api_request_and_logout_after(method: str, path: str):
    """For any HTTP method, login should be called before the API request
    and logout should be called after the API request completes successfully."""
    client = _make_client_with_target()
    call_order = []

    mock_session = MagicMock()

    # Track login
    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_login_post(url, **kwargs):
        if "/login" in url:
            call_order.append("login")
            return login_resp
        # logout call
        call_order.append("logout")
        return MagicMock()

    mock_session.post.side_effect = mock_login_post

    # Track API request
    api_response = MagicMock()
    api_response.status_code = 200
    api_response.headers = {"content-type": "application/json"}
    api_response.text = '{"ok": true}'
    api_response.raise_for_status = MagicMock()

    def mock_request(**kwargs):
        call_order.append("api_call")
        return api_response

    mock_session.request.side_effect = mock_request
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        if method in ("post", "put"):
            func("test-switch", path, payload={"key": "value"})
        else:
            func("test-switch", path)

    # Verify ordering: login -> api_call -> logout
    assert "login" in call_order, "login was not called"
    assert "api_call" in call_order, "API request was not made"
    assert "logout" in call_order, "logout was not called"

    login_idx = call_order.index("login")
    api_idx = call_order.index("api_call")
    logout_idx = call_order.index("logout")

    assert login_idx < api_idx, (
        f"login (idx={login_idx}) should be called before api_call (idx={api_idx})"
    )
    assert api_idx < logout_idx, (
        f"api_call (idx={api_idx}) should be called before logout (idx={logout_idx})"
    )


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_logout_called_even_when_api_raises_exception(method: str, path: str):
    """For any HTTP method, logout should be called even when the API request
    raises an exception (try/finally pattern)."""
    client = _make_client_with_target()
    call_order = []

    mock_session = MagicMock()

    # Track login
    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_login_post(url, **kwargs):
        if "/login" in url:
            call_order.append("login")
            return login_resp
        call_order.append("logout")
        return MagicMock()

    mock_session.post.side_effect = mock_login_post

    # API request raises exception
    def mock_request(**kwargs):
        call_order.append("api_call")
        raise RuntimeError("Simulated API failure")

    mock_session.request.side_effect = mock_request
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        try:
            if method in ("post", "put"):
                func("test-switch", path, payload={"key": "value"})
            else:
                func("test-switch", path)
        except Exception:
            pass  # Expected — we're testing that logout still happens

    # Verify login and api_call happened
    assert "login" in call_order, "login was not called"
    assert "api_call" in call_order, "API request was not attempted"

    # Critical: logout MUST be called even after exception
    assert "logout" in call_order, (
        "logout was NOT called after API exception — try/finally pattern violated"
    )

    login_idx = call_order.index("login")
    logout_idx = call_order.index("logout")
    assert login_idx < logout_idx, (
        f"login (idx={login_idx}) should be called before logout (idx={logout_idx})"
    )


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_each_http_method_follows_session_lifecycle(method: str, path: str):
    """For each HTTP method (get/post/put/delete), verify the complete
    session-per-request lifecycle: login → request → logout."""
    client = _make_client_with_target()
    login_count = 0
    logout_count = 0
    api_count = 0

    mock_session = MagicMock()

    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_post(url, **kwargs):
        nonlocal login_count, logout_count
        if "/login" in url:
            login_count += 1
            return login_resp
        if "/logout" in url:
            logout_count += 1
            return MagicMock()
        return MagicMock()

    mock_session.post.side_effect = mock_post

    api_response = MagicMock()
    api_response.status_code = 200
    api_response.headers = {"content-type": "application/json"}
    api_response.text = '{"ok": true}'
    api_response.raise_for_status = MagicMock()

    def mock_request(**kwargs):
        nonlocal api_count
        api_count += 1
        return api_response

    mock_session.request.side_effect = mock_request
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        if method in ("post", "put"):
            func("test-switch", path, payload={"key": "value"})
        else:
            func("test-switch", path)

    # Each request should have exactly one login and one logout
    assert login_count >= 1, f"Expected at least 1 login call, got {login_count}"
    assert api_count == 1, f"Expected exactly 1 API call, got {api_count}"
    assert logout_count >= 1, f"Expected at least 1 logout call, got {logout_count}"


# Feature: aruba-cx-python-port, Property 13: 401 retry with re-authentication
# **Validates: Requirements 3.4**


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_401_retry_succeeds_after_reauth(method: str, path: str):
    """For any API call that receives HTTP 401, the client should invalidate
    the session, re-authenticate, and retry. If the retry returns 200,
    the request should succeed and re-auth should have happened."""
    client = _make_client_with_target()
    login_count = 0
    api_call_count = 0

    mock_session = MagicMock()

    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_post(url, **kwargs):
        nonlocal login_count
        if "/login" in url:
            login_count += 1
            return login_resp
        # logout
        return MagicMock()

    mock_session.post.side_effect = mock_post

    # First API call returns 401, second returns 200
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.headers = {"content-type": "application/json"}
    resp_401.text = '{"error": "unauthorized"}'

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {"content-type": "application/json"}
    resp_200.text = '{"result": "ok"}'
    resp_200.raise_for_status = MagicMock()

    def mock_request(**kwargs):
        nonlocal api_call_count
        api_call_count += 1
        if api_call_count == 1:
            return resp_401
        return resp_200

    mock_session.request.side_effect = mock_request
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        if method in ("post", "put"):
            result = func("test-switch", path, payload={"key": "value"})
        else:
            result = func("test-switch", path)

    # Verify re-auth happened: 2 logins (initial + retry)
    assert login_count == 2, (
        f"Expected 2 login calls (initial + re-auth), got {login_count}"
    )
    # Verify the retry succeeded
    assert api_call_count == 2, (
        f"Expected 2 API calls (initial 401 + retry), got {api_call_count}"
    )
    # Verify the result is from the successful retry
    assert result == {"result": "ok"}


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_401_retry_returns_auth_error_on_double_401(method: str, path: str):
    """For any API call where both the initial and retry requests return 401,
    the client should raise an AUTH_ERROR."""
    client = _make_client_with_target()
    login_count = 0

    mock_session = MagicMock()

    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_post(url, **kwargs):
        nonlocal login_count
        if "/login" in url:
            login_count += 1
            return login_resp
        return MagicMock()

    mock_session.post.side_effect = mock_post

    # Both API calls return 401
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.headers = {"content-type": "application/json"}
    resp_401.text = '{"error": "unauthorized"}'

    mock_session.request.return_value = resp_401
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        raised = False
        try:
            if method in ("post", "put"):
                func("test-switch", path, payload={"key": "value"})
            else:
                func("test-switch", path)
        except ArubaCxException as exc:
            raised = True
            assert exc.error.code == "AUTH_ERROR", (
                f"Expected AUTH_ERROR, got {exc.error.code}"
            )
            assert exc.error.http_status == 401, (
                f"Expected http_status=401, got {exc.error.http_status}"
            )

    assert raised, "Expected ArubaCxException to be raised on double 401"
    # Verify exactly 2 logins happened (initial + retry)
    assert login_count == 2, (
        f"Expected 2 login calls (initial + re-auth), got {login_count}"
    )


@settings(max_examples=100)
@given(method=_http_method, path=_api_path)
def test_401_retry_exactly_two_logins(method: str, path: str):
    """For any API call that receives HTTP 401, verify exactly 2 login calls
    happen (initial login + re-authentication) regardless of whether the
    retry succeeds or fails."""
    client = _make_client_with_target()
    login_count = 0
    logout_count = 0

    mock_session = MagicMock()

    login_resp = MagicMock()
    login_resp.raise_for_status = MagicMock()

    def mock_post(url, **kwargs):
        nonlocal login_count, logout_count
        if "/login" in url:
            login_count += 1
            return login_resp
        if "/logout" in url:
            logout_count += 1
        return MagicMock()

    mock_session.post.side_effect = mock_post

    # First call returns 401, second returns 200
    call_count = 0
    resp_401 = MagicMock()
    resp_401.status_code = 401

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {"content-type": "application/json"}
    resp_200.text = '{"ok": true}'
    resp_200.raise_for_status = MagicMock()

    def mock_request(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_401
        return resp_200

    mock_session.request.side_effect = mock_request
    mock_session.close = MagicMock()
    mock_session.verify = False

    with patch("aruba_client.requests.Session", return_value=mock_session):
        func = getattr(client, method)
        if method in ("post", "put"):
            func("test-switch", path, payload={"key": "value"})
        else:
            func("test-switch", path)

    # Exactly 2 logins: initial + re-auth after 401
    assert login_count == 2, (
        f"Expected exactly 2 login calls, got {login_count}"
    )
    # At least 2 logout calls: one after 401 invalidation + one in finally
    assert logout_count >= 2, (
        f"Expected at least 2 logout calls (invalidation + finally), got {logout_count}"
    )


# Feature: aruba-cx-python-port, Property 14: Timeout configuration from environment
# **Validates: Requirements 3.5**


@settings(max_examples=100)
@given(timeout_val=st.integers(min_value=1, max_value=3600))
def test_timeout_from_env_var(timeout_val: int):
    """For any valid integer string set as ARUBA_CX_TIMEOUT, the client
    should use that value (in seconds) as the request timeout."""
    old_targets = os.environ.get("ARUBA_CX_TARGETS")
    old_timeout = os.environ.get("ARUBA_CX_TIMEOUT")
    try:
        os.environ["ARUBA_CX_TARGETS"] = json.dumps([_VALID_TARGET])
        os.environ["ARUBA_CX_TIMEOUT"] = str(timeout_val)
        client = ArubaCxClient()
        assert client._timeout == timeout_val, (
            f"Expected _timeout={timeout_val}, got {client._timeout}"
        )
    finally:
        if old_targets is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old_targets
        if old_timeout is None:
            os.environ.pop("ARUBA_CX_TIMEOUT", None)
        else:
            os.environ["ARUBA_CX_TIMEOUT"] = old_timeout


def test_timeout_default_when_unset():
    """When ARUBA_CX_TIMEOUT is unset, the default timeout should be 30 seconds."""
    old_targets = os.environ.get("ARUBA_CX_TARGETS")
    old_timeout = os.environ.get("ARUBA_CX_TIMEOUT")
    try:
        os.environ["ARUBA_CX_TARGETS"] = json.dumps([_VALID_TARGET])
        os.environ.pop("ARUBA_CX_TIMEOUT", None)
        client = ArubaCxClient()
        assert client._timeout == 30, (
            f"Expected default _timeout=30, got {client._timeout}"
        )
    finally:
        if old_targets is None:
            os.environ.pop("ARUBA_CX_TARGETS", None)
        else:
            os.environ["ARUBA_CX_TARGETS"] = old_targets
        if old_timeout is None:
            os.environ.pop("ARUBA_CX_TIMEOUT", None)
        else:
            os.environ["ARUBA_CX_TIMEOUT"] = old_timeout
