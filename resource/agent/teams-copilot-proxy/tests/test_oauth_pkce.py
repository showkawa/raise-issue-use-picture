from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse

import httpx
import pytest

from teams_copilot_proxy.oauth_pkce import (
    DEFAULT_SCOPE,
    OAuthConfig,
    OAuthError,
    TokenCache,
    TokenSet,
    build_authorization_url,
    ensure_valid_token,
    exchange_code,
    generate_pkce_verifier,
    parse_redirect_code,
    pkce_challenge,
    poll_device_code,
    refresh_access_token,
    start_device_code,
)


def _fake_jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.sig"


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- PKCE primitives -------------------------------------------------------
def test_pkce_challenge_is_s256_of_verifier():
    verifier = "test-verifier-fixed-value-1234567890"
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert pkce_challenge(verifier) == expected


def test_generate_pkce_verifier_is_urlsafe_and_unpadded():
    verifier = generate_pkce_verifier()
    assert "=" not in verifier
    assert len(verifier) >= 43


def test_build_authorization_url_has_pkce_params():
    config = OAuthConfig()
    verifier = generate_pkce_verifier()
    url = build_authorization_url(config, verifier, state="xyz")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [pkce_challenge(verifier)]
    assert query["client_id"] == [config.client_id]
    assert query["redirect_uri"] == [config.redirect_uri]
    assert query["state"] == ["xyz"]
    assert "offline_access" in query["scope"][0]


# --- redirect parsing ------------------------------------------------------
def test_parse_redirect_code_from_full_url():
    url = "https://login.microsoftonline.com/common/oauth2/nativeclient?code=ABC123&state=x"
    assert parse_redirect_code(url) == "ABC123"


def test_parse_redirect_code_accepts_bare_code():
    assert parse_redirect_code("  BARECODE  ") == "BARECODE"


def test_parse_redirect_code_raises_on_error_redirect():
    url = "https://x/nativeclient?error=access_denied&error_description=nope"
    with pytest.raises(OAuthError) as exc:
        parse_redirect_code(url)
    assert exc.value.error == "access_denied"


# --- code exchange ---------------------------------------------------------
def test_exchange_code_returns_token_set_with_account():
    config = OAuthConfig()
    id_token = _fake_jwt({"preferred_username": "user@contoso.com", "oid": "oid-1", "tid": "tid-1"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth2/v2.0/token")
        body = urllib.parse.parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code_verifier"] == ["verifier-1"]
        assert body["code"] == ["code-1"]
        return httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "id_token": id_token,
                "expires_in": 3600,
                "scope": DEFAULT_SCOPE,
            },
        )

    with _mock_client(handler) as client:
        token_set = exchange_code(config, "code-1", "verifier-1", client=client)

    assert token_set.access_token == "access-1"
    assert token_set.refresh_token == "refresh-1"
    assert token_set.account["email"] == "user@contoso.com"
    assert token_set.account["oid"] == "oid-1"
    assert 3500 < token_set.seconds_remaining() <= 3600


def test_exchange_code_raises_oauth_error():
    config = OAuthConfig()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad code"})

    with _mock_client(handler) as client:
        with pytest.raises(OAuthError) as exc:
            exchange_code(config, "bad", "verifier", client=client)
    assert exc.value.error == "invalid_grant"


# --- refresh ---------------------------------------------------------------
def test_refresh_preserves_refresh_token_when_omitted():
    config = OAuthConfig()
    previous = TokenSet(access_token="old", refresh_token="rt-keep", scope=DEFAULT_SCOPE)

    def handler(request: httpx.Request) -> httpx.Response:
        body = urllib.parse.parse_qs(request.content.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["rt-keep"]
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    with _mock_client(handler) as client:
        refreshed = refresh_access_token(config, "rt-keep", previous=previous, client=client)

    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "rt-keep"


def test_refresh_without_token_raises():
    with pytest.raises(OAuthError):
        refresh_access_token(OAuthConfig(), "")


# --- device code -----------------------------------------------------------
def test_device_code_start_and_poll_success_after_pending():
    config = OAuthConfig()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-1",
                    "user_code": "USER-CODE",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "interval": 1,
                    "expires_in": 30,
                    "message": "enter USER-CODE",
                },
            )
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "dev-access", "refresh_token": "dev-rt", "expires_in": 3600})

    with _mock_client(handler) as client:
        device = start_device_code(config, client=client)
        assert device["user_code"] == "USER-CODE"
        token_set = poll_device_code(config, device, client=client, sleep_func=lambda _s: None)

    assert token_set.access_token == "dev-access"
    assert calls["n"] == 2


def test_device_code_poll_times_out():
    config = OAuthConfig()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "authorization_pending"})

    device = {"device_code": "d", "interval": 1, "expires_in": 2}
    fake_now = {"t": 1000.0}

    def now():
        return fake_now["t"]

    def sleep(_s):
        fake_now["t"] += 1.0

    with _mock_client(handler) as client:
        with pytest.raises(OAuthError) as exc:
            poll_device_code(config, device, client=client, sleep_func=sleep, now_func=now)
    assert exc.value.error == "expired_token"


# --- cache + ensure_valid --------------------------------------------------
def test_token_cache_round_trip(tmp_path):
    cache = TokenCache(tmp_path / "oauth.json")
    ts = TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 100, account={"email": "e"})
    cache.save(ts)
    loaded = cache.load()
    assert loaded is not None
    assert loaded.access_token == "a"
    assert loaded.refresh_token == "r"
    assert loaded.account["email"] == "e"


def test_token_cache_load_missing_returns_none(tmp_path):
    assert TokenCache(tmp_path / "nope.json").load() is None


def test_ensure_valid_returns_cached_when_fresh(tmp_path):
    cache = TokenCache(tmp_path / "oauth.json")
    cache.save(TokenSet(access_token="fresh", refresh_token="r", expires_at=time.time() + 3600))

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("refresh should not be called for a fresh token")

    with _mock_client(handler) as client:
        result = ensure_valid_token(OAuthConfig(), cache, client=client)
    assert result.access_token == "fresh"


def test_ensure_valid_refreshes_when_expired(tmp_path):
    cache = TokenCache(tmp_path / "oauth.json")
    cache.save(TokenSet(access_token="old", refresh_token="rt", expires_at=time.time() - 10))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})

    with _mock_client(handler) as client:
        result = ensure_valid_token(OAuthConfig(), cache, client=client)
    assert result.access_token == "refreshed"
    assert cache.load().access_token == "refreshed"


def test_ensure_valid_without_cache_raises(tmp_path):
    with pytest.raises(OAuthError) as exc:
        ensure_valid_token(OAuthConfig(), TokenCache(tmp_path / "none.json"))
    assert exc.value.error == "no_cached_token"
