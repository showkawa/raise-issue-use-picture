"""Proxy Monitor (阶段二) — 进程内可观测子系统.

事件总线（有界队列）→ SQLite sink → 只读 /monitor API。设计原则见
docs/adr/0007-self-hosted-sqlite-monitor.md 与 docs/monitor-plan.md：

- Monitor 埋点异常绝不影响主链路（chat completion）；
- 有界队列满则丢弃监控事件并限频告警，不阻塞主链路；
- 内容留存由 Capture 档位控制（off/failures/all），失败/守卫触发才留脱敏现场。
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .usage import estimate_tokens

logger = logging.getLogger(__name__)

# Capture 档位
CAPTURE_OFF = "off"
CAPTURE_FAILURES = "failures"
CAPTURE_ALL = "all"
_VALID_CAPTURE = {CAPTURE_OFF, CAPTURE_FAILURES, CAPTURE_ALL}

# 每条现场文本截断上限（约 2KB）
_SNIPPET_LIMIT = 2048

# 请求最终状态
STATUS_OK = "ok"
STATUS_GUARD = "guard"
STATUS_ERROR = "error"

# 工具分类（OpenCode 视角）
_BUILTIN_TOOLS = {
    "read",
    "write",
    "edit",
    "multiedit",
    "bash",
    "glob",
    "grep",
    "list",
    "ls",
    "patch",
    "question",
}


def tool_category(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("mcp__") or lowered.startswith("mcp_"):
        return "mcp"
    if lowered == "task":
        return "task"
    if lowered == "skill" or lowered.startswith("skill_"):
        return "skill"
    if lowered in ("todowrite", "todoread"):
        return "todowrite"
    if lowered in ("webfetch", "websearch", "web_search", "web_fetch"):
        return "webfetch"
    if lowered in _BUILTIN_TOOLS:
        return "builtin"
    return "other"


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile over an already-sorted list; None when empty."""
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def _truncate(text: str | None, limit: int = _SNIPPET_LIMIT) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


@dataclass
class AttemptRecord:
    """一次 chat completion 请求内部的单次 substrate 往返。"""

    seq: int
    duration_ms: int
    guard: str | None = None
    retried: bool = False
    status: str = STATUS_OK
    text: str | None = None


@dataclass
class ToolCallRecord:
    """模型发起的一次 tool_call（由 OpenCode 在客户端本地执行）。"""

    call_id: str
    name: str
    category: str
    args_bytes: int


@dataclass
class ToolResultRecord:
    """下一轮请求 transcript 中出现的工具执行结果（轻量闭环：只记 error 与字节数）。"""

    call_id: str | None
    name: str | None
    is_error: bool
    result_bytes: int


@dataclass
class StreamStats:
    """流式请求的聚合指标（不逐 chunk 落库）。"""

    first_chunk_ms: int | None = None
    chunk_count: int = 0
    avg_chunk_interval_ms: int | None = None
    complete: bool = False


