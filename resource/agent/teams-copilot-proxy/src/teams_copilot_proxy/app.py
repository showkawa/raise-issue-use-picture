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
from .probe import CANDIDATE_TONES, probe_capabilities
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
    _combine_text,
)
from .token_store import AccessTokenStore
from .models import (
    OpenAIChatRequest,
    OpenAIMessage,
    TranslatedRequest,
)
from .monitor import (
    STATUS_ERROR,
    STATUS_GUARD,
    STATUS_OK,
    MonitorBus,
    RequestRecorder,
    SQLiteSink,
    _NullRecorder,
)
from .redaction import redact_outbound
from .tool_protocol import (
    TOOL_FAILURE_SENTINEL,
    ToolParseOutcome,
    correction_prompt,
    parse_model_output,
    parse_model_output_multi,
    tool_names,
)
from .translator import (
    flatten_content,
    translate_openai_request,
)
from .usage import estimate_tokens, openai_usage

logger = logging.getLogger(__name__)

_PERSIST_MODEL_SUFFIX = ":persist"
_SESSION_ID_HEADER = "x-m365-session-id"
_MONITOR_SESSION_HEADER = "x-session-id"

_NULL_RECORDER = _NullRecorder()

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
    for tone in CANDIDATE_TONES:
        if _model_id_for_tone(tone) == name:
            return tone
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
    app.state.monitor = None
    app.state.monitor_token = (
        resolved_settings.monitor_token or resolved_settings.access_token
    )
    if resolved_settings.monitor_enabled:
        try:
            sink = SQLiteSink(
                resolved_settings.monitor_db_path,
                resolved_settings.monitor_retention_days,
            )
            app.state.monitor = MonitorBus(
                sink, capture=resolved_settings.monitor_capture
            )
        except Exception:
            logger.exception("Monitor init failed; running without monitoring.")
            app.state.monitor = None

    def new_recorder(raw_request: Request, request: OpenAIChatRequest, tone: str):
        monitor = app.state.monitor
        if monitor is None:
            return _NULL_RECORDER, f"chatcmpl_{uuid.uuid4().hex}"
        request_id = f"chatcmpl_{uuid.uuid4().hex}"
        recorder = RequestRecorder(
            monitor,
            request_id=request_id,
            session_key=_monitor_session_key(raw_request, request.messages),
            model=request.model,
            tone=tone,
            stream=bool(request.stream),
        )
        return recorder, request_id

    def require_monitor_auth(raw_request: Request) -> None:
        expected = app.state.monitor_token
        header = raw_request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not expected or token != expected:
            raise HTTPException(status_code=401, detail="invalid monitor token")

    def require_monitor(raw_request: Request) -> MonitorBus:
        require_monitor_auth(raw_request)
        monitor = app.state.monitor
        if monitor is None:
            raise HTTPException(status_code=404, detail="monitor disabled")
        return monitor

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
        recorder = _NULL_RECORDER
        input_text = ""
        try:
            selected_tone = _tone_for_model(
                request.model, effective_default_tone(settings)
            )
            recorder, request_id = new_recorder(raw_request, request, selected_tone)
            allow_parallel = settings.allow_parallel_tool_calls or (
                selected_tone
                in {tone.strip() for tone in settings.parallel_tool_tones.split(",")}
            )
            translated = translate_openai_request(
                request,
                settings.max_transcript_chars,
                settings.suppress_system_prompt_with_tools,
                settings.sanitize_system_prompt_with_tools,
                allow_parallel,
                settings.dedup_websearch,
            )
            _record_tool_results(recorder, request.messages)
            translated = _redact_translated(translated, settings)
            client.tone = selected_tone
            client.images = translated.images
            client.options = translated.sampling.as_options()
            session = _persistent_session(
                app,
                raw_request,
                request.model,
                request.user,
                _conversation_key(request.messages),
            )
            input_text = _combine_text(
                translated.prompt, translated.additional_context
            )
            if settings.context_limit and estimate_tokens(input_text) > settings.context_limit:
                raise ValueError(
                    f"Estimated prompt tokens {estimate_tokens(input_text)} exceed "
                    f"the M365 Copilot context limit ({settings.context_limit})."
                )
            if request.stream:
                if translated.tools:
                    return StreamingResponse(
                        _openai_stream_with_tools(
                            settings.model_alias,
                            client,
                            translated.prompt,
                            translated.additional_context,
                            translated.tools,
                            session,
                            settings.tool_correction_retries,
                            allow_parallel=allow_parallel,
                            keepalive_interval=settings.stream_keepalive_interval_s,
                            chunk_chars=settings.stream_chunk_chars,
                            chunk_delay_ms=settings.stream_chunk_delay_ms,
                            input_text=input_text,
                            recorder=recorder,
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
                        input_text=input_text,
                        recorder=recorder,
                    ),
                    media_type="text/event-stream",
                )
            if translated.tools:
                outcome = await _chat_resolving_tools(
                    client,
                    translated.prompt,
                    translated.additional_context,
                    translated.tools,
                    session,
                    settings.tool_correction_retries,
                    allow_parallel,
                    recorder=recorder,
                )
                body = _tool_outcome_completion(
                    settings.model_alias,
                    outcome,
                    input_text,
                    completion_id=request_id,
                )
                message = body["choices"][0]["message"]
                recorder.record_tool_calls(message.get("tool_calls") or [])
                recorder.finish(
                    status=STATUS_GUARD if outcome.guard else STATUS_OK,
                    guard=outcome.guard,
                    input_text=input_text,
                    output_text=_completion_output_text(body),
                )
                return JSONResponse(body)
            started = recorder.attempt_timer()
            text = await client.chat(translated.prompt, translated.additional_context, session)
            recorder.add_attempt(started, status=STATUS_OK)
        except ValueError as exc:
            recorder.finish(
                status=STATUS_ERROR,
                input_text=input_text,
                error=str(exc),
                error_type="bad_request",
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SubstrateCopilotError as exc:
            recorder.finish(
                status=STATUS_ERROR,
                input_text=input_text,
                error=str(exc),
                error_type=_substrate_error_type(exc),
            )
            raise _upstream_http_error(exc) from exc

        recorder.finish(
            status=STATUS_OK, input_text=input_text, output_text=text
        )
        return JSONResponse({
            "id": request_id,
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
            "usage": openai_usage(input_text, text),
        })

    @app.get("/monitor/api/summary")
    async def monitor_summary(raw_request: Request) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        return monitor.sink.summary()

    @app.get("/monitor/api/requests")
    async def monitor_requests(
        raw_request: Request, limit: int = 50, session: str | None = None
    ) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        return {"requests": monitor.sink.requests(limit=limit, session=session)}

    @app.get("/monitor/api/requests/{request_id}")
    async def monitor_request_detail(raw_request: Request, request_id: str) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        detail = monitor.sink.request_detail(request_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="request not found")
        return detail

    @app.get("/monitor/api/tools")
    async def monitor_tools(raw_request: Request) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        return {"tools": monitor.sink.tools()}

    @app.get("/monitor/api/errors")
    async def monitor_errors(raw_request: Request, limit: int = 100) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        return {"errors": monitor.sink.errors(limit=limit)}

    @app.get("/monitor/api/sessions/{session_key}")
    async def monitor_session(raw_request: Request, session_key: str) -> dict:
        monitor = require_monitor(raw_request)
        monitor.flush()
        detail = monitor.sink.session_detail(session_key)
        if detail is None:
            raise HTTPException(status_code=404, detail="session not found")
        return detail

    return app


def _substrate_error_type(exc: SubstrateCopilotError) -> str:
    if isinstance(exc, SubstrateThrottledError):
        return "throttled"
    if isinstance(exc, SubstrateDisengagedError):
        return "disengaged"
    return "upstream_error"


def _record_tool_results(recorder, messages: Sequence[OpenAIMessage]) -> None:
    """把 transcript 里的工具执行结果交给 Monitor 做轻量闭环（只提 error 标记与
    字节数，不存结果全文）。配对发生在 sink 写入时，不影响主链路。"""
    call_names: dict[str, str] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                call_names[call.id] = call.function.name
        if message.role != "tool":
            continue
        content = flatten_content(message.content)
        name = message.name or call_names.get(message.tool_call_id or "")
        recorder.record_tool_result(
            call_id=message.tool_call_id,
            name=name,
            is_error=content.lstrip().lower().startswith("error"),
            result_bytes=len(content.encode("utf-8")),
        )


def _completion_output_text(body: dict) -> str:
    """Reconstruct the assistant output text (content + tool-call arguments) for
    token estimation and capture, from a chat.completion body."""
    message = body.get("choices", [{}])[0].get("message", {})
    text = message.get("content") or ""
    for call in message.get("tool_calls") or []:
        text += call.get("function", {}).get("arguments", "")
    return text


def _redact_translated(translated: TranslatedRequest, settings: Settings) -> TranslatedRequest:
    if not settings.redact_outbound:
        return translated
    prompt, additional_context = redact_outbound(
        translated.prompt, translated.additional_context
    )
    return TranslatedRequest(
        prompt=prompt,
        additional_context=additional_context,
        images=translated.images,
        sampling=translated.sampling,
        tools=translated.tools,
    )


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


def _monitor_session_key(
    raw_request: Request, messages: Sequence[object]
) -> str | None:
    """Monitor session identity: prefer an explicit ``x-session-id`` header, else
    fall back to the conversation key (hash of the first user message)."""
    header = (raw_request.headers.get(_MONITOR_SESSION_HEADER) or "").strip()
    if header:
        return header
    return _conversation_key(messages)


def _conversation_key(messages: Sequence[object]) -> str | None:
    """Stable per-conversation key: hash of the first user message's text."""
    for message in messages:
        if isinstance(message, OpenAIMessage) and message.role == "user":
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
    allow_parallel: bool = False,
    recorder=_NULL_RECORDER,
) -> ToolParseOutcome:
    allowed = tool_names(tools)
    parse = parse_model_output_multi if allow_parallel else parse_model_output
    # Completion claims are only hallucinations when no tool has actually run yet;
    # after real tool results a "created/updated the file" summary is legitimate.
    tools_have_run = "Tool result (" in prompt or any(
        "Tool result (" in ctx for ctx in additional_context
    )
    budget = max(0, min(max_corrections, 2))
    used = 0
    attempt_prompt = prompt
    attempt_context = additional_context
    while True:
        started = recorder.attempt_timer()
        try:
            text = await client.chat(attempt_prompt, attempt_context, session)
        except SubstrateDisengagedError:
            if used < budget:
                used += 1
                recorder.add_attempt(
                    started, guard=DISENGAGED, retried=True, status=STATUS_GUARD
                )
                session = None
                attempt_prompt = disengaged_retry_prompt(prompt)
                attempt_context = additional_context
                continue
            recorder.add_attempt(started, guard=DISENGAGED, status=STATUS_ERROR)
            return ToolParseOutcome(text=DISENGAGED_SENTINEL, guard=DISENGAGED)
        outcome = parse(text, allowed)
        if outcome.error is None:
            if outcome.tool_call is None and outcome.text:
                triggered = None
                if detect_confabulation(outcome.text):
                    triggered = CONFABULATION
                elif not tools_have_run and detect_hallucinated_completion(outcome.text):
                    triggered = HALLUCINATED_COMPLETION
                if triggered is not None:
                    if used < budget:
                        used += 1
                        recorder.add_attempt(
                            started, guard=triggered, retried=True,
                            status=STATUS_GUARD, text=text,
                        )
                        attempt_prompt = guard_retry_prompt(triggered)
                        attempt_context = _retry_context(additional_context, prompt, text)
                        continue
                    outcome.guard = triggered
                    recorder.add_attempt(
                        started, guard=triggered, status=STATUS_GUARD, text=text
                    )
                    return outcome
            recorder.add_attempt(started, status=STATUS_OK, text=text)
            return outcome
        if used < budget:
            used += 1
            recorder.add_attempt(
                started, guard=TOOL_PARSE_FAILURE, retried=True,
                status=STATUS_GUARD, text=text,
            )
            strict = budget > 1 and used == budget
            attempt_prompt = correction_prompt(outcome.error, strict=strict)
            attempt_context = _retry_context(additional_context, prompt, text)
            continue
        recorder.add_attempt(
            started, guard=TOOL_PARSE_FAILURE, status=STATUS_ERROR, text=text
        )
        return ToolParseOutcome(text=TOOL_FAILURE_SENTINEL, guard=TOOL_PARSE_FAILURE)


