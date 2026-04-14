import httpx
import logging
import time
import json
from pathlib import Path

logger = logging.getLogger("lark_cli_lig.api")

# Known Lark error codes with human-readable messages
ERROR_CODES = {
    99991663: "Token invalid or expired. Try: lark-lig auth login",
    99991668: "This endpoint doesn't support the current token type. Try --as user or --as bot",
    99991672: "Missing scope/permission. Check the error details for required scopes",
    99991679: "User token missing permission. Run: lark-lig auth login to re-authorize",
    60006: "Form validation failed. Check field value formats (date=RFC3339, amount=number)",
}

class LarkAPIError(Exception):
    """Lark API error with code and message"""
    def __init__(self, code: int, msg: str, log_id: str = "", details: dict = None):
        self.code = code
        self.msg = msg
        self.log_id = log_id
        self.details = details or {}
        # Add human-readable hint if available
        hint = ERROR_CODES.get(code, "")
        full_msg = f"Lark API Error {code}: {msg}"
        if hint:
            full_msg += f"\n  Hint: {hint}"
        if log_id:
            full_msg += f"\n  Log ID: {log_id}"
        super().__init__(full_msg)

class LarkAPI:
    """HTTP client for Lark Open API with logging and error handling"""

    def __init__(self, domain: str, token_getter):
        """
        domain: e.g. https://open.larksuite.com
        token_getter: callable that returns (token_string, identity_type)
        """
        self.domain = domain.rstrip("/")
        self.token_getter = token_getter
        self.client = httpx.Client(timeout=30.0)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated API request with logging"""
        url = f"{self.domain}{path}"
        token = self.token_getter()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        start = time.monotonic()
        logger.info(f"→ {method} {path}")
        logger.debug(f"  Headers: {_redact_headers(headers)}")
        if "json" in kwargs:
            logger.debug(f"  Body: {json.dumps(kwargs['json'], ensure_ascii=False)[:500]}")

        resp = self.client.request(method, url, headers=headers, **kwargs)
        elapsed = (time.monotonic() - start) * 1000

        logger.info(f"← {resp.status_code} ({elapsed:.0f}ms)")

        data = resp.json()
        logger.debug(f"  Response: {json.dumps(data, ensure_ascii=False)[:500]}")

        code = data.get("code", -1)
        if code != 0:
            raise LarkAPIError(
                code=code,
                msg=data.get("msg", "Unknown error"),
                log_id=data.get("error", {}).get("log_id", ""),
                details=data.get("error", {})
            )

        return data.get("data", data)

    def get(self, path: str, params: dict = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict = None) -> dict:
        return self._request("POST", path, json=body)

    def upload(self, path: str, files: dict, data: dict = None) -> dict:
        """Multipart upload (for images/files)"""
        url = f"{self.domain}{path}"
        token = self.token_getter()

        start = time.monotonic()
        logger.info(f"→ POST (upload) {path}")

        resp = self.client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"← {resp.status_code} ({elapsed:.0f}ms)")

        result = resp.json()
        code = result.get("code", -1)
        if code != 0:
            raise LarkAPIError(
                code=code,
                msg=result.get("msg", "Upload failed"),
                log_id=result.get("error", {}).get("log_id", ""),
            )
        return result.get("data", result)

def _redact_headers(headers: dict) -> dict:
    """Redact sensitive headers for logging"""
    safe = {}
    for k, v in headers.items():
        if k.lower() == "authorization" and len(v) > 15:
            safe[k] = v[:15] + "***"
        else:
            safe[k] = v
    return safe
