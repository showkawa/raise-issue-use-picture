from __future__ import annotations

import re

CONFABULATION = "confabulation"
HALLUCINATED_COMPLETION = "hallucinated_completion"
DISENGAGED = "disengaged"
TOOL_PARSE_FAILURE = "tool_parse_failure"

DISENGAGED_SENTINEL = (
    "[teams-copilot-proxy] Copilot's safety filter disengaged from this request after "
    "retries. Please rephrase the request."
)

_CONFAB_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:can(?:no|')t|cannot|unable to|don't have the ability to|do not have the ability to)"
        r" (?:directly )?(?:access|open|read|see|view|browse)"
        r" (?:(?:your|the|local|this)\s+)*(?:files?|file system|filesystem|director(?:y|ies)|folders?|codebase|repositor(?:y|ies)|machine|computer)",
        r"\bdon'?t have (?:direct )?access to (?:your|the|local|this)\b",
        r"\bplease (?:paste|share|provide|upload|attach) (?:the |your )?(?:files?|code|contents?|snippets?)\b",
        r"\bplease (?:attach|mount|provide|upload) (?:or \w+ )?(?:the |your )?(?:repositor(?:y|ies)|repo|files?|workspace|project|codebase)\b",
        r"\b(?:attach|mount)(?:ed|ing)? (?:or \w+ )?(?:the |your )?(?:repositor(?:y|ies)|repo|workspace|codebase)\b",
        r"/mnt\b",
        r"https?://[^\s)]*asyncgw\.teams\.microsoft\.com[^\s)]*",
        r"/v1/objects/",
        r"\bnot (?:inside )?a git repository\b",
        r"\b(?:not available|unavailable) in this (?:chat|session|conversation|environment)\b",
        r"\bcan.?t (?:directly )?use the (?:external|client.side|provided)\b[^.]{0,40}\btools?\b",
        r"\btools? (?:described|listed|mentioned) in your message\b",
        r"\b(?:could not|couldn.?t|cannot|can.?t|failed to) (?:be )?(?:run|executed?)\b",
        r"\b(?:was|were|is|are) not found in\b[^.]{0,60}\b(?:director(?:y|ies)|project|workspace|repositor(?:y|ies)|codebase)\b",
        r"\bfrom this execution environment\b",
        r"\b(?:available |the )?(?:workspace|execution environment)\b[^.]{0,40}\bis empty\b",
        r"\b(?:accessible|available)\b[^.]{0,25}\bin (?:the |your |this )?(?:project |current )?(?:workspace|repositor(?:y|ies)|environment|director(?:y|ies))\b",
        r"\bno\b[^.]{0,40}\bfile\b[^.]{0,40}\b(?:accessible|available)\b",
        r"\b(?:is|are)(?: not|n'?t)[^.]{0,25}\b(?:accessible|available)\b[^.]{0,25}\b(?:workspace|repositor(?:y|ies)|project|director(?:y|ies)|environment)\b",
        r"\b(?:accessible|available) (?:project |current )?(?:workspace|repositor(?:y|ies)|director(?:y|ies))\b",
        r"\b(?:couldn.?t|could not|can.?t|cannot|unable to|was unable to|failed to) (?:locate|find|access|see|open)\b[^.]{0,50}\b(?:repositor(?:y|ies)|project|files?|entry point|codebase|workspace|director(?:y|ies))\b",
        r"\bmake (?:the |your )?(?:repositor(?:y|ies)|project|files?|workspace|codebase) available\b",
        r"\bno (?:repositor(?:y|ies)|project|source) files?\b",
        r"\bno main entry point\b",
        r"(?:æ— æ³•|ä¸èƒ½|æ²¡åŠžæ³•)(?:ç›´æŽ¥)?(?:è®¿é—®|è¯»å–|æ‰“å¼€|æŸ¥çœ‹|æµè§ˆ)(?:ä½ çš„|æ‚¨çš„|æœ¬åœ°|è¯¥)?(?:æ–‡ä»¶|æ–‡ä»¶ç³»ç»Ÿ|ç›®å½•|æ–‡ä»¶å¤¹|ä»£ç åº“|ä»“åº“|ç”µè„‘|æœºå™¨)",
        r"\u8bf7(?:\u628a|\u5c06)?(?:\u6587\u4ef6|\u4ee3\u7801|\u5185\u5bb9)(?:\u7c98\u8d34|\u8d34|\u53d1\u7ed9\u6211|\u63d0\u4f9b|\u4e0a\u4f20)",
    )
]

_HALLUCINATED_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bI(?: have|'ve) (?:now |already |successfully )?"
        r"(?:created|updated|modified|written|edited|saved|deleted|removed|renamed|added|applied)\b",
        r"\bthe (?:files?|changes?) (?:has|have|was|were) been "
        r"(?:created|updated|modified|written|edited|saved|deleted|removed|renamed|applied)\b",
        r"\b(?:created|wrote|saved|updated)(?: the)? file\b",
        r"\b(?:file|it) is now (?:created|written|saved|updated|in place|on disk)\b",
        r"\u6587\u4ef6\u5df2(?:\u7ecf)?(?:\u6210\u529f)?(?:\u521b\u5efa|\u4fdd\u5b58|\u5199\u5165|\u843d\u76d8|\u66f4\u65b0)",
        r"(?:\u5df2\u7ecf|\u5df2)(?:\u6210\u529f)?(?:\u4e3a\u4f60|\u4e3a\u60a8|\u5e2e\u4f60|\u5e2e\u60a8)?(?:\u521b\u5efa|\u66f4\u65b0|\u4fee\u6539|\u5199\u5165|\u7f16\u8f91|\u4fdd\u5b58|\u5220\u9664|\u91cd\u547d\u540d|\u6dfb\u52a0|\u5e94\u7528)",
    )
]


def detect_confabulation(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONFAB_RES)


def detect_hallucinated_completion(text: str) -> bool:
    return any(pattern.search(text) for pattern in _HALLUCINATED_RES)


def guard_retry_prompt(guard: str) -> str:
    if guard == CONFABULATION:
        return (
            "You have no sandbox, no /mnt/data, and no execution environment of your own, "
            "and the user's files are NOT empty, missing, unavailable, or inaccessible. "
            "The client executes the available tools on the user's machine for you, so "
            "you DO have access to the repository through them. Do not claim any file is "
            "unavailable; if you need a file, call read/list to fetch it. Do not ask the "
            "user to paste, upload, attach, or mount anything, and do not reference any "
            "server-side path. You also cannot run commands yourself: never print "
            "invented command output; to run a command, emit the matching tool_call "
            "(e.g. bash) and wait for the client's result. To proceed you "
            "MUST reply with ONLY one fenced tool_call block invoking the appropriate tool "
            "(e.g. list/glob/read) against the user's project path."
        )
    return (
        "You claimed the work is already done, but you did not emit any tool call, so "
        "nothing has actually happened. To perform the action you MUST reply with ONLY "
        "one fenced tool_call block invoking the appropriate tool."
    )


def disengaged_retry_prompt(original_prompt: str) -> str:
    return (
        "Please help with the following work request from a software developer:\n\n"
        + original_prompt
    )
