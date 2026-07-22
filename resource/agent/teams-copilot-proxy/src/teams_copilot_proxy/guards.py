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
        r"(?:无法|不能|没办法)(?:直接)?(?:访问|读取|打开|查看|浏览)(?:你的|您的|本地|该)?(?:文件|文件系统|目录|文件夹|代码库|仓库|电脑|机器)",
        r"请(?:把|将)?(?:文件|代码|内容)(?:粘贴|贴|发给我|提供|上传)",
    )
]

_HALLUCINATED_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bI(?: have|'ve) (?:now |already |successfully )?"
        r"(?:created|updated|modified|written|edited|saved|deleted|removed|renamed|added|applied)\b",
        r"\bthe (?:files?|changes?) (?:has|have|was|were) been "
        r"(?:created|updated|modified|written|edited|saved|deleted|removed|renamed|applied)\b",
        r"(?:已经|已)(?:成功)?(?:为你|为您|帮你|帮您)?(?:创建|更新|修改|写入|编辑|保存|删除|重命名|添加|应用)",
    )
]


def detect_confabulation(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONFAB_RES)


def detect_hallucinated_completion(text: str) -> bool:
    return any(pattern.search(text) for pattern in _HALLUCINATED_RES)


def guard_retry_prompt(guard: str) -> str:
    if guard == CONFABULATION:
        return (
            "You claimed you cannot access files or the local environment, but the client "
            "executes the available tools on the user's machine for you, so you DO have "
            "access through them. Do not ask the user to paste anything. To proceed you "
            "MUST reply with ONLY one fenced tool_call block invoking the appropriate tool."
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
