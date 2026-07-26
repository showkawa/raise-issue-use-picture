from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
import websockets

from .models import ImageInput
from .session_store import PersistentSession
from .telemetry import TurnTelemetry
from .token_store import decode_jwt_payload, is_substrate_token_claims

SIGNALR_SEP = "\x1e"
_WS_BASE = "wss://substrate.office.com/m365Copilot/Chathub"
_UPLOAD_URL = "https://substrate.office.com/m365Copilot/UploadFile"

# The web client sends these option sets on UploadFile to enable image ingestion;
# feature.EnableImageSupportInUploadFile (x-variants) is what actually turns the
# vision upload path on. Reverse-engineered from the live m365.cloud.microsoft UI.
_UPLOAD_OPTION_SETS = (
    "cwcgptvsan",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "gptvnorm2048",
)

_VARIANTS = (
    "EnableMcpServerWidgets,feature.EnableMcpServerWidgets,feature.EnableLuForChatCIQ,"
    "feature.enableChatCIQPlugin,EnableRequestPlugins,feature.EnableSensitivityLabels,"
    "EnableUnsupportedUrlDetector,feature.IsCustomEngineCopilotEnabled,feature.bizchatfluxv3,"
    "feature.enablechatpages,feature.enableCodeCanvas,feature.turnOnWorkTabRecommendation,"
    "feature.turnOnDARecommendation,feature.IsStreamingModeInChatRequestEnabled,"
    "IncludeSourceAttributionsConcise,SkipPublishEmptyMessage,"
    "feature.EnableDeduplicatingSourceAttributions,Enable3PActionProgressMessages,"
    "feature.enableClientWebRtc,feature.EnableMeetingRecapOfSeriesMeetingWithCiq,"
    "feature.EnableReferencesListCompleteSignal,feature.StorageMessageSplitDisabled,"
    "feature.EnableCuaTakeControlApi,SingletonEnvOn,feature.cwcallowedos,"
    "feature.EnableMergingPureDeltas,feature.disabledisallowedmsgs,"
    "feature.enableCitationsForSynthesisData,feature.EnableConversationShareApis,"
    "feature.enableGenerateGraphicArtOptionsSet,cdximagen,"
    "feature.EnableUpdatedUXForConfirmationDialog,"
    "feature.EnableContentApiandDocTypeHtmlInRichAnswers,"
    "cdxgrounding_api_v2_rich_web_answers_reference_bottom_force,"
    "cdxenablerenderforisocomp,feature.EnableClientFileURLSupportForOfficeWebPaidCopilot,"
    "feature.EnableDesignEditorImageGrounding,feature.EnableDesignerEditor,"
    "feature.EnableSkipRehydrationForSpeCIdImages,feature.EnableSkipEmittingMessageOnFlush,"
    "feature.EnableRemoveEmptySourceAttributions,feature.EnableRemoveStreamingMode,"
    "feature.OfficeWebToHelix,feature.OfficeDesktopToHelix,feature.M365TeamsHubToHelix,"
    "feature.OwaHubToHelix,feature.MonarchHubToHelix,feature.Win32OutlookHubToHelix,"
    "feature.MacOutlookHubToHelix,Agt_bizchat_enableGpt5ForHelix"
)

# Code-interpreter option sets are intentionally omitted: with them enabled
# Copilot assumes it has its own sandbox (hallucinating paths like /mnt/data)
# and answers in prose instead of emitting the client-side tool_call the
# OpenCode integration depends on.
_OPTIONS_SETS = [
    "search_result_progress_messages_with_search_queries",
    "cwc_flux_image",
    "cwcfluxgptv",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "cwc_fileupload_odb",
    "update_memory_plugin",
    "add_custom_instructions",
    "cwc_flux_v3",
    "flux_v3_progress_messages",
    "enable_batch_token_processing",
    "enable_gg_gpt",
    "flux_v3_image_gen_enable_dimensions",
    "flux_v3_image_gen_enable_icon_dimensions",
    "flux_v3_image_gen_enable_system_text_with_params",
    "flux_v3_image_gen_enable_designer_dimensions_meta_prompting_in_system_prompts",
]

