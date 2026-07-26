from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None
    image_url: dict[str, Any] | None = None


class ImageInput(BaseModel):
    """An image extracted from a client request, ready to be uploaded to the
    substrate's UploadFile endpoint and referenced via a message annotation."""

    model_config = ConfigDict(extra="ignore")

    data_uri: str
    filename: str = "image.png"
    file_type: str = "png"


class OpenAIToolCallFunction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    arguments: str = ""


class OpenAIToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    type: str = "function"
    function: OpenAIToolCallFunction = Field(default_factory=OpenAIToolCallFunction)


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None = None
    tool_calls: list[OpenAIToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class OpenAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[OpenAIMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    reasoning_effort: str | None = None
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None


class SamplingParams(BaseModel):
    """Decoding parameters OpenCode requests. The substrate chat channel exposes
    no documented sampling knobs (the tone fixes the model and decoding), so these
    are forwarded best-effort into the Chathub ``options`` object and may be
    silently ignored upstream. Only fields the live channel is known to accept
    without error are forwarded."""

    model_config = ConfigDict(extra="ignore")

    temperature: float | None = None
    top_p: float | None = None

    def as_options(self) -> dict[str, float]:
        options: dict[str, float] = {}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        if self.top_p is not None:
            options["topP"] = self.top_p
        return options


class CopilotMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    text: str = ""
    attributions: list[dict[str, Any]] = Field(default_factory=list)


class CopilotConversation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    messages: list[CopilotMessage] = Field(default_factory=list)


class TranslatedRequest(BaseModel):
    prompt: str
    additional_context: list[str] = Field(default_factory=list)
    images: list[ImageInput] = Field(default_factory=list)
    sampling: SamplingParams = Field(default_factory=SamplingParams)
    tools: list[dict[str, Any]] | None = None
    # Names of the context parts injected into the prompt (Monitor telemetry).
    injections: list[str] = Field(default_factory=list)

