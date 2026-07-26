from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .oauth_pkce import (
    DEFAULT_AUTHORITY,
    DEFAULT_CLIENT_ID,
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPE,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    access_token: str = Field(default="", alias="M365_ACCESS_TOKEN")
    time_zone: str = Field(default="Asia/Tokyo", alias="M365_TIME_ZONE")
    model_alias: str = Field(default="m365-copilot", alias="M365_MODEL_ALIAS")
    default_tone: str = Field(default="Claude_Sonnet", alias="M365_DEFAULT_TONE")
    startup_probe: bool = Field(default=True, alias="M365_STARTUP_PROBE")
    probe_cache_path: str = Field(default=".probe_cache.json", alias="M365_PROBE_CACHE_PATH")
    probe_ttl_seconds: float = Field(default=86_400, alias="M365_PROBE_TTL_SECONDS")
    max_transcript_chars: int = Field(default=200_000, alias="M365_MAX_TRANSCRIPT_CHARS")
    proxy: str = Field(default="", alias="M365_PROXY")
    tool_correction_retries: int = Field(default=1, alias="M365_TOOL_CORRECTION_RETRIES")
    redact_outbound: bool = Field(default=True, alias="M365_REDACT_OUTBOUND")
    suppress_system_prompt_with_tools: bool = Field(
        default=False, alias="M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS"
    )
    sanitize_system_prompt_with_tools: bool = Field(
        default=True, alias="M365_SANITIZE_SYSTEM_PROMPT_WITH_TOOLS"
    )
    stream_keepalive_interval_s: float = Field(
        default=15.0, alias="M365_STREAM_KEEPALIVE_INTERVAL_S"
    )
    stream_chunk_chars: int = Field(default=24, alias="M365_STREAM_CHUNK_CHARS")
    stream_chunk_delay_ms: int = Field(default=0, alias="M365_STREAM_CHUNK_DELAY_MS")
    allow_parallel_tool_calls: bool = Field(
        default=False, alias="M365_ALLOW_PARALLEL_TOOL_CALLS"
    )
    # Tones that may use parallel tool calls when M365_ALLOW_PARALLEL_TOOL_CALLS
    # is not explicitly set to True. Live A/B showed Claude_Sonnet can emit multiple
    # tool_call blocks, while the Gpt_5_* reasoning tones often refuse or disengage.
    parallel_tool_tones: str = Field(
        default="Claude_Sonnet", alias="M365_PARALLEL_TOOL_TONES"
    )
    # Hard token ceiling reported to OpenCode. The M365 Copilot substrate has a
    # single-conversation limit of ~265k tokens; we reject requests that exceed it.
    context_limit: int = Field(default=265_000, alias="M365_CONTEXT_LIMIT")
    # Remove duplicate web-search tools from the client tool list; Copilot already
    # surfaces Bing-grounded web results, so an agent-provided web_search tool
    # usually just causes redundant turns.
    dedup_websearch: bool = Field(default=True, alias="M365_DEDUP_WEBSEARCH")

    # --- Monitor (阶段二) ---
    # 进程内可观测子系统：事件总线 → SQLite sink → 只读 /monitor API。
    monitor_enabled: bool = Field(default=True, alias="M365_MONITOR_ENABLED")
    monitor_db_path: str = Field(default="monitor.db", alias="M365_MONITOR_DB_PATH")
    # 内容留存档位：off（只记元数据）/ failures（失败或守卫触发才留脱敏现场，默认）/ all。
    monitor_capture: str = Field(default="failures", alias="M365_MONITOR_CAPTURE")
    monitor_retention_days: int = Field(default=30, alias="M365_MONITOR_RETENTION_DAYS")
    # /monitor 查询与面板复用的 Bearer token；未单独配置时回退到 access_token。
    monitor_token: str = Field(default="", alias="M365_MONITOR_TOKEN")

    # --- PKCE OAuth token 获取（ADR-0008，opt-in）---
    # 通过标准 Microsoft identity platform OAuth 2.0 + PKCE 拿 refresh_token，
    # 实现脱离浏览器的长效自动续期。见 `teams-copilot-proxy login` / `login-device`。
    oauth_client_id: str = Field(default=DEFAULT_CLIENT_ID, alias="M365_OAUTH_CLIENT_ID")
    oauth_authority: str = Field(default=DEFAULT_AUTHORITY, alias="M365_OAUTH_AUTHORITY")
    oauth_scope: str = Field(default=DEFAULT_SCOPE, alias="M365_OAUTH_SCOPE")
    oauth_redirect_uri: str = Field(default=DEFAULT_REDIRECT_URI, alias="M365_OAUTH_REDIRECT_URI")
    # refresh_token 缓存文件（含长效凭据，务必限制文件权限）。
    oauth_cache_path: str = Field(default=".oauth_tokens.json", alias="M365_OAUTH_CACHE_PATH")