_ALLOWED_MESSAGE_TYPES = [
    "Chat", "Suggestion", "InternalSearchQuery", "Disengaged",
    "InternalLoaderMessage", "Progress", "GeneratedCode", "RenderCardRequest",
    "AdsQuery", "SemanticSerp", "GenerateContentQuery", "GenerateGraphicArt",
    "SearchQuery", "ConfirmationCard", "AuthError", "DeveloperLogs",
    "TriggerPlugin", "HintInvocation", "MemoryUpdate", "EndOfRequest",
    "TriggerConfirmation", "ResumeInvokeAction", "ResumeUserInputRequest",
    "TriggerUserInputRequest", "EscapeHatch", "TriggerPluginAuth",
    "ResumePluginAuth", "SideBySide", "ReferencesListComplete",
    "SwitchRespondingEndpoint",
]


class SubstrateCopilotError(RuntimeError):
    pass


class SubstrateDisengagedError(SubstrateCopilotError):
    pass


class SubstrateThrottledError(SubstrateCopilotError):
    def __init__(self, message: str, retry_after: int = 30):
        super().__init__(message)
        self.retry_after = retry_after


class SubstrateCopilotClient:
    def __init__(
        self,
        access_token: str,
        time_zone: str = "Asia/Tokyo",
        proxy: str = "",
        tone: str = "Claude_Sonnet",
        throttle_retries: int = 2,
    ):
        if not access_token:
            raise SubstrateCopilotError(
                "M365_ACCESS_TOKEN is missing. Start the debug Chrome window and let startup token capture complete, "
                "or run `uv run teams-copilot-proxy set-token`."
            )
        self._token = access_token
        self._time_zone = time_zone
        self._proxy = proxy
        self.tone = tone
        self._throttle_retries = max(0, throttle_retries)
        # Set per-request by the app layer; consumed and referenced via message
        # annotations when the turn carries image attachments.
        self.images: list[ImageInput] = []
        # Best-effort decoding options (e.g. temperature/topP) forwarded into the
        # Chathub ``options`` object; empty means the web-client default ``{}``.
        self.options: dict[str, float] = {}
        # Facts about the most recent substrate round trip, read by the Monitor.
        self.last_turn: TurnTelemetry | None = None
        try:
            claims = decode_jwt_payload(access_token)
        except Exception as exc:
            raise SubstrateCopilotError(f"Cannot decode access token: {exc}") from exc
        if not is_substrate_token_claims(claims):
            raise SubstrateCopilotError("Access token is not a substrate.office.com token.")
        if time.time() > claims.get("exp", 0):
            raise SubstrateCopilotError(
                "Access token expired. To refresh: open M365 Copilot in your browser, "
                "DevTools â†’ Network â†’ filter 'substrate' â†’ click the WebSocket â†’ Headers â†’ "
                "copy the access_token= query param â†’ update M365_ACCESS_TOKEN in .env"
            )
        self._oid: str = claims["oid"]
        self._tid: str = claims["tid"]

    def _ws_url(self, conv_id: str, session_id: str, req_id: str) -> str:
        token = quote(self._token, safe="")
        return (
            f"{_WS_BASE}/{self._oid}@{self._tid}"
            f"?ClientRequestId={req_id}"
            f"&X-SessionId={session_id}"
            f"&ConversationId={conv_id}"
            f"&access_token={token}"
            f"&variants={_VARIANTS}"
            f"&source=officeweb&product=Office&agentHost=Bizchat.FullScreen"
            f"&licenseType=Starter&agent=web&scenario=OfficeWebIncludedCopilot"
        )

    async def _upload_image(
        self, conv_id: str, image: ImageInput
    ) -> tuple[str, str]:
        """Upload one image to the substrate UploadFile endpoint.

        Returns (doc_id, conversation_id). The server assigns its own document id
        and (for a fresh conversation) its own conversation id; the chat turn must
        use the returned conversation id and reference the image by doc id."""
        body, boundary = _encode_multipart([
            ("scenario", "UploadImage"),
            ("conversationId", conv_id),
            ("FileBase64", image.data_uri),
            *[("optionsSets", opt) for opt in _UPLOAD_OPTION_SETS],
        ])
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Origin": "https://m365.cloud.microsoft",
            "Referer": "https://m365.cloud.microsoft/",
            "x-anchormailbox": f"Oid:{self._oid}@{self._tid}",
            "x-scenario": "OfficeWebIncludedCopilot",
            "x-variants": "feature.EnableImageSupportInUploadFile",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=60, proxy=self._proxy or None, trust_env=True
            ) as client:
                response = await client.post(_UPLOAD_URL, headers=headers, content=body)
        except Exception as exc:
            raise SubstrateCopilotError(f"Image upload failed: {exc}") from exc
        if response.status_code == 429:
            raise SubstrateThrottledError("Substrate throttled the image upload (HTTP 429).")
        if response.status_code != 200:
            raise SubstrateCopilotError(
                f"Image upload rejected (HTTP {response.status_code}): {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SubstrateCopilotError(f"Image upload returned non-JSON: {exc}") from exc
        if (data.get("result") or {}).get("value") != "Success":
            raise SubstrateCopilotError(f"Image upload was not accepted: {data.get('result')}")
        doc_id = data.get("docId")
        if not doc_id:
            raise SubstrateCopilotError("Image upload response is missing docId.")
        return doc_id, data.get("conversationId") or conv_id

    async def _upload_images(
        self, conv_id: str, images: list[ImageInput]
    ) -> tuple[str, list[dict]]:
        """Upload all images to one conversation and build their annotations.

        Returns (conversation_id, annotations). Later uploads reuse the conversation
        id the server assigns to the first one so they attach to the same turn."""
        annotations: list[dict] = []
        for image in images:
            doc_id, conv_id = await self._upload_image(conv_id, image)
            annotations.append({
                "id": doc_id,
                "messageAnnotationMetadata": {
                    "@type": "File",
                    "annotationType": "File",
                    "fileType": image.file_type,
                    "fileName": image.filename,
                },
                "messageAnnotationType": "ImageFile",
            })
        return conv_id, annotations

    def _chat_invoke(
        self,
        text: str,
        conv_id: str,
        session_id: str,
        req_id: str,
        is_start_of_session: bool,
        annotations: list[dict] | None = None,
    ) -> str:
        option_sets = list(_OPTIONS_SETS)
        if annotations:
            option_sets.append("gptvnorm2048")
        payload = {
            "arguments": [{
                "source": "officeweb",
                "clientCorrelationId": req_id,
                "sessionId": session_id,
                "optionsSets": option_sets,
                "streamingMode": "ConciseWithPadding",
                "spokenTextMode": "None",
                "options": dict(self.options),
                "extraExtensionParameters": {},
                "allowedMessageTypes": _ALLOWED_MESSAGE_TYPES,
                "sliceIds": [],
                "threadLevelGptId": {},
                "traceId": req_id,
                "isStartOfSession": is_start_of_session,
                "clientInfo": {
                    "clientPlatform": "mcmcopilot-web",
                    "clientAppName": "Office",
                    "clientEntrypoint": "mcmcopilot-officeweb",
                    "clientSessionId": session_id,
                    "clientAppType": "Web",
                    "deviceOS": "Windows",
                    "deviceType": "Desktop",
                },
                "message": {
                    "author": "user",
                    "inputMethod": "Keyboard",
                    "text": text,
                    "entityAnnotationTypes": ["People", "File", "Event", "Email", "TeamsMessage"],
                    "requestId": req_id,
                    "locationInfo": {"timeZoneOffset": 9, "timeZone": self._time_zone},
                    "locale": "en-us",
                    "messageType": "Chat",
                    "experienceType": "Default",
                    "messageAnnotations": annotations or [],
                    "adaptiveCards": [],
                    "clientPreferences": {},
                },
                "plugins": [{"Id": "BingWebSearch", "Source": "BuiltIn"}],
                "isSbsSupported": True,
                "tone": self.tone,
                "renderReferencesBehindEOS": True,
            }],
            "invocationId": "0",
            "target": "chat",
            "type": 4,
        }
        return json.dumps(payload, ensure_ascii=False) + SIGNALR_SEP

    async def chat_stream(
        self,
        prompt: str,
        additional_context: list[str],
        session: PersistentSession | None = None,
    ) -> AsyncIterator[str]:
        text = _combine_text(prompt, additional_context)
        images = self.images
        if images:
            # Image turns must run on the conversation the UploadFile endpoint
            # assigns, so they bypass persistent-session reuse and start fresh.
            conv_id, annotations = await self._upload_images(str(uuid.uuid4()), images)
            async for chunk in self._chat_stream_for_turn(
                text=text,
                conv_id=conv_id,
                session_id=str(uuid.uuid4()),
                is_start_of_session=True,
                annotations=annotations,
            ):
                yield chunk
            return

        if session is None:
            async for chunk in self._chat_stream_for_turn(
                text=text,
                conv_id=str(uuid.uuid4()),
                session_id=str(uuid.uuid4()),
                is_start_of_session=True,
            ):
                yield chunk
            return

        async with session.lock:
            turn = session.reserve_turn()
            async for chunk in self._chat_stream_for_turn(
                text=text,
                conv_id=turn.conversation_id,
                session_id=turn.client_session_id,
                is_start_of_session=turn.is_start_of_session,
            ):
                yield chunk

    async def _chat_stream_for_turn(
        self,
        text: str,
        conv_id: str,
        session_id: str,
        is_start_of_session: bool,
        annotations: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        req_id = str(uuid.uuid4())
        url = self._ws_url(conv_id, session_id, req_id)
        turn = TurnTelemetry(
            conversation_id=conv_id,
            client_request_id=req_id,
            substrate_session_id=session_id,
            start_of_session=is_start_of_session,
            images=len(annotations or []),
            option_sets=len(_OPTIONS_SETS) + (1 if annotations else 0),
        )
        turn.mark_sent(text)
        self.last_turn = turn
        started = time.perf_counter()
        try:
            async with websockets.connect(
                url,
                additional_headers={
                    "Origin": "https://m365.cloud.microsoft",
                },
                proxy=self._proxy or True,
            ) as ws:
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + SIGNALR_SEP)
                await ws.recv()
                await ws.send(self._chat_invoke(
                    text, conv_id, session_id, req_id, is_start_of_session, annotations
                ))
                fallback_text = ""
                yielded_any = False
                async for raw in ws:
                    for part in raw.split(SIGNALR_SEP):
                        part = part.strip()
                        if not part:
                            continue
                        try:
                            msg = json.loads(part)
                        except json.JSONDecodeError:
                            continue
                        t = msg.get("type")
                        turn.mark_frame(int((time.perf_counter() - started) * 1000))
                        if t == 6:
                            continue
                        if t == 1 and msg.get("target") == "update":
                            args = (msg.get("arguments") or [{}])[0]
                            delta = args.get("writeAtCursor")
                            if delta:
                                if not yielded_any and fallback_text:
                                    yield fallback_text
                                yielded_any = True
                                turn.reply_bytes += len(delta.encode("utf-8"))
                                yield delta
                            msgs = args.get("messages")
                            if msgs:
                                entries = msgs if isinstance(msgs, list) else [msgs]
                                for entry in reversed(entries):
                                    if entry.get("author") != "user":
                                        turn.mark_message(entry)
                                        _raise_if_disengaged(entry)
                                        fallback_text = entry.get("text", "")
                                        break
                        if t == 2:
                            item_msgs = (msg.get("item") or {}).get("messages") or []
                            for entry in reversed(item_msgs):
                                if entry.get("author") != "user":
                                    turn.mark_message(entry)
                                    _raise_if_disengaged(entry)
                                    fallback_text = entry.get("text", "")
                                    break
                        if t == 3:
                            turn.terminated_cleanly = True
                            if not yielded_any and fallback_text:
                                turn.reply_bytes = len(fallback_text.encode("utf-8"))
                                yield fallback_text
                            return
        except SubstrateCopilotError:
            raise
        except Exception as exc:
            response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
            status = response.status_code if response is not None else None
            turn.upstream_status = status
            turn.close_reason = f"{type(exc).__name__}: {exc}"[:200]
            if status == 429:
                raise SubstrateThrottledError(
                    "Substrate throttled the request (HTTP 429)."
                ) from exc
            raise SubstrateCopilotError(str(exc)) from exc

    async def chat(
        self,
        prompt: str,
        additional_context: list[str],
        session: PersistentSession | None = None,
    ) -> str:
        # HTTP 429 backoff: safe to retry the whole turn only while nothing has
        # been received yet; a partially consumed stream is surfaced as-is.
        throttles = 0
        while True:
            chunks: list[str] = []
            try:
                async for chunk in self.chat_stream(
                    prompt, additional_context, session
                ):
                    chunks.append(chunk)
                return "".join(chunks)
            except SubstrateThrottledError as exc:
                if chunks or throttles >= self._throttle_retries:
                    raise
                throttles += 1
                await asyncio.sleep(min(exc.retry_after, 20) * throttles)


def _encode_multipart(fields: list[tuple[str, str]]) -> tuple[bytes, str]:
    """Encode text form fields as multipart/form-data, mirroring the browser's
    UploadFile request (repeated field names are allowed)."""
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    parts: list[str] = []
    for name, value in fields:
        parts.append(f"--{boundary}\r\n")
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        parts.append(f"{value}\r\n")
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8"), boundary


def _raise_if_disengaged(entry: dict) -> None:
    if entry.get("messageType") == "Disengaged":
        raise SubstrateDisengagedError(
            "Copilot's safety filter disengaged from this request."
        )


def _combine_text(prompt: str, context: list[str]) -> str:
    if not context:
        return prompt
    return "\n\n".join(context) + "\n\n---\n\n" + prompt
