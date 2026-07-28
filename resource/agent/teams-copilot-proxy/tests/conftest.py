from __future__ import annotations

import os

import pytest

from teams_copilot_proxy.config import Settings

# Files Settings resolves relative to the working directory. Left at their
# defaults the suite would read and rewrite a real deployment's state.
_REDIRECTED_PATHS = {
    "M365_MONITOR_DB_PATH": "monitor.db",
    "M365_OAUTH_CACHE_PATH": "oauth_tokens.json",
    "M365_PROBE_CACHE_PATH": "probe_cache.json",
}


@pytest.fixture(autouse=True)
def isolate_deployment_state(monkeypatch, tmp_path):
    """Run every test against a throwaway configuration.

    Settings loads `.env` from the working directory and honours `M365_*`
    environment variables, so a suite started inside a configured deployment
    would pick up its planning mode, write into its Monitor database and spend
    its cached OAuth refresh token.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith("M365_"):
            monkeypatch.delenv(name, raising=False)
    for name, filename in _REDIRECTED_PATHS.items():
        monkeypatch.setenv(name, str(tmp_path / filename))
    monkeypatch.chdir(tmp_path)
