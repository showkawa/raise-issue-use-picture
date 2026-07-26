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
    prompt_summary: str | None = None
    reply_snippet: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)


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
    ) -> None:
        try:
            rec = self.record
            rec.status = status
            rec.guard = guard
            rec.error = _truncate(error, 512)
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
                    prompt_summary TEXT,
                    reply_snippet TEXT
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

    def write(self, rec: RequestRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO requests (
                    id, ts, session_key, model, tone, stream, status, guard,
                    prompt_tokens, completion_tokens, total_tokens, duration_ms,
                    error, prompt_summary, reply_snippet
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    rec.prompt_summary,
                    rec.reply_snippet,
                ),
            )
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

