"""PKCE OAuth token acquisition for the substrate.office.com API.

This is the ADR-0008 evolution path away from browser (CDP) token scraping: a
standard Microsoft identity platform OAuth 2.0 Authorization Code + PKCE (and
Device Code) flow that yields a ``refresh_token`` via the ``offline_access``
scope, enabling browserless, long-lived automatic renewal.

The module is intentionally free of any framework/CLI coupling so it can be unit
tested by injecting an ``httpx.Client`` backed by a mock transport. The higher
level CLI commands wire it to ``.env`` / the token cache.

Security note: a ``refresh_token`` grants long-lived access and is more sensitive
than a ~1h ``access_token``. The on-disk cache is written with best-effort
owner-only permissions; treat the cache file like a password.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .token_store import decode_jwt_payload

# Reverse-engineered first-party Office web Copilot public client (see ADR-0008).
# This client is registered with the "nativeclient" redirect and is allowed to
# request the substrate/sydney scopes through the public PKCE flow.
DEFAULT_CLIENT_ID = "c0ab8ce9-e9a0-42e7-b064-33d422df41f1"
DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"
DEFAULT_SCOPE = (
    "openid profile offline_access "
    "https://substrate.office.com/sydney/M365Chat.Read "
    "https://substrate.office.com/sydney/sydney.readwrite"
)
DEFAULT_REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"


class OAuthError(RuntimeError):
    """Raised when a token endpoint returns an OAuth error response."""

    def __init__(self, error: str, description: str = "") -> None:
        self.error = error
        self.description = description
        super().__init__(f"{error}: {description}" if description else error)


# ---------------------------------------------------------------------------
# PKCE primitives
# ---------------------------------------------------------------------------
def generate_pkce_verifier() -> str:
    """Return a high-entropy RFC 7636 code_verifier (43-char base64url)."""

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    """Return the S256 code_challenge for ``verifier``."""

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str = DEFAULT_CLIENT_ID
    authority: str = DEFAULT_AUTHORITY
    scope: str = DEFAULT_SCOPE
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.authority.rstrip('/')}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority.rstrip('/')}/oauth2/v2.0/token"

    @property
    def devicecode_endpoint(self) -> str:
        return f"{self.authority.rstrip('/')}/oauth2/v2.0/devicecode"


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    scope: str = ""
    expires_at: float = 0.0
    account: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, skew_seconds: int = 30) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)

    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "scope": self.scope,
            "expires_at": self.expires_at,
            "account": self.account,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenSet":
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            id_token=str(data.get("id_token", "")),
            scope=str(data.get("scope", "")),
            expires_at=float(data.get("expires_at", 0.0) or 0.0),
            account=dict(data.get("account", {}) or {}),
        )

    @classmethod
    def from_token_response(
        cls, data: dict[str, Any], *, previous: "TokenSet | None" = None
    ) -> "TokenSet":
        now = time.time()
        expires_in = float(data.get("expires_in", 0) or 0)
        # A refresh response may omit the refresh_token; keep the previous one.
        refresh = str(data.get("refresh_token", "") or (previous.refresh_token if previous else ""))
        id_token = str(data.get("id_token", "") or (previous.id_token if previous else ""))
        account = _account_from_claims(data.get("access_token", ""), id_token)
        if not account and previous:
            account = previous.account
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=refresh,
            id_token=id_token,
            scope=str(data.get("scope", "") or (previous.scope if previous else "")),
            expires_at=now + expires_in if expires_in else now,
            account=account,
        )


def _account_from_claims(access_token: str, id_token: str) -> dict[str, Any]:
    for token in (id_token, access_token):
        if not token:
            continue
        try:
            claims = decode_jwt_payload(token)
        except Exception:
            continue
        account = {
            "email": claims.get("preferred_username") or claims.get("upn") or claims.get("email"),
            "name": claims.get("name"),
            "oid": claims.get("oid"),
            "tid": claims.get("tid"),
        }
        if any(account.values()):
            return {k: v for k, v in account.items() if v}
    return {}


# ---------------------------------------------------------------------------
# Authorization Code + PKCE
# ---------------------------------------------------------------------------
def build_authorization_url(config: OAuthConfig, verifier: str, state: str | None = None) -> str:
    """Build the browser authorize URL for an Authorization Code + PKCE login."""

    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "scope": config.scope,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state or secrets.token_urlsafe(16),
        "prompt": "select_account",
    }
    return f"{config.authorize_endpoint}?{urllib.parse.urlencode(params)}"


def parse_redirect_code(redirect: str) -> str:
    """Extract the ``code`` from a pasted redirect URL (or return it verbatim)."""

    redirect = redirect.strip()
    if "?" not in redirect and "code=" not in redirect and "error=" not in redirect:
        # Assume the user pasted just the bare authorization code.
        return redirect
    parsed = urllib.parse.urlparse(redirect)
    query = urllib.parse.parse_qs(parsed.query)
    if "error" in query:
        raise OAuthError(query["error"][0], (query.get("error_description") or [""])[0])
    code = query.get("code", [""])[0]
    if not code:
        raise OAuthError("invalid_request", "no authorization code found in redirect URL")
    return code


def exchange_code(
    config: OAuthConfig, code: str, verifier: str, *, client: httpx.Client | None = None
) -> TokenSet:
    data = {
        "client_id": config.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "code_verifier": verifier,
        "scope": config.scope,
    }
    return _post_token(config, data, client=client)


# ---------------------------------------------------------------------------
# Device Code
# ---------------------------------------------------------------------------
def start_device_code(config: OAuthConfig, *, client: httpx.Client | None = None) -> dict[str, Any]:
    owns = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            config.devicecode_endpoint,
            data={"client_id": config.client_id, "scope": config.scope},
        )
        payload = resp.json()
        if resp.status_code >= 400 or "error" in payload:
            raise OAuthError(payload.get("error", "device_code_error"), payload.get("error_description", ""))
        return payload
    finally:
        if owns:
            client.close()


def poll_device_code(
    config: OAuthConfig,
    device: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    sleep_func: Any = time.sleep,
    now_func: Any = time.time,
) -> TokenSet:
    owns = client is None
    client = client or httpx.Client(timeout=30)
    interval = int(device.get("interval", 5) or 5)
    expires_in = int(device.get("expires_in", 900) or 900)
    deadline = now_func() + expires_in
    try:
        while now_func() < deadline:
            sleep_func(interval)
            resp = client.post(
                config.token_endpoint,
                data={
                    "client_id": config.client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device["device_code"],
                },
            )
            payload = resp.json()
            error = payload.get("error")
            if not error:
                return TokenSet.from_token_response(payload)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise OAuthError(error, payload.get("error_description", ""))
        raise OAuthError("expired_token", "device code expired before authorization")
    finally:
        if owns:
            client.close()


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def refresh_access_token(
    config: OAuthConfig,
    refresh_token: str,
    *,
    previous: TokenSet | None = None,
    client: httpx.Client | None = None,
) -> TokenSet:
    if not refresh_token:
        raise OAuthError("invalid_grant", "no refresh_token available")
    data = {
        "client_id": config.client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": config.scope,
    }
    return _post_token(config, data, previous=previous, client=client)


def _post_token(
    config: OAuthConfig,
    data: dict[str, str],
    *,
    previous: TokenSet | None = None,
    client: httpx.Client | None = None,
) -> TokenSet:
    owns = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(config.token_endpoint, data=data)
        payload = resp.json()
        if resp.status_code >= 400 or "error" in payload:
            raise OAuthError(payload.get("error", "token_error"), payload.get("error_description", ""))
        return TokenSet.from_token_response(payload, previous=previous)
    finally:
        if owns:
            client.close()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
class TokenCache:
    """Owner-only JSON cache for the current :class:`TokenSet`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> TokenSet | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not data.get("access_token") and not data.get("refresh_token"):
            return None
        return TokenSet.from_dict(data)

    def save(self, token_set: TokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(token_set.to_dict(), indent=2)
        self.path.write_text(text, encoding="utf-8")
        _restrict_permissions(self.path)


def _restrict_permissions(path: Path) -> None:
    """Best-effort owner-only permissions for the token cache."""

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        try:
            user = os.environ.get("USERNAME")
            if user:
                os.system(
                    f'icacls "{path}" /inheritance:r /grant:r "{user}:F" >nul 2>&1'
                )
        except Exception:
            pass


def ensure_valid_token(
    config: OAuthConfig,
    cache: TokenCache,
    *,
    skew_seconds: int = 60,
    client: httpx.Client | None = None,
) -> TokenSet:
    """Return a non-expired :class:`TokenSet`, refreshing via ``refresh_token``.

    Raises :class:`OAuthError` if no cached credentials exist or the refresh
    fails (e.g. the refresh_token was revoked and the user must log in again).
    """

    current = cache.load()
    if current is None:
        raise OAuthError("no_cached_token", "run `teams-copilot-proxy login` first")
    if current.access_token and not current.is_expired(skew_seconds):
        return current
    refreshed = refresh_access_token(
        config, current.refresh_token, previous=current, client=client
    )
    cache.save(refreshed)
    return refreshed