def _tool_outcome_completion(
    model_alias: str,
    outcome: ToolParseOutcome,
    input_text: str = "",
    completion_id: str | None = None,
) -> dict:
    completion_text = outcome.text or ""
    if outcome.tool_calls:
        calls = [tc.as_openai() for tc in outcome.tool_calls]
        completion_text = completion_text + "".join(
            c["function"]["arguments"] for c in calls
        )
        message = {
            "role": "assistant",
            "content": outcome.text or None,
            "tool_calls": calls,
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": outcome.text}
        finish_reason = "stop"
    body = {
        "id": completion_id or f"chatcmpl_{uuid.uuid4().hex}",
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
        "usage": openai_usage(input_text, completion_text),
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
    allow_parallel: bool = False,
    keepalive_interval: float = 15.0,
    chunk_chars: int = 24,
    chunk_delay_ms: int = 0,
    input_text: str = "",
    recorder=_NULL_RECORDER,
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
            client,
            prompt,
            additional_context,
            tools,
            session,
            max_corrections,
            allow_parallel,
            recorder=recorder,
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
            recorder.finish(
                status=STATUS_ERROR,
                input_text=input_text,
                error=str(exc),
                error_type=_substrate_error_type(exc),
            )
            yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
            return

        if outcome.tool_calls:
            calls = [tc.as_openai() for tc in outcome.tool_calls]
            recorder.record_tool_calls(calls)
            if outcome.text:
                recorder.stream_chunk()
                yield chunk({"content": outcome.text})
            recorder.stream_chunk()
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
            completion_text = (outcome.text or "") + "".join(
                c["function"]["arguments"] for c in calls
            )
            yield chunk({}, "tool_calls", {"usage": openai_usage(input_text, completion_text)})
        else:
            completion_text = outcome.text or ""
            if outcome.text:
                pieces = (
                    [outcome.text]
                    if outcome.guard is not None
                    else _split_stream_text(outcome.text, chunk_chars)
                )
                for index, piece in enumerate(pieces):
                    if index and chunk_delay_ms > 0:
                        await asyncio.sleep(chunk_delay_ms / 1000)
                    recorder.stream_chunk()
                    yield chunk({"content": piece})
            extra = {"usage": openai_usage(input_text, outcome.text or "")}
            if outcome.guard:
                extra["x_m365_guard"] = {"guard": outcome.guard, "retries_exhausted": True}
            yield chunk({}, "stop", extra)
        recorder.stream_complete()
        recorder.finish(
            status=STATUS_GUARD if outcome.guard else STATUS_OK,
            guard=outcome.guard,
            input_text=input_text,
            output_text=completion_text,
        )
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
    input_text: str = "",
    recorder=_NULL_RECORDER,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())
    full_text = ""
    started = recorder.attempt_timer()
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
            full_text += delta
            recorder.stream_chunk()
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_alias,
                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
    except SubstrateCopilotError as exc:
        recorder.add_attempt(started, status=STATUS_ERROR)
        recorder.finish(
            status=STATUS_ERROR,
            input_text=input_text,
            error=str(exc),
            error_type=_substrate_error_type(exc),
        )
        yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_error'}})}\n\n"
        yield "data: [DONE]\n\n"
        return
    recorder.add_attempt(started, status=STATUS_OK, text=full_text)
    recorder.stream_complete()
    recorder.finish(status=STATUS_OK, input_text=input_text, output_text=full_text)
    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_alias,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": openai_usage(input_text, full_text),
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"



