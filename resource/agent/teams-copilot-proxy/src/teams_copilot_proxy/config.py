from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    startup_probe: bool = Field(default=False, alias="M365_STARTUP_PROBE")
    probe_cache_path: str = Field(default=".probe_cache.json", alias="M365_PROBE_CACHE_PATH")
    probe_ttl_seconds: float = Field(default=86_400, alias="M365_PROBE_TTL_SECONDS")
    max_transcript_chars: int = Field(default=200_000, alias="M365_MAX_TRANSCRIPT_CHARS")
    proxy: str = Field(default="", alias="M365_PROXY")
    tool_correction_retries: int = Field(default=1, alias="M365_TOOL_CORRECTION_RETRIES")
    redact_outbound: bool = Field(default=True, alias="M365_REDACT_OUTBOUND")
    suppress_system_prompt_with_tools: bool = Field(
        default=True, alias="M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS"
    )
    stream_keepalive_interval_s: float = Field(
        default=15.0, alias="M365_STREAM_KEEPALIVE_INTERVAL_S"
    )
    stream_chunk_chars: int = Field(default=24, alias="M365_STREAM_CHUNK_CHARS")
    stream_chunk_delay_ms: int = Field(default=0, alias="M365_STREAM_CHUNK_DELAY_MS")
