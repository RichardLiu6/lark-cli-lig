"""Authentication module: manages tenant and user access tokens with keyring storage."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any

import click
import httpx
import keyring
import keyring.errors

from .config import APP_ID, APP_SECRET, DOMAIN

logger = logging.getLogger("lark_cli_lig.auth")

# ---------------------------------------------------------------------------
# OAuth callback handler
# ---------------------------------------------------------------------------

_auth_code: str | None = None
_auth_event = Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth authorization code from the redirect."""

    def do_GET(self) -> None:  # noqa: N802
        global _auth_code
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _auth_code = qs.get("code", [None])[0]  # type: ignore[assignment]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if _auth_code:
            self.wfile.write(
                b"<h2>Authorization successful! You can close this tab.</h2>"
            )
        else:
            self.wfile.write(
                b"<h2>Authorization failed: no code received.</h2>"
            )
        _auth_event.set()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default stderr logging from the HTTP server.
        pass


# ---------------------------------------------------------------------------
# TokenManager
# ---------------------------------------------------------------------------


class TokenManager:
    """Manages both tenant and user access tokens with keyring storage."""

    TENANT_KEY = "lark-cli-lig-tenant-token"
    USER_KEY = "lark-cli-lig-user-token"
    SERVICE_NAME = "lark-cli-lig"

    REDIRECT_URI = "http://localhost:9876/callback"
    CALLBACK_PORT = 9876

    DEFAULT_SCOPES = [
        "approval:approval",
        "approval:approval:readonly",
        "approval:definition",
        # send-file 需要；app 只开通了经典版 im:resource，
        # 不要换成细粒度 im:resource:upload（授权页会报 20027）
        "im:resource",
    ]

    def __init__(
        self,
        app_id: str = APP_ID,
        app_secret: str = APP_SECRET,
        domain: str = DOMAIN,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain.rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_token(self, identity: str = "bot") -> str:
        """Return access token based on *identity*.

        ``"bot"`` (default) returns a tenant_access_token.
        ``"user"`` returns a user_access_token (OAuth required).
        """
        if identity == "user":
            return self.get_user_token()
        return self.get_tenant_token()

    # ── Tenant token ──────────────────────────────────────────────────

    def get_tenant_token(self) -> str:
        """Get / refresh the tenant_access_token.  Cached in keyring with expiry."""
        cached = self._keyring_get(self.TENANT_KEY)
        if cached:
            try:
                data = json.loads(cached)
                if data.get("expires_at", 0) > time.time():
                    logger.debug("Using cached tenant token (expires_at=%s)", data["expires_at"])
                    return data["token"]
                logger.info("Tenant token expired, refreshing")
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt tenant token cache, fetching new token")

        # Fetch a new tenant token
        url = f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}

        with httpx.Client() as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        logger.debug("tenant_access_token response: %s", body)

        if body.get("code") != 0:
            raise click.ClickException(
                f"Failed to get tenant token: {body.get('msg', body)}"
            )

        token: str = body["tenant_access_token"]
        expires_in: int = body.get("expire", 7200)  # default 2 h
        store = json.dumps(
            {"token": token, "expires_at": time.time() + expires_in - 60}
        )
        self._keyring_set(self.TENANT_KEY, store)
        logger.info("Tenant token fetched and cached (expires_in=%ds)", expires_in)
        return token

    # ── User token ────────────────────────────────────────────────────

    def get_user_token(self) -> str:
        """Return user_access_token from keyring.  Tries refresh if expired."""
        cached = self._keyring_get(self.USER_KEY)
        if not cached:
            raise click.ClickException("Not logged in. Run: lark-lig auth login")

        try:
            data = json.loads(cached)
        except json.JSONDecodeError:
            raise click.ClickException("Corrupt user token cache. Run: lark-lig auth login")

        # Still valid?
        if data.get("expires_at", 0) > time.time():
            logger.debug("Using cached user token (expires_at=%s)", data["expires_at"])
            return data["token"]

        # Try refresh
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise click.ClickException(
                "User token expired and no refresh_token available. Run: lark-lig auth login"
            )

        logger.info("User token expired, attempting refresh")
        try:
            return self.refresh_user_token(refresh_token)
        except Exception as exc:
            logger.warning("Refresh failed (%s), re-login required", exc)
            raise click.ClickException(
                f"Token refresh failed: {exc}\nRun: lark-lig auth login"
            )

    def refresh_user_token(self, refresh_token: str | None = None) -> str:
        """Use *refresh_token* to obtain a new user_access_token."""
        if refresh_token is None:
            cached = self._keyring_get(self.USER_KEY)
            if not cached:
                raise click.ClickException("Not logged in. Run: lark-lig auth login")
            data = json.loads(cached)
            refresh_token = data.get("refresh_token")
            if not refresh_token:
                raise click.ClickException(
                    "No refresh_token stored. Run: lark-lig auth login"
                )

        # Need an app_access_token to call the refresh endpoint
        app_token = self._get_app_access_token()

        url = f"{self.domain}/open-apis/authen/v1/oidc/refresh_access_token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        with httpx.Client() as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()
            body = resp.json()

        logger.debug("refresh_access_token response: %s", body)

        if body.get("code") != 0:
            raise RuntimeError(f"Refresh failed: {body.get('msg', body)}")

        token_data = body["data"]
        self._store_user_token(token_data)
        logger.info("User token refreshed successfully")
        return token_data["access_token"]

    # ── OAuth login flow ──────────────────────────────────────────────

    def oauth_login(self, scopes: list[str] | None = None) -> dict:
        """Run the full OAuth flow: browser -> callback -> exchange code -> store."""
        global _auth_code
        _auth_code = None
        _auth_event.clear()

        scopes = scopes or self.DEFAULT_SCOPES
        scope_str = " ".join(scopes)

        # 1. Get app_access_token (needed to exchange the code)
        app_token = self._get_app_access_token()

        # 2. Start local callback server
        server = HTTPServer(("localhost", self.CALLBACK_PORT), _CallbackHandler)
        logger.info("OAuth callback server listening on port %d", self.CALLBACK_PORT)

        # 3. Build authorize URL and open browser
        auth_url = (
            f"{self.domain}/open-apis/authen/v1/authorize"
            f"?app_id={self.app_id}"
            f"&redirect_uri={urllib.parse.quote(self.REDIRECT_URI)}"
            f"&state=lark_oauth"
            f"&scope={urllib.parse.quote(scope_str)}"
        )
        click.echo(f"Opening browser for authorization...")
        click.echo(f"  URL: {auth_url}")
        webbrowser.open(auth_url)

        # 4. Wait for callback
        click.echo("Waiting for authorization callback...")
        server.handle_request()
        server.server_close()

        if not _auth_code:
            raise click.ClickException("Authorization failed: no code received.")

        logger.info("Authorization code received")

        # 5. Exchange code for user_access_token
        url = f"{self.domain}/open-apis/authen/v1/oidc/access_token"
        payload = {
            "grant_type": "authorization_code",
            "code": _auth_code,
        }

        with httpx.Client() as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()
            body = resp.json()

        logger.debug("access_token exchange response: %s", body)

        if body.get("code") != 0:
            raise click.ClickException(
                f"Token exchange failed: {body.get('msg', body)}"
            )

        token_data: dict = body["data"]

        # 6. Store in keyring
        self._store_user_token(token_data)
        logger.info(
            "User logged in as %s (open_id=%s)",
            token_data.get("name", "N/A"),
            token_data.get("open_id", "N/A"),
        )

        # 7. Return user info
        return {
            "name": token_data.get("name"),
            "open_id": token_data.get("open_id"),
            "user_id": token_data.get("user_id"),
            "email": token_data.get("email"),
            "expires_in": token_data.get("expires_in"),
            "refresh_expires_in": token_data.get("refresh_expires_in"),
        }

    # ── Current user info ───────────────────────────────────────────

    def get_current_open_id(self) -> str:
        """Return the open_id of the logged-in user, or raise with helpful message."""
        cached = self._keyring_get(self.USER_KEY)
        if cached:
            try:
                data = json.loads(cached)
                open_id = data.get("open_id")
                if open_id:
                    return open_id
            except (json.JSONDecodeError, KeyError):
                pass

        raise click.ClickException(
            "Cannot determine your open_id. Please login first:\n"
            "  lark auth login\n"
            "\n"
            "This will open a browser for Lark OAuth and store your identity."
        )

    # ── Logout ────────────────────────────────────────────────────────

    def logout(self) -> None:
        """Remove all stored tokens from keyring."""
        for key in (self.TENANT_KEY, self.USER_KEY):
            try:
                self._keyring_delete(key)
                logger.info("Removed %s from keyring", key)
            except Exception:
                pass  # already absent
        click.echo("Logged out. All tokens removed.")

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return current auth status information."""
        result: dict[str, Any] = {
            "app_id": self.app_id,
            "domain": self.domain,
            "tenant_token_valid": False,
            "tenant_token_expires_at": None,
            "user_logged_in": False,
            "user_name": None,
            "user_open_id": None,
            "user_token_expires_at": None,
            "user_refresh_expires_at": None,
        }

        # Tenant token
        cached = self._keyring_get(self.TENANT_KEY)
        if cached:
            try:
                data = json.loads(cached)
                expires_at = data.get("expires_at", 0)
                result["tenant_token_valid"] = expires_at > time.time()
                result["tenant_token_expires_at"] = expires_at
            except (json.JSONDecodeError, KeyError):
                pass

        # User token
        cached = self._keyring_get(self.USER_KEY)
        if cached:
            try:
                data = json.loads(cached)
                expires_at = data.get("expires_at", 0)
                result["user_logged_in"] = True
                result["user_name"] = data.get("name")
                result["user_open_id"] = data.get("open_id")
                result["user_token_expires_at"] = expires_at
                result["user_refresh_expires_at"] = data.get("refresh_expires_at")
                result["user_token_active"] = expires_at > time.time()
            except (json.JSONDecodeError, KeyError):
                pass

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_app_access_token(self) -> str:
        """Fetch a short-lived app_access_token (needed for OAuth endpoints)."""
        url = f"{self.domain}/open-apis/auth/v3/app_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}

        with httpx.Client() as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        logger.debug("app_access_token response: %s", body)

        if body.get("code") != 0:
            raise click.ClickException(
                f"Failed to get app_access_token: {body.get('msg', body)}"
            )

        return body["app_access_token"]

    def _store_user_token(self, token_data: dict) -> None:
        """Persist user token data to keyring."""
        now = time.time()
        store = json.dumps({
            "token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": now + token_data.get("expires_in", 7200) - 60,
            "refresh_expires_at": now + token_data.get("refresh_expires_in", 2592000) - 60,
            "name": token_data.get("name"),
            "open_id": token_data.get("open_id"),
            "user_id": token_data.get("user_id"),
            "email": token_data.get("email"),
        })
        self._keyring_set(self.USER_KEY, store)

    # ── Keyring with file-based fallback ──────────────────────────────

    def _keyring_set(self, key: str, value: str) -> None:
        try:
            keyring.set_password(self.SERVICE_NAME, key, value)
        except keyring.errors.NoKeyringError:
            logger.debug("No keyring backend, falling back to file storage")
            path = Path.home() / ".lark-cli-lig" / "credentials" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            path.chmod(0o600)

    def _keyring_get(self, key: str) -> str | None:
        try:
            value = keyring.get_password(self.SERVICE_NAME, key)
            if value is not None:
                return value
        except keyring.errors.NoKeyringError:
            logger.debug("No keyring backend, falling back to file storage")

        # Fallback: try file
        path = Path.home() / ".lark-cli-lig" / "credentials" / key
        if path.exists():
            return path.read_text()
        return None

    def _keyring_delete(self, key: str) -> None:
        try:
            keyring.delete_password(self.SERVICE_NAME, key)
        except keyring.errors.NoKeyringError:
            pass
        except keyring.errors.PasswordDeleteError:
            pass

        # Also clean up fallback file if present
        path = Path.home() / ".lark-cli-lig" / "credentials" / key
        if path.exists():
            path.unlink()
