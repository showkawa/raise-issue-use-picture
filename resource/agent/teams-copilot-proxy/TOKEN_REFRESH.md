# Token Refresh Automation Options

The `substrate.office.com` API requires a user JWT that expires in ~1 hour. Admin consent is blocked, so tokens cannot be obtained programmatically via MSAL device code flow. Browser automation is required.

## Current manual flow

```bat
uv run teams-copilot-proxy set-token
REM paste full WebSocket URL from DevTools -> Network -> substrate WebSocket -> Headers
```

---

## Option A — Playwright (recommended)

Launch a hidden browser using the existing Chrome user profile (already authenticated). Navigate to M365 Copilot, intercept the WebSocket connection, extract the token, update `.env`, restart the server.

**Pros:** fully automatic, works even if Chrome is not open  
**Cons:** requires `playwright` + `playwright install chrome`, takes ~5s per refresh

Implementation sketch:
```python
from playwright.async_api import async_playwright

async def get_fresh_token() -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="C:/Users/<user>/AppData/Local/Google/Chrome/User Data",
            channel="chrome",
            headless=True,
        )
        token = None
        page = await browser.new_page()
        async def on_websocket(ws):
            nonlocal token
            m = re.search(r"access_token=([^&]+)", ws.url)
            if m:
                token = m.group(1)
        page.on("websocket", on_websocket)
        await page.goto("https://m365.cloud.microsoft/chat")
        await page.wait_for_timeout(5000)
        await browser.close()
        return token
```

Schedule with `schedule` or `apscheduler` every 50 minutes.

---

## Option B — Chrome remote debugging (CDP)

Launch a dedicated Chrome profile with the remote debugging flag, then connect to it via CDP.

**Start the server:**
```bat
uv run teams-copilot-proxy serve
```

`serve` opens the dedicated debug Chrome window by default. Sign in to M365 Copilot in that window once.
The profile is stored under
`%USERPROFILE%\.teams-copilot-proxy\chrome-profile`, so later launches can reuse the sign-in.
Then the server connects to `http://localhost:9222` and extracts the token from the Copilot tab.
`uv run teams-copilot-proxy serve` starts an auto-refresh loop by default. It refreshes when the
current JWT has less than 5 minutes left.
If the current token is missing, expired, or not a Substrate token, `serve` first tries the same `r`-style
refresh from the current debug Chrome tab. If no Substrate token is available yet, it starts a one-shot
startup capture listener. Generate a new WebSocket by pressing `F5` in the debug Chrome Copilot tab, clicking
the message box, and typing one character. The message does not need to be sent.

Useful serve flags:
```bat
uv run teams-copilot-proxy serve --refresh-before-seconds 300
uv run teams-copilot-proxy serve --no-launch-chrome
uv run teams-copilot-proxy serve --no-capture-on-start
uv run teams-copilot-proxy serve --no-auto-refresh
```

**Pros:** lightweight, uses `websockets` (already installed), works even if normal Chrome is already open  
**Cons:** requires a separate Chrome profile; less reliable if the Copilot tab is closed

---

## Option C — Windows WAM / MSAL broker

`msal` with `allow_broker=True` on Windows 10/11 uses the OS-level Web Account Manager. Investigated but **not viable** — WAM token caches are per-app and the `substrate.office.com` resource requires pre-authorization (`AADSTS65002`), which blocks even cached token reuse from external client IDs.

---

## Option D — Admin consent (cleanest long-term fix)

Ask the IT admin to either:
1. Register a new Entra app and grant delegated Graph permissions (original approach), or
2. Grant admin consent for the `Microsoft Graph Command Line Tools` app (`14d82eec-...`)

Either removes the need for token automation entirely.
