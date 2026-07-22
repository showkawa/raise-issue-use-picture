from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from contextlib import asynccontextmanager
from pathlib import Path

from .config import Settings
from .probe import probe_capabilities
from .session_store import PersistentSession, PersistentSessionStore
from .guards import (
    CONFABULATION,
    DISENGAGED,
    DISENGAGED_SENTINEL,
    HALLUCINATED_COMPLETION,
    TOOL_PARSE_FAILURE,
    detect_confabulation,
    detect_hallucinated_completion,
    disengaged_retry_prompt,
    guard_retry_prompt,
)
from .substrate_client import (
    SubstrateCopilotClient,
    SubstrateCopilotError,
    SubstrateDisengagedError,
    SubstrateThrottledError,
)
from .token_store import AccessTokenStore
from .models import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    OpenAIChatRequest,
    OpenAIMessage,
    OpenAIResponsesRequest,
    TranslatedRequest,
)
from .redaction import redact_outbound
from .tool_protocol import (
    TOOL_FAILURE_SENTINEL,
    ToolParseOutcome,
    correction_prompt,
    parse_model_output,
    tool_names,
)
from .translator import (
    flatten_content,
    translate_anthropic_request,
    translate_openai_request,
    translate_responses_request,
)

logger = logging.getLogger(__name__)

_PERSIST_MODEL_SUFFIX = ":persist"
_SESSION_ID_HEADER = "x-m365-session-id"

_TONE_BY_MODEL_PREFIX = (
    ("claude", "Claude_Sonnet"),
    ("gpt", "Gpt_5_5_Chat"),
    ("magic", "Magic"),
)


def _upstream_http_error(exc: SubstrateCopilotError) -> HTTPException:
    if isinstance(exc, SubstrateThrottledError):
        return HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        )
    return HTTPException(status_code=502, detail=str(exc))


def _retry_context(
    additional_context: list[str], prompt: str, previous_reply: str
) -> list[str]:
    return additional_context + [
        f"Original request:\n{prompt}",
        f"Your previous reply:\n{previous_reply}",
    ]


def _tone_for_model(model: str, default_tone: str) -> str:
    name = model.removesuffix(_PERSIST_MODEL_SUFFIX).lower()
    for prefix, tone in _TONE_BY_MODEL_PREFIX:
        if name.startswith(prefix):
            return tone
    return default_tone


def _model_id_for_tone(tone: str) -> str:
    return tone.lower().replace("_", "-")


