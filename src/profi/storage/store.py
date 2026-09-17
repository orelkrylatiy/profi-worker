"""SQLite: technical feed memory, candidate lifecycle and outreach analytics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_seen (
    order_id       TEXT PRIMARY KEY,
    last_update    INTEGER NOT NULL,
    first_seen_at  INTEGER NOT NULL,
    last_seen_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    order_id               TEXT PRIMARY KEY,
    first_seen_at          INTEGER NOT NULL,
    updated_at             INTEGER NOT NULL,
    source_last_update     INTEGER NOT NULL,

    title                  TEXT,
    snippet_json           TEXT,

    triage_reason          TEXT,
    priority               INTEGER,

    details_status         TEXT NOT NULL,
    details_json           TEXT,
    details_loaded_at      INTEGER,

    draft_status           TEXT NOT NULL,
    draft_text             TEXT,
    draft_source           TEXT,
    draft_generated_at     INTEGER,

    prompt_experiment      TEXT,
    prompt_variant         TEXT,
    prompt_assigned_at     INTEGER,
    first_reply_text       TEXT,
    first_reply_source     TEXT,
    first_reply_at         INTEGER,
    first_client_reply_at  INTEGER,

    send_status            TEXT NOT NULL,
    send_started_at        INTEGER,
    sent_at                INTEGER,

    respond_mode           TEXT,
    paid_rub               INTEGER,
    last_error             TEXT
);

CREATE TABLE IF NOT EXISTS chat_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT,
    client_name  TEXT,
    sender       TEXT NOT NULL,
    text         TEXT,
    created_at   INTEGER NOT NULL
);

-- Наблюдаемость чатов: что воркер ВИДЕЛ в сайдбаре (слепая зона до 16.09:
-- chat_log хранит только обработанное; живых диалогов было 19, в БД — 6).
-- Одна строка на диалог (name, order_id), состояние обновляется на каждом чат-чеке.
CREATE TABLE IF NOT EXISTS chat_seen (
    client_name  TEXT NOT NULL,
    order_id     TEXT NOT NULL DEFAULT '',
    who_last     TEXT,
    unread       INTEGER,
    preview      TEXT,
    first_seen_at INTEGER NOT NULL,
    seen_at      INTEGER NOT NULL,
    PRIMARY KEY (client_name, order_id)
);
"""

VIEW_SCHEMA = """
DROP VIEW IF EXISTS v_responses;
CREATE VIEW v_responses AS
SELECT
    order_id,
    title,
    triage_reason                                     AS llm_summary,
    send_status,
    datetime(first_seen_at, 'unixepoch', 'localtime') AS first_seen,
    datetime(sent_at, 'unixepoch', 'localtime')       AS sent_at,
    CAST(json_extract(details_json, '$.bid_price') AS INTEGER) AS bid_price,
    json_extract(details_json, '$.competition_position')       AS position,
    length(draft_text)                                          AS text_len,
    draft_text,
    draft_source,
    prompt_experiment,
    prompt_variant,
    first_reply_text,
    first_reply_source,
    datetime(first_reply_at, 'unixepoch', 'localtime') AS first_reply_at,
    datetime(first_client_reply_at, 'unixepoch', 'localtime') AS first_client_reply_at,
    respond_mode,
    paid_rub,
    COALESCE(
        paid_rub,
        CAST(json_extract(details_json, '$.bid_price') AS INTEGER)
    ) AS paid
FROM candidates;

DROP VIEW IF EXISTS v_prompt_experiments;
CREATE VIEW v_prompt_experiments AS
SELECT
    prompt_experiment,
    prompt_variant,
    COUNT(*) AS assigned,
    SUM(CASE WHEN draft_source = 'llm' THEN 1 ELSE 0 END) AS evaluated,
    SUM(CASE WHEN first_reply_source = 'llm' AND first_reply_text IS NOT NULL THEN 1 ELSE 0 END)
        AS generated,
    SUM(CASE WHEN first_reply_source = 'fallback' AND first_reply_text IS NOT NULL THEN 1 ELSE 0 END)
        AS fallbacks,
    SUM(CASE WHEN draft_source = 'llm' AND send_status = 'sent' THEN 1 ELSE 0 END)
        AS sent,
    SUM(
        CASE
            WHEN draft_source = 'llm'
             AND send_status = 'sent'
             AND first_client_reply_at IS NOT NULL
            THEN 1 ELSE 0
        END
    ) AS replied,
    ROUND(
        100.0 * SUM(
            CASE WHEN draft_source = 'llm' AND send_status = 'sent' THEN 1 ELSE 0 END
        ) / NULLIF(SUM(CASE WHEN draft_source = 'llm' THEN 1 ELSE 0 END), 0),
        1
    ) AS send_rate_pct,
    ROUND(
        100.0 * SUM(
            CASE
                WHEN draft_source = 'llm'
                 AND send_status = 'sent'
                 AND first_client_reply_at IS NOT NULL
                THEN 1 ELSE 0
            END
        ) / NULLIF(
            SUM(CASE WHEN draft_source = 'llm' AND send_status = 'sent' THEN 1 ELSE 0 END),
            0
        ),
        1
    ) AS reply_rate_pct,
    ROUND(
        100.0 * SUM(
            CASE
                WHEN draft_source = 'llm'
                 AND send_status = 'sent'
                 AND first_client_reply_at IS NOT NULL
                THEN 1 ELSE 0
            END
        ) / NULLIF(SUM(CASE WHEN draft_source = 'llm' THEN 1 ELSE 0 END), 0),
        1
    ) AS reply_yield_pct,
    ROUND(
        AVG(
            CASE
                WHEN draft_source = 'llm'
                 AND send_status = 'sent'
                 AND first_client_reply_at IS NOT NULL
                 AND sent_at IS NOT NULL
                THEN (first_client_reply_at - sent_at) / 60.0
            END
        ),
        1
    ) AS avg_reply_min
FROM candidates
WHERE prompt_experiment IS NOT NULL AND prompt_variant IS NOT NULL
GROUP BY prompt_experiment, prompt_variant;
"""

