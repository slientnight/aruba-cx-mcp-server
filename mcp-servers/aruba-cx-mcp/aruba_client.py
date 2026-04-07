"""REST API client for Aruba CX switches.

Implements session-per-request lifecycle (login → API call → logout),
401 retry with re-authentication, configurable timeout, SSL verification,
error classification, and credential redaction.
"""

import json
import os
import re
import sys
from typing import Optional

import requests

from models import ArubaCxError, ArubaCxTarget, ErrorCode


class ArubaCxException(Exception):
    """Exception wrapper around ArubaCxError for proper raise/catch semantics.

    ArubaCxError is a Pydantic BaseModel and cannot be raised directly.
    This exception carries the structured error data while being a proper
    BaseException subclass.
    """

    def __init__(self, error: ArubaCxError) -> None:
        self.error = error
        super().__init__(error.message)


class ArubaCxClient:
    """HTTP client for Aruba CX AOS-CX REST API.

    Loads target switch configurations from the ARUBA_CX_TARGETS environment
    variable and provides get/post/put/delete methods with session-per-request
    lifecycle management.
    """

    def __init__(self) -> None:
        """Load targets from ARUBA_CX_TARGETS env var or ARUBA_CX_CONFIG file.

        Resolution order:
        1. ARUBA_CX_TARGETS env var (inline JSON array)
        2. ARUBA_CX_CONFIG env var (path to a JSON config file)
        3. aruba-cx-config.json in the current directory

        The config file format is:
        {
          "targets": [
            {"name": "sw1", "host": "10.0.0.1", "username": "admin", "password": "secret"},
            ...
          ],
          "timeout": 30
        }

        Parses JSON, validates each entry with ArubaCxTarget, and skips
        invalid entries with a warning logged to stderr.
        """
        self._targets: dict[str, ArubaCxTarget] = {}
        self._timeout: int = int(os.environ.get("ARUBA_CX_TIMEOUT", "30"))

        entries = self._load_targets()
        for entry in entries:
            try:
                target = ArubaCxTarget(**entry)
                self._targets[target.name] = target
            except Exception as exc:
                print(
                    f"Warning: Skipping invalid target entry: {exc}",
                    file=sys.stderr,
                )

    def _load_targets(self) -> list:
        """Load target entries from env var or config file.

        Returns a list of target dicts (may be empty).
        """
        # 1. Try ARUBA_CX_TARGETS env var (inline JSON)
        raw = os.environ.get("ARUBA_CX_TARGETS", "")
        if raw and raw.strip():
            try:
                entries = json.loads(raw)
                if isinstance(entries, list):
                    return entries
                print(
                    "Warning: ARUBA_CX_TARGETS is not a JSON array, "
                    "trying config file",
                    file=sys.stderr,
                )
            except (json.JSONDecodeError, ValueError):
                print(
                    "Warning: ARUBA_CX_TARGETS contains invalid JSON, "
                    "trying config file",
                    file=sys.stderr,
                )

        # 2. Try ARUBA_CX_CONFIG env var or default file path
        config_path = os.environ.get("ARUBA_CX_CONFIG", "")
        if not config_path:
            # Check for config file in current dir and server dir
            for candidate in ["aruba-cx-config.json",
                              os.path.join(os.path.dirname(__file__), "aruba-cx-config.json")]:
                if os.path.isfile(candidate):
                    config_path = candidate
                    break

        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                # Support both {"targets": [...]} and bare [...]
                if isinstance(config, list):
                    print(f"Loaded {len(config)} targets from {config_path}", file=sys.stderr)
                    return config
                if isinstance(config, dict):
                    # Read timeout from config file if present
                    if "timeout" in config:
                        self._timeout = int(config["timeout"])
                    targets = config.get("targets", config.get("switches", []))
                    if isinstance(targets, list):
                        print(f"Loaded {len(targets)} targets from {config_path}", file=sys.stderr)
                        return targets
                print(
                    f"Warning: Config file {config_path} has unexpected format",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"Warning: Failed to read config file {config_path}: {exc}",
                    file=sys.stderr,
                )

        print(
            "Warning: No targets configured (set ARUBA_CX_TARGETS env var "
            "or create aruba-cx-config.json)",
            file=sys.stderr,
        )
        return []

    def _login(self, target: ArubaCxTarget) -> requests.Session:
        """Authenticate with the switch and return a session with cookie.

        POST to /rest/{api_version}/login with username/password form data.
        Uses HTTPS with SSL verification controlled by target.verify_ssl.
        """
        session = requests.Session()
        session.verify = target.verify_ssl

        url = f"https://{target.host}:{target.port}/rest/{target.api_version}/login"
        response = session.post(
            url,
            data={"username": target.username, "password": target.password},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return session

    def _logout(self, target: ArubaCxTarget, session: requests.Session) -> None:
        """Logout from the switch and close the session.

        POST to /rest/{api_version}/logout, then close the session.
        """
        try:
            url = f"https://{target.host}:{target.port}/rest/{target.api_version}/logout"
            session.post(url, timeout=self._timeout)
        except Exception:
            pass
        finally:
            session.close()

    def _request(
        self,
        target_name: str,
        method: str,
        path: str,
        payload: Optional[dict] = None,
    ) -> dict:
        """Core request method with session-per-request lifecycle.

        Resolves target by name, performs login → API call → logout in
        try/finally. On HTTP 401, retries once with re-authentication.
        On any exception, classifies via _classify_error and raises ArubaCxError.
        """
        target = self._targets.get(target_name)
        if target is None:
            raise ArubaCxException(
                ArubaCxError(
                    code=ErrorCode.CONNECTION_ERROR,
                    message=f"Target '{target_name}' not found",
                    target=target_name,
                )
            )

        session: Optional[requests.Session] = None
        try:
            session = self._login(target)
            base_url = f"https://{target.host}:{target.port}/rest/{target.api_version}"
            url = f"{base_url}{path if path.startswith('/') else '/' + path}"

            response = session.request(
                method=method,
                url=url,
                json=payload if payload is not None else None,
                timeout=self._timeout,
            )

            # On 401, retry once with re-auth
            if response.status_code == 401:
                self._logout(target, session)
                session = self._login(target)
                response = session.request(
                    method=method,
                    url=url,
                    json=payload if payload is not None else None,
                    timeout=self._timeout,
                )
                if response.status_code == 401:
                    raise ArubaCxException(
                        ArubaCxError(
                            code=ErrorCode.AUTH_ERROR,
                            message=f"Authentication failed for target '{target_name}' after retry",
                            target=target_name,
                            http_status=401,
                        )
                    )

            response.raise_for_status()

            # Parse response body
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                text = response.text
                return json.loads(text) if text.strip() else {}
            else:
                text = response.text
                return {"data": text} if text.strip() else {}

        except ArubaCxException:
            raise
        except Exception as exc:
            raise ArubaCxException(self._classify_error(exc, target_name))
        finally:
            if session is not None:
                self._logout(target, session)

    def get(self, target_name: str, path: str) -> dict:
        """GET request with session-per-request lifecycle."""
        return self._request(target_name, "GET", path)

    def post(
        self, target_name: str, path: str, payload: Optional[dict] = None
    ) -> dict:
        """POST request with session-per-request lifecycle."""
        return self._request(target_name, "POST", path, payload)

    def put(
        self, target_name: str, path: str, payload: Optional[dict] = None
    ) -> dict:
        """PUT request with session-per-request lifecycle."""
        return self._request(target_name, "PUT", path, payload)

    def patch(
        self, target_name: str, path: str, payload: Optional[dict] = None
    ) -> dict:
        """PATCH request with session-per-request lifecycle."""
        return self._request(target_name, "PATCH", path, payload)

    def delete(self, target_name: str, path: str) -> dict:
        """DELETE request with session-per-request lifecycle."""
        return self._request(target_name, "DELETE", path)

    def list_targets(self) -> list[dict]:
        """Return all configured targets without credentials.

        Excludes username and password from the output.
        """
        result = []
        for target in self._targets.values():
            result.append(
                {
                    "name": target.name,
                    "host": target.host,
                    "port": target.port,
                    "api_version": target.api_version,
                    "verify_ssl": target.verify_ssl,
                }
            )
        return result

    def _classify_error(
        self, error: Exception, target_name: str
    ) -> ArubaCxError:
        """Map exceptions to ArubaCxError with appropriate error codes.

        Classification:
        - requests.ConnectionError → CONNECTION_ERROR
        - requests.exceptions.SSLError → SSL_ERROR
        - requests.Timeout → TIMEOUT_ERROR
        - HTTP 401 → AUTH_ERROR
        - Other HTTP errors → API_ERROR (include status code and sanitized body)
        - ValueError (ITSM) → ITSM_ERROR
        - Other → CONNECTION_ERROR
        """
        # SSLError is a subclass of ConnectionError, so check it first
        if isinstance(error, requests.exceptions.SSLError):
            return ArubaCxError(
                code=ErrorCode.SSL_ERROR,
                message=f"SSL error connecting to target '{target_name}': {self._redact(str(error))}",
                target=target_name,
            )

        if isinstance(error, requests.ConnectionError):
            return ArubaCxError(
                code=ErrorCode.CONNECTION_ERROR,
                message=f"Connection error to target '{target_name}': {self._redact(str(error))}",
                target=target_name,
            )

        if isinstance(error, requests.Timeout):
            return ArubaCxError(
                code=ErrorCode.TIMEOUT_ERROR,
                message=f"Request to target '{target_name}' timed out",
                target=target_name,
            )

        if isinstance(error, requests.HTTPError):
            response = error.response
            if response is not None:
                status_code = response.status_code
                if status_code == 401:
                    return ArubaCxError(
                        code=ErrorCode.AUTH_ERROR,
                        message=f"Authentication failed for target '{target_name}'",
                        target=target_name,
                        http_status=status_code,
                    )
                body = self._redact(response.text[:500]) if response.text else ""
                return ArubaCxError(
                    code=ErrorCode.API_ERROR,
                    message=f"API error from target '{target_name}': HTTP {status_code}",
                    target=target_name,
                    details=body,
                    http_status=status_code,
                )

        if isinstance(error, ValueError):
            return ArubaCxError(
                code=ErrorCode.ITSM_ERROR,
                message=f"ITSM validation error: {error}",
                target=target_name,
            )

        return ArubaCxError(
            code=ErrorCode.CONNECTION_ERROR,
            message=f"Unexpected error for target '{target_name}': {self._redact(str(error))}",
            target=target_name,
        )

    @staticmethod
    def _redact(text: str) -> str:
        """Strip sensitive data from text.

        Removes:
        - Password values: "password": "..." → "password": "***"
        - Bearer tokens: Bearer xxx → Bearer ***
        - PEM certificate blocks: -----BEGIN...-----END... → [REDACTED CERTIFICATE]
        """
        # Redact password values in JSON-like strings
        result = re.sub(
            r'("password"\s*:\s*)"[^"]*"',
            r'\1"***"',
            text,
            flags=re.IGNORECASE,
        )

        # Redact Bearer tokens
        result = re.sub(
            r"Bearer\s+\S+",
            "Bearer ***",
            result,
            flags=re.IGNORECASE,
        )

        # Redact PEM certificate blocks
        result = re.sub(
            r"-----BEGIN[^-]*-----[\s\S]*?-----END[^-]*-----",
            "[REDACTED CERTIFICATE]",
            result,
        )

        return result