def create_app(
    settings: Settings | None = None,
    copilot_client_factory: Callable[[], SubstrateCopilotClient] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await _startup_capability_probe()
        yield

    app = FastAPI(title="Teams Copilot Proxy", lifespan=lifespan)
    resolved_settings = settings or Settings()
    app.state.settings = resolved_settings
    app.state.token_store = AccessTokenStore(resolved_settings.access_token)
    app.state.session_store = PersistentSessionStore()
    app.state.warned_persist_without_id = False
    app.state.copilot_client_factory = copilot_client_factory or (
        lambda: SubstrateCopilotClient(
            app.state.token_store.get(),
            resolved_settings.time_zone,
            resolved_settings.proxy,
            resolved_settings.default_tone,
        )
    )
    app.state.capability = None

    async def _startup_capability_probe() -> None:
        if not resolved_settings.startup_probe or not resolved_settings.access_token:
            return
        try:
            app.state.capability = await probe_capabilities(
                app.state.copilot_client_factory,
                Path(resolved_settings.probe_cache_path),
                resolved_settings.probe_ttl_seconds,
            )
        except Exception:
            logger.exception(
                "Startup capability probe failed; falling back to configured defaults."
            )

    def effective_default_tone(settings: Settings) -> str:
        capability = app.state.capability
        return capability.tone if capability else settings.default_tone

    def get_settings() -> Settings:
        return app.state.settings

    def get_copilot_client() -> SubstrateCopilotClient:
        return app.state.copilot_client_factory()

    @app.get("/healthz")
    async def healthz() -> dict:
        body = {"status": "ok", "token": app.state.token_store.status()}
        if app.state.capability:
            body["capability"] = app.state.capability.as_dict()
        return body

    @app.get("/v1/token/status")
    async def token_status() -> dict:
        return app.state.token_store.status()

    @app.get("/v1/models")
    async def list_models(settings: Settings = Depends(get_settings)) -> dict:
        ids = [settings.model_alias]
        capability = app.state.capability
        if capability:
            for tone in capability.accepted_tones:
                model_id = _model_id_for_tone(tone)
                if model_id not in ids:
                    ids.append(model_id)
        return {
            "object": "list",
            "data": [
                {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "microsoft-365-copilot",
                }
                for model_id in ids
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        raw_request: Request,
        request: OpenAIChatRequest,
        settings: Settings = Depends(get_settings),
        client: SubstrateCopilotClient = Depends(get_copilot_client),
    ):
        try:
            translated = translate_openai_request(
                request,
                settings.max_transcript_chars,
                settings.suppress_system_prompt_with_tools,
            )
            translated = _redact_translated(translated, settings)
            client.tone = _tone_for_model(request.model, effective_default_tone(settings))
            session = _persistent_session(
                app,
                raw_request,
                request.model,
                request.user,
                _conversation_key(request.messages),
            )
            if request.stream:
                if request.tools:
                    return StreamingResponse(
                        _openai_stream_with_tools(
                            settings.model_alias,
                            client,
                            translated.prompt,
                            translated.additional_context,
                            request.tools,
                            session,
                            settings.tool_correction_retries,
                            keepalive_interval=settings.stream_keepalive_interval_s,
                            chunk_chars=settings.stream_chunk_chars,
                            chunk_delay_ms=settings.stream_chunk_delay_ms,
                        ),
                        media_type="text/event-stream",
                    )
                return StreamingResponse(
                    _openai_stream(
                        settings.model_alias,
                        client,
                        translated.prompt,
                        translated.additional_context,
                        session,
                    ),
                    media_type="text/event-stream",
                )
            if request.tools:
                outcome = await _chat_resolving_tools(
                    client,
                    translated.prompt,
                    translated.additional_context,
                    request.tools,
                    session,
                    settings.tool_correction_retries,
                )
                return JSONResponse(_tool_outcome_completion(settings.model_alias, outcome))
            text = await client.chat(translated.prompt, translated.additional_context, session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SubstrateCopilotError as exc:
            raise _upstream_http_error(exc) from exc

        return JSONResponse({
            "id": f"chatcmpl_{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": settings.model_alias,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        })

    @app.post("/v1/responses")
    async def openai_responses(
        raw: Request,
        settings: Settings = Depends(get_settings),
        client: SubstrateCopilotClient = Depends(get_copilot_client),
    ):
        body = await raw.json()
        try:
            request = OpenAIResponsesRequest.model_validate(body)
            translated = translate_responses_request(request)
            translated = _redact_translated(translated, settings)
            client.tone = _tone_for_model(request.model, effective_default_tone(settings))
            session = _persistent_session(app, raw, request.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if request.stream:
            return StreamingResponse(
                _responses_stream(settings.model_alias, client, translated.prompt, translated.additional_context, session),
                media_type="text/event-stream",
            )

        try:
            text = await client.chat(translated.prompt, translated.additional_context, session)
        except SubstrateCopilotError as exc:
            raise _upstream_http_error(exc) from exc

        return JSONResponse({
            "id": f"resp_{uuid.uuid4().hex}",
            "object": "response",
            "created_at": int(time.time()),
            "model": settings.model_alias,
            "output": [{
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        })

    @app.post("/v1/messages")
    async def anthropic_messages(
        raw_request: Request,
        request: AnthropicMessagesRequest,
        settings: Settings = Depends(get_settings),
        client: SubstrateCopilotClient = Depends(get_copilot_client),
    ):
        try:
            translated = translate_anthropic_request(request)
            translated = _redact_translated(translated, settings)
            client.tone = _tone_for_model(request.model, effective_default_tone(settings))
            session = _persistent_session(
                app,
                raw_request,
                request.model,
                derived_key=_conversation_key(request.messages),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if request.stream:
            return StreamingResponse(
                _anthropic_stream(settings.model_alias, client, translated.prompt, translated.additional_context, session),
                media_type="text/event-stream",
            )

        try:
            text = await client.chat(translated.prompt, translated.additional_context, session)
        except SubstrateCopilotError as exc:
            raise _upstream_http_error(exc) from exc

        return JSONResponse({
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "role": "assistant",
            "model": settings.model_alias,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

    return app


def _redact_translated(translated: TranslatedRequest, settings: Settings) -> TranslatedRequest:
    if not settings.redact_outbound:
        return translated
    prompt, additional_context = redact_outbound(
        translated.prompt, translated.additional_context
    )
    return TranslatedRequest(prompt=prompt, additional_context=additional_context)


def _persistent_session(
    app: FastAPI,
    raw_request: Request,
    model: str,
    fallback_key: str | None = None,
    derived_key: str | None = None,
) -> PersistentSession | None:
    if not model.endswith(_PERSIST_MODEL_SUFFIX):
        return None
    header_key = (raw_request.headers.get(_SESSION_ID_HEADER) or "").strip()
    if header_key:
        return app.state.session_store.get(f"header:{header_key}")
    user_key = (fallback_key or "").strip()
    if user_key:
        return app.state.session_store.get(f"model:{user_key}")
    if derived_key:
        return app.state.session_store.get(f"conversation:{derived_key}")
    if not app.state.warned_persist_without_id:
        app.state.warned_persist_without_id = True
        logger.warning(
            "':persist' used without an %s header, a 'user' field, or a derivable "
            "conversation key; falling back to a stateless request to avoid sharing "
            "one global Copilot session across conversations.",
            _SESSION_ID_HEADER,
        )
    return None


def _conversation_key(messages: Sequence[object]) -> str | None:
    """Stable per-conversation key: hash of the first user message's text."""
    for message in messages:
        if isinstance(message, (OpenAIMessage, AnthropicMessage)) and message.role == "user":
            text = flatten_content(message.content).strip()
        else:
            continue
        if text:
            return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return None


async def _chat_resolving_tools(
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    tools: list[dict],
    session: PersistentSession | None = None,
    max_corrections: int = 1,
) -> ToolParseOutcome:
    allowed = tool_names(tools)
    budget = max(0, min(max_corrections, 2))
    used = 0
    attempt_prompt = prompt
    attempt_context = additional_context
    while True:
        try:
            text = await client.chat(attempt_prompt, attempt_context, session)
        except SubstrateDisengagedError:
            if used < budget:
                used += 1
                session = None
                attempt_prompt = disengaged_retry_prompt(prompt)
                attempt_context = additional_context
                continue
            return ToolParseOutcome(text=DISENGAGED_SENTINEL, guard=DISENGAGED)
        outcome = parse_model_output(text, allowed)
        if outcome.error is None:
            if not outcome.tool_calls and outcome.text:
                triggered = None
                if detect_confabulation(outcome.text):
                    triggered = CONFABULATION
                elif detect_hallucinated_completion(outcome.text):
                    triggered = HALLUCINATED_COMPLETION
                if triggered is not None:
                    if used < budget:
                        used += 1
                        attempt_prompt = guard_retry_prompt(triggered)
                        attempt_context = _retry_context(additional_context, prompt, text)
                        continue
                    outcome.guard = triggered
            return outcome
        if used < budget:
            used += 1
            strict = budget > 1 and used == budget
            attempt_prompt = correction_prompt(outcome.error, strict=strict)
            attempt_context = _retry_context(additional_context, prompt, text)
            continue
        return ToolParseOutcome(text=TOOL_FAILURE_SENTINEL, guard=TOOL_PARSE_FAILURE)


def _tool_outcome_completion(model_alias: str, outcome: ToolParseOutcome) -> dict:
    if outcome.tool_calls:
        message = {
            "role": "assistant",
            "content": outcome.text or None,
            "tool_calls": [call.as_openai() for call in outcome.tool_calls],
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": outcome.text}
        finish_reason = "stop"
    body = {
        "id": f"chatcmpl_{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_alias,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }
    if outcome.guard:
        body["x_m365_guard"] = {"guard": outcome.guard, "retries_exhausted": True}
    return body


def _split_stream_text(text: str, chunk_chars: int) -> list[str]:
    if chunk_chars <= 0 or len(text) <= chunk_chars:
        return [text]
    return [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]


async def _openai_stream_with_tools(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    tools: list[dict],
    session: PersistentSession | None = None,
    max_corrections: int = 1,
    keepalive_interval: float = 15.0,
    chunk_chars: int = 24,
    chunk_delay_ms: int = 0,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())

    def chunk(delta: dict, finish_reason: str | None = None, extra: dict | None = None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_alias,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if extra:
            payload.update(extra)
        return f"data: {json.dumps(payload)}\n\n"

    yield chunk({"role": "assistant"})
    resolve_task = asyncio.create_task(
        _chat_resolving_tools(
            client, prompt, additional_context, tools, session, max_corrections
        )
    )
    try:
        try:
            if keepalive_interval > 0:
                while True:
                    done, _ = await asyncio.wait({resolve_task}, timeout=keepalive_interval)
                    if done:
                        break
                    yield ": keepalive\n\n"
            outcome = await resolve_task
        except SubstrateCopilotError as exc:
            yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
            return

        if outcome.tool_calls:
            if outcome.text:
                yield chunk({"content": outcome.text})
            calls = [call.as_openai() for call in outcome.tool_calls]
            yield chunk({
                "tool_calls": [
                    {
                        "index": index,
                        "id": call["id"],
                        "type": "function",
                        "function": call["function"],
                    }
                    for index, call in enumerate(calls)
                ]
            })
            yield chunk({}, "tool_calls")
        else:
            if outcome.text:
                pieces = (
                    [outcome.text]
                    if outcome.guard is not None
                    else _split_stream_text(outcome.text, chunk_chars)
                )
                for index, piece in enumerate(pieces):
                    if index and chunk_delay_ms > 0:
                        await asyncio.sleep(chunk_delay_ms / 1000)
                    yield chunk({"content": piece})
            extra = (
                {"x_m365_guard": {"guard": outcome.guard, "retries_exhausted": True}}
                if outcome.guard
                else None
            )
            yield chunk({}, "stop", extra)
        yield "data: [DONE]\n\n"
    finally:
        if not resolve_task.done():
            resolve_task.cancel()


async def _openai_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())
    first_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_alias,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first_chunk)}\n\n"
    try:
        async for delta in client.chat_stream(prompt, additional_context, session):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_alias,
                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
    except SubstrateCopilotError as exc:
        yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_error'}})}\n\n"
        yield "data: [DONE]\n\n"
        return
    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_alias,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


async def _responses_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
) -> AsyncIterator[str]:
    resp_id = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    created = int(time.time())

    yield f"data: {json.dumps({'type': 'response.created', 'response': {'id': resp_id, 'object': 'response', 'created_at': created, 'model': model_alias, 'status': 'in_progress', 'output': []}})}\n\n"
    yield f"data: {json.dumps({'type': 'response.output_item.added', 'output_index': 0, 'item': {'id': item_id, 'type': 'message', 'role': 'assistant', 'content': []}})}\n\n"
    yield f"data: {json.dumps({'type': 'response.content_part.added', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"

    full_text = ""
    try:
        async for delta in client.chat_stream(prompt, additional_context, session):
            full_text += delta
            yield f"data: {json.dumps({'type': 'response.output_text.delta', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'delta': delta})}\n\n"
    except SubstrateCopilotError as exc:
        yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(exc), 'type': 'upstream_error'}})}\n\n"
        return

    yield f"data: {json.dumps({'type': 'response.output_text.done', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'text': full_text})}\n\n"
    yield f"data: {json.dumps({'type': 'response.completed', 'response': {'id': resp_id, 'object': 'response', 'created_at': created, 'model': model_alias, 'status': 'completed', 'output': [{'id': item_id, 'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': full_text}]}], 'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}})}\n\n"


async def _anthropic_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
) -> AsyncIterator[str]:
    msg_id = f"msg_{uuid.uuid4().hex}"

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model_alias, "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
    yield sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    yield sse("ping", {"type": "ping"})

    try:
        async for delta in client.chat_stream(prompt, additional_context, session):
            yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta}})
    except SubstrateCopilotError as exc:
        yield sse("error", {"type": "error", "error": {"type": "upstream_error", "message": str(exc)}})
        return

    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 0}})
    yield sse("message_stop", {"type": "message_stop"})