# feed_seen: NEW / UPDATED / UNCHANGED
# candidates.details_status: pending / ready / error
# candidates.draft_status:   pending / generating / generated / skipped / error
# candidates.send_status:    not_sent / sending / sent / unknown / skipped / failed


class Store:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(TABLE_SCHEMA)

        for ddl in (
            "ALTER TABLE candidates ADD COLUMN respond_mode TEXT",
            "ALTER TABLE candidates ADD COLUMN paid_rub INTEGER",
            "ALTER TABLE candidates ADD COLUMN draft_source TEXT",
            "ALTER TABLE candidates ADD COLUMN prompt_experiment TEXT",
            "ALTER TABLE candidates ADD COLUMN prompt_variant TEXT",
            "ALTER TABLE candidates ADD COLUMN prompt_assigned_at INTEGER",
            "ALTER TABLE candidates ADD COLUMN first_reply_text TEXT",
            "ALTER TABLE candidates ADD COLUMN first_reply_source TEXT",
            "ALTER TABLE candidates ADD COLUMN first_reply_at INTEGER",
            "ALTER TABLE candidates ADD COLUMN first_client_reply_at INTEGER",
            "ALTER TABLE candidates ADD COLUMN send_started_at INTEGER",
        ):
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        self.conn.executescript(VIEW_SCHEMA)
        self.conn.commit()
        self.reconcile_stale_sending()

    def close(self):
        self.conn.close()

    def register_feed_seen(self, order_id: str, last_update: int | None) -> str:
        now = int(time.time())
        lu = last_update if last_update is not None else 0
        row = self.conn.execute(
            "SELECT last_update FROM feed_seen WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO feed_seen (order_id, last_update, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (order_id, lu, now, now),
            )
            self.conn.commit()
            return "NEW"
        if lu != row["last_update"]:
            self.conn.execute(
                "UPDATE feed_seen SET last_update = ?, last_seen_at = ? WHERE order_id = ?",
                (lu, now, order_id),
            )
            self.conn.commit()
            return "UPDATED"
        self.conn.execute(
            "UPDATE feed_seen SET last_seen_at = ? WHERE order_id = ?", (now, order_id)
        )
        self.conn.commit()
        return "UNCHANGED"

    def create_candidate(self, snippet, triage_reason: str | None, priority: int | None) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT OR REPLACE INTO candidates "
            "(order_id, first_seen_at, updated_at, source_last_update, title, snippet_json, "
            " triage_reason, priority, details_status, draft_status, send_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', 'not_sent')",
            (
                snippet.id,
                now,
                now,
                snippet.last_update or 0,
                snippet.title,
                json.dumps(snippet.raw, ensure_ascii=False) if snippet.raw else None,
                triage_reason,
                priority,
            ),
        )
        self.conn.commit()

    def update_details(self, order_id: str, status: str, details_json: str | None) -> None:
        now = int(time.time())
        self.conn.execute(
            "UPDATE candidates SET details_status = ?, details_json = ?, details_loaded_at = ?, "
            "updated_at = ? WHERE order_id = ?",
            (status, details_json, now if status == "ready" else None, now, order_id),
        )
        self.conn.commit()

    def update_details_for_fast_path(self, order_id: str, details_json: str) -> None:
        now = int(time.time())
        self.conn.execute(
            "UPDATE candidates SET details_status='ready', details_json=?, details_loaded_at=?, "
            "draft_status='generating', updated_at=? WHERE order_id=? AND send_status='not_sent'",
            (details_json, now, now, order_id),
        )
        self.conn.commit()

    def assign_prompt_variant(
        self, order_id: str, experiment_id: str, variants: tuple[str, ...] | list[str]
    ) -> str:
        """Assign one stable pseudo-random experiment arm before any LLM call."""
        choices = tuple(dict.fromkeys(str(v) for v in variants if str(v)))
        if not choices:
            raise ValueError("prompt experiment requires at least one variant")

        row = self.conn.execute(
            "SELECT prompt_experiment, prompt_variant FROM candidates WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"candidate {order_id} not found")
        if row["prompt_variant"]:
            return str(row["prompt_variant"])

        digest = hashlib.sha256(f"{experiment_id}:{order_id}".encode()).digest()
        variant = choices[int.from_bytes(digest[:8], "big") % len(choices)]
        now = int(time.time())
        self.conn.execute(
            "UPDATE candidates SET prompt_experiment=?, prompt_variant=?, prompt_assigned_at=?, "
            "updated_at=? WHERE order_id=? AND prompt_variant IS NULL",
            (experiment_id, variant, now, now, order_id),
        )
        self.conn.commit()
        actual = self.conn.execute(
            "SELECT prompt_variant FROM candidates WHERE order_id=?", (order_id,)
        ).fetchone()
        if actual is None or not actual["prompt_variant"]:
            raise RuntimeError(f"failed to assign prompt variant for {order_id}")
        return str(actual["prompt_variant"])

    def set_draft(
        self,
        order_id: str,
        status: str,
        *,
        text: str | None = None,
        source: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Persist mutable draft state plus immutable first outreach copy."""
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE candidates SET draft_status=?, "
            "draft_text=CASE WHEN ? IS NOT NULL THEN ? ELSE draft_text END, "
            "draft_source=CASE WHEN ? IS NOT NULL THEN ? ELSE draft_source END, "
            "draft_generated_at=CASE WHEN ?='generated' THEN ? ELSE draft_generated_at END, "
            "last_error=CASE WHEN ? IS NOT NULL THEN ? ELSE last_error END, "
            "updated_at=? WHERE order_id=?",
            (
                status,
                text,
                text,
                source,
                source,
                status,
                now,
                error,
                error[:300] if error else None,
                now,
                order_id,
            ),
        )
        if status == "generated" and text:
            self.conn.execute(
                "UPDATE candidates SET first_reply_text=?, first_reply_source=?, first_reply_at=? "
                "WHERE order_id=? AND first_reply_text IS NULL",
                (text, source, now, order_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_client_reply(self, order_id: str) -> bool:
        """Record only the first observed client reply timestamp; no message text needed."""
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE candidates SET first_client_reply_at=?, updated_at=? "
            "WHERE order_id=? AND sent_at IS NOT NULL AND first_client_reply_at IS NULL",
            (now, now, order_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def claim_send(self, order_id: str) -> bool:
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE candidates SET send_status='sending', send_started_at=?, updated_at=? "
            "WHERE order_id=? AND send_status='not_sent'",
            (now, now, order_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def reconcile_stale_sending(self, max_age_s: int = 300, *, now: int | None = None) -> int:
        """Fail closed after a crash inside send processing.

        We intentionally never retry a stale ``sending`` candidate. Once a process
        owned the send, a crash can leave the platform outcome unknowable, so the
        conservative terminal state is ``unknown`` and it consumes the daily send
        budget. This is safer than a duplicate paid response.
        """
        now = int(time.time()) if now is None else int(now)
        cutoff = now - max(0, int(max_age_s))
        cur = self.conn.execute(
            "UPDATE candidates SET send_status='unknown', sent_at=COALESCE(sent_at, send_started_at, ?), "
            "last_error='stale sending reconciled after worker crash; no retry', updated_at=? "
            "WHERE send_status='sending' AND COALESCE(send_started_at, updated_at, 0) < ?",
            (now, now, cutoff),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def set_send_status(self, order_id: str, status: str) -> bool:
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE candidates SET send_status = ?, "
            "sent_at = CASE WHEN ? IN ('sent','unknown') THEN ? ELSE sent_at END, "
            "updated_at = ? WHERE order_id = ?",
            (status, status, now, now, order_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def record_response(self, order_id: str, mode: str, paid_rub: int | None) -> None:
        self.conn.execute(
            "UPDATE candidates SET respond_mode = ?, paid_rub = ?, updated_at = ? "
            "WHERE order_id = ?",
            (mode, paid_rub, int(time.time()), order_id),
        )
        self.conn.commit()

    def log_chat(self, order_id: str | None, client_name: str, sender: str, text: str) -> None:
        """Persist chat event and infer first client reply from auto-chat handling.

        Runtime writes tutor/system events and (since the nudge fix, 04.09)
        the client message being replied to. Only tutor/system events count as
        a reply observation: timestamp only, no incoming client text is copied
        into candidates.
        """
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO chat_log (order_id, client_name, sender, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (order_id, client_name, sender, text, now),
        )
        if order_id and sender in {"tutor", "system"}:
            self.conn.execute(
                "UPDATE candidates SET first_client_reply_at=?, updated_at=? "
                "WHERE order_id=? AND sent_at IS NOT NULL AND first_client_reply_at IS NULL",
                (now, now, order_id),
            )
        self.conn.commit()

    def last_chat_sent_at(self, order_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(created_at) FROM chat_log WHERE order_id = ? AND sender = 'tutor'",
            (order_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def chat_tutor_streak(self, order_id: str) -> int:
        """Сколько последних наших сообщений в диалоге подряд без ответа клиента.

        Защита от «догонялок» (инцидент 04.09: 8 сообщений молчащему клиенту):
        если streak >= лимита — клиент молчит после N наших сообщений, молчим и мы.
        """
        rows = self.conn.execute(
            "SELECT sender FROM chat_log "
            "WHERE order_id = ? AND sender IN ('tutor', 'client') "
            "ORDER BY created_at DESC, id DESC LIMIT 20",
            (order_id,),
        ).fetchall()
        streak = 0
        for (sender,) in rows:
            if sender != "tutor":
                break
            streak += 1
        return streak

    def upsert_chat_seen(
        self, client_name: str, order_id: str, who_last: str, unread: int, preview: str
    ) -> None:
        """Запомнить состояние диалога, виденное в сайдбаре (наблюдаемость).

        order_id неизвестен до открытия диалога — тогда '' (диалоги с
        одинаковым именем без order_id схлопнутся в одну строку — принято:
        сайдбар не даёт стабильного id, см. BACKLOG про dialog identity).
        """
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO chat_seen (client_name, order_id, who_last, unread, preview, "
            "first_seen_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (client_name, order_id) DO UPDATE SET "
            "who_last=excluded.who_last, unread=excluded.unread, "
            "preview=excluded.preview, seen_at=excluded.seen_at",
            (client_name, order_id or "", who_last, unread, preview[:400], now, now),
        )
        self.conn.commit()

    def chat_last_events_by_name(
        self, client_name: str, n: int = 3
    ) -> list[tuple[str, str, int]]:
        """Последние n событий диалога (новые сверху): (sender, text, created_at).

        Ищем по client_name, а не order_id: фильтр целей в chat-auto работает
        ДО открытия диалога, order_id в этот момент ещё неизвестен.
        """
        rows = self.conn.execute(
            "SELECT sender, text, created_at FROM chat_log "
            "WHERE client_name = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (client_name, n),
        ).fetchall()
        return [(r["sender"], r["text"] or "", int(r["created_at"])) for r in rows]

    def set_note(self, order_id: str, note: str) -> bool:
        """Internal decision/debug note; not the primary outreach analytics field."""
        cur = self.conn.execute(
            "UPDATE candidates SET triage_reason = ?, updated_at = ? WHERE order_id = ?",
            (note, int(time.time()), order_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def sends_today(self, now=None) -> int:
        from profi.utils.workhours import business_now

        current = business_now(now)
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE send_status IN ('sent','unknown') "
            "AND sent_at >= ?",
            (int(midnight.timestamp()),),
        ).fetchone()
        return int(row[0])

    def ensure_candidate(self, order_id: str, title: str | None) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT OR IGNORE INTO candidates "
            "(order_id, first_seen_at, updated_at, source_last_update, title, "
            " details_status, draft_status, send_status) "
            "VALUES (?, ?, ?, 0, ?, 'pending', 'pending', 'not_sent')",
            (order_id, now, now, title),
        )
        self.conn.commit()

    def list_candidates(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT order_id, title, priority, triage_reason, details_status, draft_status, "
            "send_status, prompt_experiment, prompt_variant, updated_at "
            "FROM candidates ORDER BY updated_at DESC"
        ).fetchall()

    def get_candidate(self, order_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM candidates WHERE order_id = ?", (order_id,)
        ).fetchone()
