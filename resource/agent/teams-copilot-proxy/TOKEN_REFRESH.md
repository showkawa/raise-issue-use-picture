# Token Refresh Automation

The proxy needs a short-lived user JWT for the
`substrate.office.com` WebSocket API. It supports two implemented refresh paths:

1. **OAuth 2.0 + PKCE** — preferred when the tenant accepts the bundled public
   client and requested scopes. A cached `refresh_token` lets the proxy renew
   the substrate access token without keeping Chrome open.
2. **Chrome CDP capture** — fallback for a signed-in dedicated Chrome profile.
   The proxy extracts the access token from a live Copilot WebSocket.

The OAuth path is tenant-dependent. If sign-in or refresh is rejected by
Microsoft Entra, use the CDP path or the manual fallback below.

> After a token is available, `serve` may run the startup capability probe
> (`M365_STARTUP_PROBE`). Its result is cached in `.probe_cache.json` for
> `M365_PROBE_TTL_SECONDS` (24 hours by default).

## OAuth 2.0 + PKCE (preferred)

Interactive sign-in opens a browser and asks you to paste the redirect URL:

```bat
uv run teams-copilot-proxy login
```

For a headless environment, use device code sign-in:

```bat
uv run teams-copilot-proxy login-device
```

Both commands save the token set, including the `refresh_token`, to
`.oauth_tokens.json` and publish the current access token to `.env` as
`M365_ACCESS_TOKEN`. The cache is git-ignored and should be treated like a
password.

Once the cache exists, `serve` tries an OAuth refresh at startup and prefers
OAuth during its background refresh loop. Force a one-off refresh with:

```bat
uv run teams-copilot-proxy oauth-refresh
```

If the refresh token is revoked or the tenant rejects the OAuth flow, run
`login` or `login-device` again, or use the CDP fallback.

## Chrome CDP capture

Start the server normally:

```bat
uv run teams-copilot-proxy serve
```

It launches a dedicated Chrome profile at
`%USERPROFILE%\.teams-copilot-proxy\chrome-profile`. Sign in to M365 Copilot
there once. The server refreshes before expiry by reading a new WebSocket
token from that profile.

If startup capture is waiting for a token, press `F5` in the dedicated Copilot
tab, click the message box, and type one character. Do not send the message.

Useful flags:

```bat
uv run teams-copilot-proxy serve --refresh-before-seconds 300
uv run teams-copilot-proxy serve --no-launch-chrome
uv run teams-copilot-proxy serve --no-capture-on-start
uv run teams-copilot-proxy serve --no-auto-refresh
```

`--no-auto-refresh` disables both OAuth and Chrome background refresh.
`--no-launch-chrome` only suppresses launching Chrome; it does not disable
OAuth refresh or CDP capture if a debug Chrome instance is already available.

## Manual fallback

```bat
uv run teams-copilot-proxy set-token
```

Paste the full WebSocket URL from DevTools:

1. Open the signed-in M365 Copilot Chrome window.
2. Open DevTools (`F12`) and select **Network**.
3. Filter for `substrate`.
4. Select the WebSocket entry.
5. In **Headers**, copy the full **Request URL**.
6. Paste it into the terminal.

The command extracts `access_token` and writes it to `.env`.

## Health checks

```bat
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/token/status
```

Both endpoints report token validity and remaining lifetime. `/healthz` also
reports the startup capability result when one is available.
