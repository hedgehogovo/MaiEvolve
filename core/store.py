from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [已脱敏]", text)
    text = re.sub(
        r"(?i)(密码|验证码|令牌|password|passcode|secret|token|api[_ -]?key)\s*[:：=]\s*\S+",
        r"\1：[已脱敏]",
        text,
    )
    text = re.sub(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])", "[邮箱已脱敏]", text)
    text = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[手机号已脱敏]", text)
    text = re.sub(r"(?<!\d)\d{7,}(?!\d)", "[数字串已脱敏]", text)
    return text


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    event_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL DEFAULT '',
                    reply_to TEXT NOT NULL DEFAULT '',
                    speaker TEXT NOT NULL CHECK (speaker IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    positive_feedback INTEGER NOT NULL DEFAULT 0,
                    is_private INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_interactions_owner_time
                    ON interactions(session_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL DEFAULT '',
                    is_private INTEGER NOT NULL DEFAULT 0,
                    last_activity REAL NOT NULL,
                    last_reflected_at REAL NOT NULL DEFAULT 0,
                    last_audit_attempt REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    weight REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (session_id, user_id, category, rule)
                );
                CREATE INDEX IF NOT EXISTS idx_strategies_owner
                    ON strategies(session_id, user_id, weight DESC, updated_at DESC);

                CREATE TABLE IF NOT EXISTS audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    issues TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    expression_saved INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audits_time ON audits(created_at DESC);

                CREATE TABLE IF NOT EXISTS constraints (
                    category TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    remaining_turns INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_calls (
                    day TEXT PRIMARY KEY,
                    calls INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    value TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(interactions)").fetchall()
            }
            if "message_id" not in columns:
                connection.execute("ALTER TABLE interactions ADD COLUMN message_id TEXT NOT NULL DEFAULT ''")
            if "reply_to" not in columns:
                connection.execute("ALTER TABLE interactions ADD COLUMN reply_to TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_message ON interactions(session_id, message_id)"
            )

    def save_settings(self, settings: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(id, value) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                (json.dumps(settings, ensure_ascii=False),),
            )

    def get_settings(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE id=1").fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def add_interaction(
        self,
        *,
        session_id: str,
        stream_id: str,
        user_id: str,
        speaker: str,
        content: str,
        is_private: bool,
        positive_feedback: bool = False,
        event_id: str = "",
        message_id: str = "",
        reply_to: str = "",
        created_at: float | None = None,
    ) -> bool:
        cleaned = redact_sensitive_text(str(content or "").strip())[:8000]
        if not session_id or not user_id or speaker not in {"user", "assistant"} or not cleaned:
            return False
        timestamp = float(created_at if created_at is not None else time.time())
        fingerprint = hashlib.sha256(
            f"{session_id}\0{user_id}\0{speaker}\0{event_id}\0{timestamp if not event_id else ''}\0{cleaned}".encode(
                "utf-8"
            )
        ).hexdigest()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO interactions(
                    event_key, session_id, stream_id, user_id, speaker, content,
                    message_id, reply_to, positive_feedback, is_private, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    session_id,
                    stream_id,
                    user_id,
                    speaker,
                    cleaned,
                    message_id,
                    reply_to,
                    int(positive_feedback),
                    int(is_private),
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO sessions(session_id, user_id, stream_id, is_private, last_activity)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id, user_id) DO UPDATE SET
                    stream_id=CASE WHEN excluded.stream_id != '' THEN excluded.stream_id ELSE sessions.stream_id END,
                    is_private=MIN(sessions.is_private, excluded.is_private),
                    last_activity=MAX(sessions.last_activity, excluded.last_activity)
                """,
                (session_id, user_id, stream_id, int(is_private), timestamp),
            )
            return cursor.rowcount > 0

    def latest_user(self, session_id: str, *, max_age_seconds: float = 180) -> dict[str, Any] | None:
        cutoff = time.time() - max_age_seconds
        with self._lock, self._connect() as connection:
            users = connection.execute(
                """
                SELECT user_id, is_private, stream_id, MAX(created_at) AS created_at
                FROM interactions
                WHERE session_id=? AND speaker='user' AND created_at>=?
                GROUP BY user_id, is_private, stream_id
                ORDER BY created_at DESC
                LIMIT 2
                """,
                (session_id, cutoff),
            ).fetchall()
        if not users:
            return None
        if len(users) > 1 and float(users[0]["created_at"]) - float(users[1]["created_at"]) < 45:
            return None
        return dict(users[0])

    def user_for_message(self, session_id: str, message_id: str) -> dict[str, Any] | None:
        if not session_id or not message_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, is_private, stream_id, created_at
                FROM interactions
                WHERE session_id=? AND message_id=? AND speaker='user'
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, message_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def due_sessions(self, *, now: float, idle_seconds: int, interval_seconds: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE last_activity <= ?
                  AND last_activity > last_reflected_at
                  AND last_audit_attempt <= ?
                ORDER BY last_activity
                LIMIT 100
                """,
                (now - idle_seconds, now - interval_seconds),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_audit_attempt(self, session_id: str, user_id: str, when: float | None = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET last_audit_attempt=? WHERE session_id=? AND user_id=?",
                (float(when if when is not None else time.time()), session_id, user_id),
            )

    def interactions_for(self, session_id: str, user_id: str, limit: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT speaker, content, positive_feedback, is_private, created_at
                FROM interactions
                WHERE session_id=? AND user_id=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, user_id, max(2, min(100, limit * 2 + 2))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def mark_reflected(self, session_id: str, user_id: str, when: float | None = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions SET last_reflected_at=MAX(last_reflected_at, ?)
                WHERE session_id=? AND user_id=?
                """,
                (float(when if when is not None else time.time()), session_id, user_id),
            )

    def add_audit(
        self,
        *,
        session_id: str,
        user_id: str,
        score: int,
        issues: list[str],
        summary: str,
        expression_saved: bool = False,
        created_at: float | None = None,
    ) -> None:
        timestamp = float(created_at if created_at is not None else time.time())
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audits(session_id, user_id, score, issues, summary, expression_saved, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    max(0, min(100, int(score))),
                    json.dumps(issues, ensure_ascii=False),
                    redact_sensitive_text(str(summary))[:500],
                    int(expression_saved),
                    timestamp,
                ),
            )

    def recent_audits(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audits ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, limit)),),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["issues"] = json.loads(item["issues"])
            except (TypeError, json.JSONDecodeError):
                item["issues"] = []
            results.append(item)
        return results

    def upsert_strategy(
        self,
        *,
        session_id: str,
        user_id: str,
        category: str,
        rule: str,
        evidence: str,
        weight: float,
        max_items: int,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategies(session_id, user_id, category, rule, evidence, weight, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, user_id, category, rule) DO UPDATE SET
                    evidence=excluded.evidence,
                    weight=MAX(strategies.weight, excluded.weight),
                    updated_at=excluded.updated_at
                """,
                (session_id, user_id, category, rule, evidence, weight, time.time()),
            )
            connection.execute(
                """
                DELETE FROM strategies
                WHERE id IN (
                    SELECT id FROM strategies
                    WHERE session_id=? AND user_id=?
                    ORDER BY weight DESC, updated_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (session_id, user_id, max(1, max_items)),
            )

    def strategies_for(self, session_id: str, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT category, rule, evidence, weight, updated_at
                FROM strategies WHERE session_id=? AND user_id=?
                ORDER BY weight DESC, updated_at DESC LIMIT ?
                """,
                (session_id, user_id, max(1, min(20, limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def consume_prompt_context(
        self,
        session_id: str,
        user_id: str,
        *,
        strategy_limit: int,
        include_constraints: bool,
        now: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        timestamp = float(now if now is not None else time.time())
        with self._lock, self._connect() as connection:
            constraints: list[dict[str, Any]] = []
            if include_constraints:
                connection.execute(
                    "DELETE FROM constraints WHERE expires_at<=? OR remaining_turns<=0",
                    (timestamp,),
                )
                rows = connection.execute(
                    """
                    SELECT category, instruction, expires_at, remaining_turns
                    FROM constraints ORDER BY updated_at DESC
                    """
                ).fetchall()
                constraints = [dict(row) for row in rows]
                connection.execute("UPDATE constraints SET remaining_turns=remaining_turns-1")
            strategies: list[dict[str, Any]] = []
            if user_id:
                rows = connection.execute(
                    """
                    SELECT category, rule, evidence, weight, updated_at
                    FROM strategies WHERE session_id=? AND user_id=?
                    ORDER BY weight DESC, updated_at DESC LIMIT ?
                    """,
                    (session_id, user_id, max(1, min(20, strategy_limit))),
                ).fetchall()
                strategies = [dict(row) for row in rows]
        return constraints, strategies

    def all_strategies(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, user_id, category, rule, evidence, weight, updated_at
                FROM strategies ORDER BY updated_at DESC LIMIT ?
                """,
                (max(1, min(200, limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune(self, *, before: float) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM interactions WHERE created_at<?", (before,))
            connection.execute("DELETE FROM sessions WHERE last_activity<?", (before,))
            connection.execute("DELETE FROM strategies WHERE updated_at<?", (before,))
            connection.execute("DELETE FROM audits WHERE created_at<?", (before,))

    def recent_issue_counts(self, limit: int) -> dict[str, int]:
        audits = self.recent_audits(limit)
        counts: dict[str, int] = {}
        for audit in audits:
            for issue in audit["issues"]:
                counts[issue] = counts.get(issue, 0) + 1
        return counts

    def set_constraint(
        self,
        category: str,
        instruction: str,
        *,
        expires_at: float,
        remaining_turns: int,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO constraints(category, instruction, expires_at, remaining_turns, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(category) DO UPDATE SET
                    instruction=excluded.instruction,
                    expires_at=excluded.expires_at,
                    remaining_turns=MAX(constraints.remaining_turns, excluded.remaining_turns),
                    updated_at=excluded.updated_at
                """,
                (category, instruction, expires_at, remaining_turns, time.time()),
            )

    def remove_constraint(self, category: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM constraints WHERE category=?", (category,))

    def consume_constraints(self, *, now: float | None = None) -> list[dict[str, Any]]:
        timestamp = float(now if now is not None else time.time())
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM constraints WHERE expires_at<=? OR remaining_turns<=0", (timestamp,))
            rows = connection.execute(
                "SELECT category, instruction, expires_at, remaining_turns FROM constraints ORDER BY updated_at DESC"
            ).fetchall()
            connection.execute("UPDATE constraints SET remaining_turns=remaining_turns-1")
        return [dict(row) for row in rows if int(row["remaining_turns"]) > 0]

    def active_constraints(self, *, now: float | None = None) -> list[dict[str, Any]]:
        timestamp = float(now if now is not None else time.time())
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT category, instruction, expires_at, remaining_turns
                FROM constraints WHERE expires_at>? AND remaining_turns>0
                ORDER BY updated_at DESC
                """,
                (timestamp,),
            ).fetchall()
        return [dict(row) for row in rows]

    def refresh_constraints(
        self,
        issue_counts: dict[str, int],
        *,
        threshold: int,
        ttl_hours: int,
        ttl_turns: int,
    ) -> None:
        from .critic import ISSUE_INSTRUCTIONS

        now = time.time()
        active = {key for key, count in issue_counts.items() if count >= threshold}
        with self._lock, self._connect() as connection:
            for category in ISSUE_INSTRUCTIONS:
                if category in active:
                    connection.execute(
                        """
                        INSERT INTO constraints(category, instruction, expires_at, remaining_turns, updated_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(category) DO UPDATE SET
                            instruction=excluded.instruction,
                            expires_at=excluded.expires_at,
                            remaining_turns=excluded.remaining_turns,
                            updated_at=excluded.updated_at
                        """,
                        (
                            category,
                            ISSUE_INSTRUCTIONS[category],
                            now + max(1, ttl_hours) * 3600,
                            max(1, ttl_turns),
                            now,
                        ),
                    )
                else:
                    connection.execute("DELETE FROM constraints WHERE category=?", (category,))

    def reserve_daily_call(self, maximum: int, day: str) -> bool:
        if maximum <= 0:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT calls FROM daily_calls WHERE day=?", (day,)).fetchone()
            if row is not None and int(row["calls"]) >= maximum:
                return False
            connection.execute(
                """
                INSERT INTO daily_calls(day, calls) VALUES(?, 1)
                ON CONFLICT(day) DO UPDATE SET calls=daily_calls.calls+1
                """,
                (day,),
            )
            return True

    def dashboard_stats(self, today: str) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            sessions = connection.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()["count"]
            strategies = connection.execute("SELECT COUNT(*) AS count FROM strategies").fetchone()["count"]
            audits = connection.execute("SELECT COUNT(*) AS count FROM audits").fetchone()["count"]
            calls = connection.execute(
                "SELECT calls FROM daily_calls WHERE day=?", (today,)
            ).fetchone()
            rules = connection.execute("SELECT COUNT(*) AS count FROM constraints WHERE expires_at>?", (time.time(),)).fetchone()["count"]
        return {
            "sessions": int(sessions),
            "strategies": int(strategies),
            "audits": int(audits),
            "calls_today": int(calls["calls"]) if calls else 0,
            "active_constraints": int(rules),
        }