@dataclass
class RequestRecord:
    """一次 /v1/chat/completions 请求的聚合监控记录。"""

    id: str
    ts: float
    session_key: str | None
    model: str
    tone: str
    stream: bool
    status: str = STATUS_OK
    guard: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None
    error_type: str | None = None
    prompt_summary: str | None = None
    reply_snippet: str | None = None
    # Tool-planning telemetry (baseline for a later router-vs-single comparison).
    had_tools: bool = False
    planning_mode: str = "single"
    shell_recovered: int = 0
    deduped: int = 0
    repeated_call: bool = False
    repeated_failure: bool = False
    attempts: list[AttemptRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_results: list[ToolResultRecord] = field(default_factory=list)
    stream_stats: StreamStats = field(default_factory=StreamStats)


class RequestRecorder:
    """在单次请求处理期间累积 attempt，结束时向总线发出一条完整记录。

    在主链路上是纯内存操作；``finish`` 只做一次非阻塞 emit。所有方法都吞掉异常，
    确保监控永不影响 chat completion。
    """

    def __init__(
        self,
        bus: MonitorBus,
        *,
        request_id: str,
        session_key: str | None,
        model: str,
        tone: str,
        stream: bool,
    ) -> None:
        self._bus = bus
        self._t0 = time.perf_counter()
        self._seq = 0
        self.record = RequestRecord(
            id=request_id,
            ts=time.time(),
            session_key=session_key,
            model=model,
            tone=tone,
            stream=stream,
        )

    def attempt_timer(self) -> float:
        return time.perf_counter()

    def record_tool_calls(self, calls: list[dict]) -> None:
        """记录本次回复中模型发起的 tool_call（OpenAI 格式）。"""
        try:
            for call in calls:
                function = call.get("function", {})
                name = function.get("name", "")
                self.record.tool_calls.append(
                    ToolCallRecord(
                        call_id=call.get("id", ""),
                        name=name,
                        category=tool_category(name),
                        args_bytes=len(
                            function.get("arguments", "").encode("utf-8")
                        ),
                    )
                )
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor record_tool_calls failed", exc_info=True)

    def record_tool_result(
        self,
        *,
        call_id: str | None,
        name: str | None,
        is_error: bool,
        result_bytes: int,
    ) -> None:
        """记录本次请求 transcript 里携带的工具执行结果，用于闭环配对。"""
        try:
            self.record.tool_results.append(
                ToolResultRecord(
                    call_id=call_id,
                    name=name,
                    is_error=is_error,
                    result_bytes=result_bytes,
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor record_tool_result failed", exc_info=True)

    def mark_tool_turn(self, *, planning_mode: str = "single") -> None:
        """Flag that this request carried tool definitions (the denominator for
        tool-call reliability) and record which planning mode produced it."""
        try:
            self.record.had_tools = True
            self.record.planning_mode = planning_mode
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor mark_tool_turn failed", exc_info=True)

    def mark_ledger(self, *, repeated_call: bool, repeated_failure: bool) -> None:
        try:
            self.record.repeated_call = repeated_call
            self.record.repeated_failure = repeated_failure
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor mark_ledger failed", exc_info=True)

    def add_shell_recovery(self, count: int = 1) -> None:
        try:
            self.record.shell_recovered += count
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor add_shell_recovery failed", exc_info=True)

    def add_dedup(self, count: int) -> None:
        try:
            if count > 0:
                self.record.deduped += count
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor add_dedup failed", exc_info=True)

    def stream_chunk(self) -> None:
        """每个 SSE 内容 chunk 调一次；只更新聚合指标，不落库。"""
        try:
            stats = self.record.stream_stats
            now = time.perf_counter()
            if stats.first_chunk_ms is None:
                stats.first_chunk_ms = int((now - self._t0) * 1000)
            stats.chunk_count += 1
            elapsed_ms = (now - self._t0) * 1000 - stats.first_chunk_ms
            if stats.chunk_count > 1:
                stats.avg_chunk_interval_ms = int(
                    elapsed_ms / (stats.chunk_count - 1)
                )
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor stream_chunk failed", exc_info=True)

    def stream_complete(self) -> None:
        """流式回复正常发出 [DONE] 时调用。"""
        self.record.stream_stats.complete = True

    def add_attempt(
        self,
        started: float,
        *,
        guard: str | None = None,
        retried: bool = False,
        status: str = STATUS_OK,
        text: str | None = None,
    ) -> None:
        try:
            self._seq += 1
            self.record.attempts.append(
                AttemptRecord(
                    seq=self._seq,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    guard=guard,
                    retried=retried,
                    status=status,
                    text=text,
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor add_attempt failed", exc_info=True)

    def finish(
        self,
        *,
        status: str,
        guard: str | None = None,
        input_text: str = "",
        output_text: str = "",
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        try:
            rec = self.record
            rec.status = status
            rec.guard = guard
            rec.error = _truncate(error, 512)
            rec.error_type = error_type
            rec.duration_ms = int((time.perf_counter() - self._t0) * 1000)
            rec.prompt_tokens = estimate_tokens(input_text)
            rec.completion_tokens = estimate_tokens(output_text)
            rec.total_tokens = rec.prompt_tokens + rec.completion_tokens
            self._apply_capture(input_text, output_text)
            self._bus.emit(rec)
        except Exception:  # pragma: no cover - defensive
            logger.debug("monitor finish failed", exc_info=True)

    def _apply_capture(self, input_text: str, output_text: str) -> None:
        capture = self._bus.capture
        rec = self.record
        keep = capture == CAPTURE_ALL or (
            capture == CAPTURE_FAILURES and rec.status != STATUS_OK
        )
        if not keep:
            # off 档、或 failures 档下的正常请求：不保留任何内容现场
            for attempt in rec.attempts:
                attempt.text = None
            return
        rec.prompt_summary = _truncate(input_text)
        rec.reply_snippet = _truncate(output_text)
        for attempt in rec.attempts:
            attempt.text = _truncate(attempt.text)


class _NullRecorder:
    """Monitor 关闭时使用的空记录器：所有调用都是 no-op。"""

    def attempt_timer(self) -> float:
        return 0.0

    def add_attempt(self, *args, **kwargs) -> None:
        return None

    def record_tool_calls(self, calls: list[dict]) -> None:
        return None

    def record_tool_result(self, **kwargs) -> None:
        return None

    def mark_tool_turn(self, *args, **kwargs) -> None:
        return None

    def mark_ledger(self, *args, **kwargs) -> None:
        return None

    def add_shell_recovery(self, *args, **kwargs) -> None:
        return None

    def add_dedup(self, *args, **kwargs) -> None:
        return None

    def stream_chunk(self) -> None:
        return None

    def stream_complete(self) -> None:
        return None

    def finish(self, *args, **kwargs) -> None:
        return None


class SQLiteSink:
    """把 RequestRecord 写入单文件 SQLite（WAL），并做保留期清理。"""

    def __init__(self, db_path: str, retention_days: int) -> None:
        self.db_path = db_path
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._writes = 0
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self.cleanup()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    session_key TEXT,
                    model TEXT,
                    tone TEXT,
                    stream INTEGER,
                    status TEXT,
                    guard TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    duration_ms INTEGER,
                    error TEXT,
                    error_type TEXT,
                    prompt_summary TEXT,
                    reply_snippet TEXT,
                    first_chunk_ms INTEGER,
                    chunk_count INTEGER,
                    avg_chunk_interval_ms INTEGER,
                    stream_complete INTEGER,
                    had_tools INTEGER,
                    planning_mode TEXT,
                    shell_recovered INTEGER,
                    deduped INTEGER,
                    repeated_call INTEGER,
                    repeated_failure INTEGER
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    session_key TEXT,
                    ts REAL NOT NULL,
                    name TEXT,
                    category TEXT,
                    args_bytes INTEGER,
                    result_error INTEGER,
                    result_bytes INTEGER
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    request_id TEXT,
                    session_key TEXT,
                    type TEXT NOT NULL,
                    detail TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    request_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    duration_ms INTEGER,
                    guard TEXT,
                    retried INTEGER,
                    status TEXT,
                    text TEXT,
                    PRIMARY KEY (request_id, seq)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_key)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_calls_session "
                "ON tool_calls(session_key, name)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)"
            )
        self._migrate_requests()

    def _migrate_requests(self) -> None:
        """Additive column migration so databases created before the tool-planning
        telemetry gain the new columns without dropping existing rows."""
        wanted = {
            "had_tools": "INTEGER",
            "planning_mode": "TEXT",
            "shell_recovered": "INTEGER",
            "deduped": "INTEGER",
            "repeated_call": "INTEGER",
            "repeated_failure": "INTEGER",
        }
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(requests)").fetchall()
        }
        with self._conn:
            for column, col_type in wanted.items():
                if column not in existing:
                    self._conn.execute(
                        f"ALTER TABLE requests ADD COLUMN {column} {col_type}"
                    )

    def write(self, rec: RequestRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO requests (
                    id, ts, session_key, model, tone, stream, status, guard,
                    prompt_tokens, completion_tokens, total_tokens, duration_ms,
                    error, error_type, prompt_summary, reply_snippet,
                    first_chunk_ms, chunk_count, avg_chunk_interval_ms,
                    stream_complete, had_tools, planning_mode, shell_recovered,
                    deduped, repeated_call, repeated_failure
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.id,
                    rec.ts,
                    rec.session_key,
                    rec.model,
                    rec.tone,
                    1 if rec.stream else 0,
                    rec.status,
                    rec.guard,
                    rec.prompt_tokens,
                    rec.completion_tokens,
                    rec.total_tokens,
                    rec.duration_ms,
                    rec.error,
                    rec.error_type,
                    rec.prompt_summary,
                    rec.reply_snippet,
                    rec.stream_stats.first_chunk_ms,
                    rec.stream_stats.chunk_count if rec.stream else None,
                    rec.stream_stats.avg_chunk_interval_ms,
                    (1 if rec.stream_stats.complete else 0) if rec.stream else None,
                    1 if rec.had_tools else 0,
                    rec.planning_mode,
                    rec.shell_recovered,
                    rec.deduped,
                    1 if rec.repeated_call else 0,
                    1 if rec.repeated_failure else 0,
                ),
            )
            for call in rec.tool_calls:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO tool_calls (
                        call_id, request_id, session_key, ts, name, category,
                        args_bytes, result_error, result_bytes
                    ) VALUES (?,?,?,?,?,?,?,NULL,NULL)
                    """,
                    (
                        call.call_id,
                        rec.id,
                        rec.session_key,
                        rec.ts,
                        call.name,
                        call.category,
                        call.args_bytes,
                    ),
                )
            for result in rec.tool_results:
                self._close_tool_call(rec.session_key, result)
            self._derive_events(rec)
            for a in rec.attempts:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO attempts (
                        request_id, seq, duration_ms, guard, retried, status, text
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (rec.id, a.seq, a.duration_ms, a.guard, 1 if a.retried else 0, a.status, a.text),
                )
        self._writes += 1
        if self._writes % 50 == 0:
            self.cleanup()

    def _close_tool_call(self, session_key: str | None, result: ToolResultRecord) -> None:
        """把工具结果配对回未闭环的 tool_call：优先 call_id，其次同会话同名最早一条；
        配不上则留空（OpenCode 每轮重发全量 transcript，只更新未闭环行即可天然去重）。"""
        error_flag = 1 if result.is_error else 0
        if result.call_id:
            cursor = self._conn.execute(
                "UPDATE tool_calls SET result_error = ?, result_bytes = ? "
                "WHERE call_id = ? AND result_bytes IS NULL",
                (error_flag, result.result_bytes, result.call_id),
            )
            if cursor.rowcount:
                return
            # call_id 已闭环（transcript 重发）或未知：尝试名称配对前先确认未闭环过
            known = self._conn.execute(
                "SELECT 1 FROM tool_calls WHERE call_id = ?", (result.call_id,)
            ).fetchone()
            if known:
                return
        if not result.name:
            return
        row = self._conn.execute(
            "SELECT call_id FROM tool_calls WHERE session_key = ? AND name = ? "
            "AND result_bytes IS NULL ORDER BY ts LIMIT 1",
            (session_key, result.name),
        ).fetchone()
        if row:
            self._conn.execute(
                "UPDATE tool_calls SET result_error = ?, result_bytes = ? "
                "WHERE call_id = ?",
                (error_flag, result.result_bytes, row[0]),
            )

    def _derive_events(self, rec: RequestRecord) -> None:
        """从请求记录派生错误/守卫时间线事件。"""
        events: list[tuple[str, str | None]] = []
        if rec.status == STATUS_ERROR:
            events.append((rec.error_type or "upstream_error", rec.error))
        for attempt in rec.attempts:
            if attempt.guard:
                detail = attempt.guard + (" (retried)" if attempt.retried else "")
                events.append(("guard", detail))
        if (
            rec.stream
            and rec.status == STATUS_OK
            and not rec.stream_stats.complete
        ):
            events.append(("stream_incomplete", None))
        for event_type, detail in events:
            self._conn.execute(
                "INSERT INTO events (ts, request_id, session_key, type, detail) "
                "VALUES (?,?,?,?,?)",
                (rec.ts, rec.id, rec.session_key, event_type, detail),
            )

    def cleanup(self) -> None:
        if not self.retention_days or self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        with self._lock, self._conn:
            ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT id FROM requests WHERE ts < ?", (cutoff,)
                ).fetchall()
            ]
            if ids:
                self._conn.executemany(
                    "DELETE FROM attempts WHERE request_id = ?", [(i,) for i in ids]
                )
                self._conn.execute(
                    "DELETE FROM requests WHERE ts < ?", (cutoff,)
                )
            self._conn.execute("DELETE FROM tool_calls WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))

    # --- 只读查询（/monitor API 使用）---

    def _readonly_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def summary(self) -> dict:
        conn = self._readonly_conn()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), 0) AS errors,
                    COALESCE(SUM(CASE WHEN guard IS NOT NULL THEN 1 ELSE 0 END), 0) AS guarded
                FROM requests
                """
            ).fetchone()
            tones = [
                {"tone": r["tone"], "count": r["count"]}
                for r in conn.execute(
                    "SELECT tone, COUNT(*) AS count FROM requests GROUP BY tone ORDER BY count DESC"
                ).fetchall()
            ]
            data = dict(row)
            total = data["requests"] or 0
            data["error_rate"] = (data["errors"] / total) if total else 0.0
            data["guard_rate"] = (data["guarded"] / total) if total else 0.0
            data["tones"] = tones
            return data
        finally:
            conn.close()

    def requests(self, limit: int = 50, session: str | None = None) -> list[dict]:
        conn = self._readonly_conn()
        try:
            if session:
                rows = conn.execute(
                    "SELECT * FROM requests WHERE session_key = ? ORDER BY ts DESC LIMIT ?",
                    (session, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM requests ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def tools(self) -> list[dict]:
        """工具调用排行：调用数、闭环数、失败数与失败率（按已闭环计算）。"""
        conn = self._readonly_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    name,
                    category,
                    COUNT(*) AS calls,
                    SUM(CASE WHEN result_bytes IS NOT NULL THEN 1 ELSE 0 END)
                        AS closed,
                    SUM(CASE WHEN result_error = 1 THEN 1 ELSE 0 END) AS errors,
                    COALESCE(SUM(result_bytes), 0) AS result_bytes
                FROM tool_calls
                GROUP BY name, category
                ORDER BY calls DESC
                """
            ).fetchall()
            tools = []
            for row in rows:
                data = dict(row)
                closed = data["closed"] or 0
                data["error_rate"] = (data["errors"] / closed) if closed else 0.0
                tools.append(data)
            return tools
        finally:
            conn.close()

    def tool_efficiency(self) -> list[dict]:
        """Per-planning-mode tool-call reliability & cost, grouped by
        ``planning_mode`` over tool-bearing requests only. This is the baseline a
        later router mode can be A/B'd against: tool-call yield, correction /
        guard / error rates, round-trips and latency (p50/p95), plus how often
        the ledger/shell/dedup mechanisms fired.
        """
        conn = self._readonly_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(r.planning_mode, 'single') AS planning_mode,
                    COUNT(*) AS requests,
                    SUM(CASE WHEN tc.calls > 0 THEN 1 ELSE 0 END) AS with_tool_call,
                    SUM(CASE WHEN r.guard IS NOT NULL THEN 1 ELSE 0 END) AS guarded,
                    SUM(CASE WHEN r.status = 'error' THEN 1 ELSE 0 END) AS errors,
                    COALESCE(SUM(at.attempts), 0) AS attempts,
                    COALESCE(SUM(at.retries), 0) AS corrections,
                    COALESCE(SUM(r.shell_recovered), 0) AS shell_recovered,
                    COALESCE(SUM(r.deduped), 0) AS deduped,
                    COALESCE(SUM(r.repeated_call), 0) AS repeated_call,
                    COALESCE(SUM(r.repeated_failure), 0) AS repeated_failure,
                    COALESCE(SUM(r.total_tokens), 0) AS total_tokens
                FROM requests r
                LEFT JOIN (
                    SELECT request_id, COUNT(*) AS attempts,
                        SUM(retried) AS retries
                    FROM attempts GROUP BY request_id
                ) at ON at.request_id = r.id
                LEFT JOIN (
                    SELECT request_id, COUNT(*) AS calls
                    FROM tool_calls GROUP BY request_id
                ) tc ON tc.request_id = r.id
                WHERE r.had_tools = 1
                GROUP BY COALESCE(r.planning_mode, 'single')
                ORDER BY requests DESC
                """
            ).fetchall()
            out = []
            for row in rows:
                data = dict(row)
                mode = data["planning_mode"]
                total = data["requests"] or 0
                durations = [
                    d[0]
                    for d in conn.execute(
                        "SELECT duration_ms FROM requests "
                        "WHERE had_tools = 1 "
                        "AND COALESCE(planning_mode, 'single') = ? "
                        "AND duration_ms IS NOT NULL ORDER BY duration_ms",
                        (mode,),
                    ).fetchall()
                ]
                data["tool_call_yield"] = (
                    data["with_tool_call"] / total if total else 0.0
                )
                data["guard_rate"] = data["guarded"] / total if total else 0.0
                data["error_rate"] = data["errors"] / total if total else 0.0
                data["avg_attempts"] = data["attempts"] / total if total else 0.0
                data["avg_corrections"] = (
                    data["corrections"] / total if total else 0.0
                )
                data["p50_duration_ms"] = _percentile(durations, 0.50)
                data["p95_duration_ms"] = _percentile(durations, 0.95)
                out.append(data)
            return out
        finally:
            conn.close()

    def errors(self, limit: int = 100) -> list[dict]:
        """守卫与 substrate 错误事件时间线（时间倒序）。"""
        conn = self._readonly_conn()
        try:
            rows = conn.execute(
                "SELECT ts, request_id, session_key, type, detail FROM events "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def session_detail(self, session_key: str) -> dict | None:
        """单会话累计（token/请求/错误）与事件流。"""
        conn = self._readonly_conn()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), 0)
                        AS errors,
                    COALESCE(SUM(CASE WHEN guard IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS guarded,
                    MIN(ts) AS first_ts,
                    MAX(ts) AS last_ts
                FROM requests WHERE session_key = ?
                """,
                (session_key,),
            ).fetchone()
            summary = dict(row)
            if not summary["requests"]:
                return None
            summary["session_key"] = session_key
            requests = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, ts, model, tone, stream, status, guard, "
                    "total_tokens, duration_ms FROM requests "
                    "WHERE session_key = ? ORDER BY ts",
                    (session_key,),
                ).fetchall()
            ]
            tool_calls = [
                dict(r)
                for r in conn.execute(
                    "SELECT call_id, request_id, ts, name, category, args_bytes, "
                    "result_error, result_bytes FROM tool_calls "
                    "WHERE session_key = ? ORDER BY ts",
                    (session_key,),
                ).fetchall()
            ]
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT ts, request_id, type, detail FROM events "
                    "WHERE session_key = ? ORDER BY ts",
                    (session_key,),
                ).fetchall()
            ]
            return {
                "summary": summary,
                "requests": requests,
                "tool_calls": tool_calls,
                "events": events,
            }
        finally:
            conn.close()

    def request_detail(self, request_id: str) -> dict | None:
        conn = self._readonly_conn()
        try:
            row = conn.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            detail = dict(row)
            detail["attempts"] = [
                dict(a)
                for a in conn.execute(
                    "SELECT seq, duration_ms, guard, retried, status, text "
                    "FROM attempts WHERE request_id = ? ORDER BY seq",
                    (request_id,),
                ).fetchall()
            ]
            return detail
        finally:
            conn.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class MonitorBus:
    """有界队列 + 后台线程消费者，把记录异步写入 SQLite sink。

    - ``emit`` 非阻塞：队列满则丢弃事件并限频告警；
    - 后台线程写库，写库异常被吞掉；
    - ``flush`` 供只读查询前调用，确保读到最新（只在读路径，不在 chat 路径）。
    """

    def __init__(
        self,
        sink: SQLiteSink,
        *,
        capture: str = CAPTURE_FAILURES,
        capacity: int = 1000,
    ) -> None:
        self.sink = sink
        self.capture = capture if capture in _VALID_CAPTURE else CAPTURE_FAILURES
        self._q: queue.Queue = queue.Queue(maxsize=capacity)
        self._dropped = 0
        self._last_warn = 0.0
        self._stop = object()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        while True:
            rec = self._q.get()
            try:
                if rec is self._stop:
                    return
                self.sink.write(rec)
            except Exception:  # pragma: no cover - defensive
                logger.debug("monitor sink write failed", exc_info=True)
            finally:
                self._q.task_done()

    def emit(self, rec: RequestRecord) -> None:
        try:
            self._q.put_nowait(rec)
        except queue.Full:
            self._dropped += 1
            now = time.monotonic()
            if now - self._last_warn > 30:
                self._last_warn = now
                logger.warning(
                    "monitor queue full; dropped %d event(s) so far", self._dropped
                )

    def flush(self) -> None:
        self._q.join()

    def close(self) -> None:
        try:
            self._q.put_nowait(self._stop)
        except queue.Full:
            pass

