"""SQLite storage for guild settings and application tickets."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def _env_id(name: str, fallback: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value.isdigit() else fallback


def _env_ids(name: str, fallback: str) -> list[str]:
    values = [item.strip() for item in os.getenv(name, fallback).split(",") if item.strip()]
    return [value for value in values if value.isdigit()]


def _optional_env_id(name: str, fallback: str = "") -> str | None:
    value = os.getenv(name, fallback).strip()
    return value if value.isdigit() else None


DEFAULTS: dict[str, Any] = {
    "panel_title": "Заявка в клан RIval",
    "panel_description": "Подай заявку в клан. Нажми кнопку ниже и заполни форму.",
    "category_id": _env_id("ticket_category_id", "1558846469904146513"),
    "applicant_role_id": _env_id("application_role_id", "1503725660231700620"),
    "support_role_ids": _env_ids("staff_guildid", "1558523889687199905"),
    "log_channel_id": _env_id("ticket_log_channel_id", "1558844108171382945"),
    "panel_channel_id": None,
    "panel_message_id": None,
    "ticket_parent_channel_id": None,
    "applications_open": True,
    "recruiter_ping_role_id": _env_id("recruiter_ping_role_id", "1558483532932718602"),
    "recruit_panel_channel_id": _env_id("recruit_panel_channel_id", "1559169856929005628"),
    "recruit_panel_message_id": None,
    "rank_review_channel_id": _env_id("rank_review_channel_id", "1558859590580576427"),
    "main_role_id": _env_id("main_role_id", "1558482456246489129"),
    "recruiter_role_id": _optional_env_id("recruiter_role_id"),
    "curator_role_id": _env_id("curator_role_id", "1558483015804391504"),
    "recruit_applications_open": True,
}


class TicketStore:
    def __init__(self, db_path: str | Path = "data/rivalbot.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    panel_title TEXT NOT NULL,
                    panel_description TEXT NOT NULL,
                    category_id TEXT,
                    applicant_role_id TEXT,
                    support_role_ids TEXT NOT NULL,
                    log_channel_id TEXT,
                    panel_channel_id TEXT,
                    panel_message_id TEXT,
                    ticket_parent_channel_id TEXT,
                    applications_open INTEGER NOT NULL DEFAULT 1,
                    recruiter_ping_role_id TEXT,
                    recruit_panel_channel_id TEXT,
                    recruit_panel_message_id TEXT,
                    rank_review_channel_id TEXT,
                    main_role_id TEXT,
                    recruiter_role_id TEXT,
                    curator_role_id TEXT,
                    recruit_applications_open INTEGER NOT NULL DEFAULT 1
                )"""
            )
            settings_columns = {row["name"] for row in db.execute("PRAGMA table_info(guild_settings)").fetchall()}
            for column, definition in (
                ("applications_open", "INTEGER NOT NULL DEFAULT 1"),
                ("recruiter_ping_role_id", "TEXT"),
                ("recruit_panel_channel_id", "TEXT"),
                ("recruit_panel_message_id", "TEXT"),
                ("rank_review_channel_id", "TEXT"),
                ("main_role_id", "TEXT"),
                ("recruiter_role_id", "TEXT"),
                ("curator_role_id", "TEXT"),
                ("recruit_applications_open", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if column not in settings_columns:
                    db.execute(f"ALTER TABLE guild_settings ADD COLUMN {column} {definition}")
            db.execute(
                """CREATE TABLE IF NOT EXISTS ticket_counters (
                    guild_id TEXT PRIMARY KEY,
                    next_number INTEGER NOT NULL DEFAULT 1
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS tickets (
                    guild_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    answers TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    rating INTEGER,
                    rated_by TEXT,
                    interview_invited_at TEXT,
                    interview_invited_by TEXT,
                    interview_completed_at TEXT,
                    interview_completed_by TEXT,
                    accepted_at TEXT,
                    accepted_by TEXT,
                    rejected_at TEXT,
                    rejected_by TEXT,
                    ticket_message_id TEXT,
                    ticket_type TEXT NOT NULL DEFAULT 'clan',
                    PRIMARY KEY (guild_id, thread_id),
                    UNIQUE (guild_id, number)
                )"""
            )
            ticket_columns = {row["name"] for row in db.execute("PRAGMA table_info(tickets)").fetchall()}
            for column, definition in (
                ("interview_invited_at", "TEXT"),
                ("interview_invited_by", "TEXT"),
                ("interview_completed_at", "TEXT"),
                ("interview_completed_by", "TEXT"),
                ("accepted_at", "TEXT"),
                ("accepted_by", "TEXT"),
                ("rejected_at", "TEXT"),
                ("rejected_by", "TEXT"),
                ("ticket_message_id", "TEXT"),
                ("ticket_type", "TEXT NOT NULL DEFAULT 'clan'"),
            ):
                if column not in ticket_columns:
                    db.execute(f"ALTER TABLE tickets ADD COLUMN {column} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS tickets_user_status ON tickets(guild_id, user_id, status)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS trainee_tracking (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    trainee_since TEXT NOT NULL,
                    eligible_at TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'waiting',
                    review_message_id TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    review_note TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS accepted_members (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    ticket_thread_id TEXT NOT NULL,
                    ticket_number INTEGER NOT NULL,
                    accepted_at TEXT NOT NULL,
                    accepted_by TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            trainee_columns = {row["name"] for row in db.execute("PRAGMA table_info(trainee_tracking)").fetchall()}
            if "eligible_at" not in trainee_columns:
                db.execute("ALTER TABLE trainee_tracking ADD COLUMN eligible_at TEXT")
                old_rows = db.execute("SELECT guild_id, user_id, trainee_since FROM trainee_tracking").fetchall()
                for row in old_rows:
                    try:
                        eligible_at = (datetime.fromisoformat(row["trainee_since"]) + timedelta(days=7)).isoformat()
                    except (TypeError, ValueError):
                        eligible_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                    db.execute(
                        "UPDATE trainee_tracking SET eligible_at=? WHERE guild_id=? AND user_id=?",
                        (eligible_at, row["guild_id"], row["user_id"]),
                    )
            db.execute(
                """CREATE TABLE IF NOT EXISTS member_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS member_messages_recent ON member_messages(guild_id, user_id, sent_at)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS member_role_presence (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    present INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, role_id)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS dm_notifications (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    notification_key TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, notification_key)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS voice_sessions_recent ON voice_sessions(guild_id, user_id, started_at, ended_at)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS bot_heartbeats (
                    guild_id TEXT PRIMARY KEY,
                    heartbeat_at TEXT NOT NULL
                )"""
            )

    def ensure_guild(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO guild_settings
                (guild_id, panel_title, panel_description, category_id, applicant_role_id,
                 support_role_ids, log_channel_id, panel_channel_id, panel_message_id, ticket_parent_channel_id,
                 applications_open, recruiter_ping_role_id, recruit_panel_channel_id, recruit_panel_message_id,
                 rank_review_channel_id, main_role_id, recruiter_role_id, curator_role_id, recruit_applications_open)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, DEFAULTS["panel_title"], DEFAULTS["panel_description"], DEFAULTS["category_id"],
                 DEFAULTS["applicant_role_id"], json.dumps(DEFAULTS["support_role_ids"]),
                 DEFAULTS["log_channel_id"], None, None, None, 1, DEFAULTS["recruiter_ping_role_id"],
                 DEFAULTS["recruit_panel_channel_id"], None, DEFAULTS["rank_review_channel_id"],
                 DEFAULTS["main_role_id"], DEFAULTS["recruiter_role_id"], DEFAULTS["curator_role_id"], 1),
            )
            db.execute("INSERT OR IGNORE INTO ticket_counters (guild_id, next_number) VALUES (?, 1)", (key,))
        return self.get_settings(guild_id)

    def get_settings(self, guild_id: int) -> dict[str, Any]:
        self.ensure_guild_row(guild_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
        settings = dict(DEFAULTS)
        settings.update(dict(row))
        for key in (
            "recruiter_ping_role_id", "recruit_panel_channel_id", "rank_review_channel_id",
            "main_role_id", "curator_role_id", "recruit_applications_open",
        ):
            if settings.get(key) is None:
                settings[key] = DEFAULTS[key]
        settings["support_role_ids"] = json.loads(settings["support_role_ids"])
        settings["applications_open"] = bool(settings.get("applications_open", 1))
        settings["recruit_applications_open"] = bool(settings.get("recruit_applications_open", 1))
        return settings

    def ensure_guild_row(self, guild_id: int) -> None:
        key = str(guild_id)
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO guild_settings
                (guild_id, panel_title, panel_description, category_id, applicant_role_id,
                 support_role_ids, log_channel_id, panel_channel_id, panel_message_id, ticket_parent_channel_id,
                 applications_open, recruiter_ping_role_id, recruit_panel_channel_id, recruit_panel_message_id,
                 rank_review_channel_id, main_role_id, recruiter_role_id, curator_role_id, recruit_applications_open)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, DEFAULTS["panel_title"], DEFAULTS["panel_description"], DEFAULTS["category_id"],
                 DEFAULTS["applicant_role_id"], json.dumps(DEFAULTS["support_role_ids"]),
                 DEFAULTS["log_channel_id"], None, None, None, 1, DEFAULTS["recruiter_ping_role_id"],
                 DEFAULTS["recruit_panel_channel_id"], None, DEFAULTS["rank_review_channel_id"],
                 DEFAULTS["main_role_id"], DEFAULTS["recruiter_role_id"], DEFAULTS["curator_role_id"], 1),
            )

    def update_settings(self, guild_id: int, **changes: Any) -> dict[str, Any]:
        allowed = {
            "panel_title", "panel_description", "category_id", "applicant_role_id",
            "support_role_ids", "log_channel_id", "panel_channel_id", "panel_message_id",
            "ticket_parent_channel_id", "applications_open", "recruiter_ping_role_id",
            "recruit_panel_channel_id", "recruit_panel_message_id", "rank_review_channel_id",
            "main_role_id", "recruiter_role_id", "curator_role_id", "recruit_applications_open",
        }
        unknown = changes.keys() - allowed
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
        self.ensure_guild_row(guild_id)
        for key, value in changes.items():
            if key == "support_role_ids":
                value = json.dumps([str(role_id) for role_id in value])
            elif key in {"applications_open", "recruit_applications_open"}:
                value = 1 if value else 0
            elif value is not None:
                value = str(value)
            with self._connect() as db:
                db.execute(f"UPDATE guild_settings SET {key} = ? WHERE guild_id = ?", (value, str(guild_id)))
        return self.get_settings(guild_id)

    def find_open_ticket(self, guild_id: int, user_id: int, ticket_type: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            query = "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status IN ('open', 'claimed', 'closing')"
            params: list[Any] = [str(guild_id), str(user_id)]
            if ticket_type is not None:
                query += " AND ticket_type = ?"
                params.append(ticket_type)
            query += " ORDER BY number DESC LIMIT 1"
            row = db.execute(query, params).fetchone()
        return self._ticket_dict(row)

    def create_ticket(
        self, guild_id: int, thread_id: int, user_id: int, answers: dict[str, str], opened_at: str,
        ticket_type: str = "clan",
    ) -> dict[str, Any]:
        if ticket_type not in {"clan", "recruiter"}:
            raise ValueError("Unknown ticket type")
        key = str(guild_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT thread_id FROM tickets WHERE guild_id = ? AND user_id = ? AND ticket_type=? AND status IN ('open', 'claimed', 'closing') LIMIT 1",
                (key, str(user_id), ticket_type),
            ).fetchone()
            if existing:
                raise ValueError("У тебя уже есть открытая заявка.")
            row = db.execute("SELECT next_number FROM ticket_counters WHERE guild_id = ?", (key,)).fetchone()
            number = int(row["next_number"]) if row else 1
            db.execute(
                "INSERT OR IGNORE INTO ticket_counters (guild_id, next_number) VALUES (?, ?)", (key, number + 1)
            )
            db.execute("UPDATE ticket_counters SET next_number = ? WHERE guild_id = ?", (number + 1, key))
            db.execute(
                "INSERT INTO tickets (guild_id, thread_id, number, user_id, answers, status, opened_at, ticket_type) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                (key, str(thread_id), number, str(user_id), json.dumps(answers, ensure_ascii=False), opened_at, ticket_type),
            )
        return self.get_ticket(guild_id, thread_id)  # type: ignore[return-value]

    def get_ticket(self, guild_id: int, thread_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tickets WHERE guild_id = ? AND thread_id = ?", (str(guild_id), str(thread_id))).fetchone()
        return self._ticket_dict(row)

    def delete_ticket(self, guild_id: int, thread_id: int) -> bool:
        """Remove the database record after its Discord thread has been deleted."""
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM tickets WHERE guild_id=? AND thread_id=?",
                (str(guild_id), str(thread_id)),
            )
        return cursor.rowcount > 0

    def record_acceptance(
        self,
        guild_id: int,
        user_id: int,
        thread_id: int,
        ticket_number: int,
        accepted_at: str,
        accepted_by: int,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO accepted_members
                (guild_id, user_id, ticket_thread_id, ticket_number, accepted_at, accepted_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    ticket_thread_id=excluded.ticket_thread_id,
                    ticket_number=excluded.ticket_number,
                    accepted_at=excluded.accepted_at,
                    accepted_by=excluded.accepted_by""",
                (str(guild_id), str(user_id), str(thread_id), int(ticket_number), accepted_at, str(accepted_by)),
            )

    def reserve_dm_notification(self, guild_id: int, user_id: int, notification_key: str) -> bool:
        """Return True once per event key, including across bot restarts."""
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO dm_notifications (guild_id, user_id, notification_key, attempted_at) VALUES (?, ?, ?, ?)",
                (str(guild_id), str(user_id), notification_key, datetime.now(timezone.utc).isoformat()),
            )
        return cursor.rowcount == 1

    def sync_member_role_presence(self, guild_id: int, user_id: int, role_id: int, present: bool) -> None:
        """Set a baseline role state without creating a notification-worthy transition."""
        with self._connect() as db:
            db.execute(
                """INSERT INTO member_role_presence (guild_id, user_id, role_id, present) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET present=excluded.present""",
                (str(guild_id), str(user_id), str(role_id), int(present)),
            )

    def sync_member_role_states(self, guild_id: int, role_id: int, present_user_ids: set[int]) -> None:
        """Refresh a guild role baseline during startup without emitting notifications."""
        with self._connect() as db:
            # Clear previously-known holders first. A stale `present=1` row
            # must not survive a restart after someone lost the role.
            db.execute(
                "UPDATE member_role_presence SET present=0 WHERE guild_id=? AND role_id=?",
                (str(guild_id), str(role_id)),
            )
            rows = [
                (str(guild_id), str(user_id), str(role_id), int(user_id in present_user_ids))
                for user_id in present_user_ids
            ]
            if rows:
                db.executemany(
                    """INSERT INTO member_role_presence (guild_id, user_id, role_id, present) VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET present=excluded.present""",
                    rows,
                )

    def record_member_role_transition(
        self, guild_id: int, user_id: int, role_id: int, before: bool, after: bool
    ) -> bool:
        """Atomically record one observed edge; duplicate gateway updates are ignored."""
        if before == after:
            return False
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO member_role_presence (guild_id, user_id, role_id, present) VALUES (?, ?, ?, ?)",
                (str(guild_id), str(user_id), str(role_id), int(before)),
            )
            cursor = db.execute(
                "UPDATE member_role_presence SET present=? WHERE guild_id=? AND user_id=? AND role_id=? AND present=?",
                (int(after), str(guild_id), str(user_id), str(role_id), int(before)),
            )
        return cursor.rowcount == 1

    def get_acceptance(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM accepted_members WHERE guild_id=? AND user_id=?",
                (str(guild_id), str(user_id)),
            ).fetchone()
        return dict(row) if row else None

    def find_last_accepted_ticket(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM tickets
                WHERE guild_id=? AND user_id=? AND accepted_at IS NOT NULL
                ORDER BY accepted_at DESC LIMIT 1""",
                (str(guild_id), str(user_id)),
            ).fetchone()
        return self._ticket_dict(row)

    def update_ticket(self, guild_id: int, thread_id: int, **changes: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "claimed_by", "closed_at", "rating", "rated_by",
            "interview_invited_at", "interview_invited_by", "interview_completed_at",
            "interview_completed_by", "accepted_at", "accepted_by", "answers", "ticket_message_id",
            "rejected_at", "rejected_by",
        }
        unknown = changes.keys() - allowed
        if unknown:
            raise ValueError(f"Unknown ticket field(s): {', '.join(unknown)}")
        if not changes:
            return self.get_ticket(guild_id, thread_id)
        assignments = ", ".join(f"{key} = ?" for key in changes)
        values = [
            json.dumps(value, ensure_ascii=False) if key == "answers" else str(value) if value is not None and key in {
                "claimed_by", "rated_by", "interview_invited_by", "interview_completed_by",
                "accepted_by", "ticket_message_id",
                "rejected_by",
            } else value
            for key, value in changes.items()
        ]
        with self._connect() as db:
            db.execute(f"UPDATE tickets SET {assignments} WHERE guild_id = ? AND thread_id = ?", (*values, str(guild_id), str(thread_id)))
        return self.get_ticket(guild_id, thread_id)

    def _conditional_ticket_update(
        self,
        guild_id: int,
        thread_id: int,
        condition: str,
        condition_values: tuple[Any, ...],
        **changes: Any,
    ) -> dict[str, Any] | None:
        """Apply one state transition only if its expected prior state still holds."""
        allowed = {
            "status", "claimed_by", "closed_at", "rating", "rated_by",
            "interview_invited_at", "interview_invited_by", "interview_completed_at",
            "interview_completed_by", "accepted_at", "accepted_by",
            "rejected_at", "rejected_by",
        }
        if not changes or changes.keys() - allowed:
            raise ValueError("Invalid conditional ticket update")
        assignments = ", ".join(f"{key}=?" for key in changes)
        values = tuple(
            str(value) if value is not None and key in {
                "claimed_by", "rated_by", "interview_invited_by", "interview_completed_by", "accepted_by",
                "rejected_by",
            } else value
            for key, value in changes.items()
        )
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE tickets SET {assignments} WHERE guild_id=? AND thread_id=? AND {condition}",
                (*values, str(guild_id), str(thread_id), *condition_values),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_ticket(guild_id, thread_id)

    def claim_ticket(self, guild_id: int, thread_id: int, user_id: int) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id, "status='open' AND claimed_by IS NULL", (),
            claimed_by=user_id, status="claimed",
        )

    def begin_close_ticket(self, guild_id: int, thread_id: int) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id, "status IN ('open', 'claimed')", (), status="closing",
        )

    def finish_ticket_rating(
        self, guild_id: int, thread_id: int, rating: int, user_id: int, closed_at: str
    ) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id, "status='closing' AND rating IS NULL", (),
            status="closed", rating=rating, rated_by=user_id, closed_at=closed_at,
        )

    def mark_interview_invited(
        self, guild_id: int, thread_id: int, invited_at: str, invited_by: int
    ) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id,
            "interview_invited_at IS NULL AND accepted_at IS NULL AND status IN ('open', 'claimed')", (),
            interview_invited_at=invited_at, interview_invited_by=invited_by,
        )

    def mark_interview_completed(
        self, guild_id: int, thread_id: int, completed_at: str, completed_by: int
    ) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id,
            "interview_invited_at IS NOT NULL AND interview_completed_at IS NULL "
            "AND accepted_at IS NULL AND status IN ('open', 'claimed')", (),
            interview_completed_at=completed_at, interview_completed_by=completed_by,
        )

    def mark_ticket_accepted(
        self, guild_id: int, thread_id: int, accepted_at: str, accepted_by: int
    ) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id,
            "interview_completed_at IS NOT NULL AND accepted_at IS NULL AND rejected_at IS NULL AND status IN ('open', 'claimed')", (),
            accepted_at=accepted_at, accepted_by=accepted_by,
        )

    def mark_ticket_rejected(
        self, guild_id: int, thread_id: int, rejected_at: str, rejected_by: int
    ) -> dict[str, Any] | None:
        return self._conditional_ticket_update(
            guild_id, thread_id,
            "accepted_at IS NULL AND rejected_at IS NULL AND status IN ('open', 'claimed')", (),
            rejected_at=rejected_at, rejected_by=rejected_by, status="rejected",
        )

    def list_tickets(self, guild_id: int, *, open_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM tickets WHERE guild_id = ?"
        params: list[Any] = [str(guild_id)]
        if open_only:
            query += " AND status IN ('open', 'claimed', 'closing')"
        query += " ORDER BY number DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._ticket_dict(row) for row in rows if row is not None]

    def register_trainee(self, guild_id: int, user_id: int, trainee_since: str) -> bool:
        """Record a new trainee period; return True only when inserted/reset."""
        eligible_at = (datetime.fromisoformat(trainee_since) + timedelta(days=7)).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO trainee_tracking (guild_id, user_id, trainee_since, eligible_at, review_status)
                VALUES (?, ?, ?, ?, 'waiting')""",
                (str(guild_id), str(user_id), trainee_since, eligible_at),
            )
        return cursor.rowcount > 0

    def reset_trainee(self, guild_id: int, user_id: int, trainee_since: str) -> None:
        eligible_at = (datetime.fromisoformat(trainee_since) + timedelta(days=7)).isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO trainee_tracking (guild_id, user_id, trainee_since, eligible_at, review_status, review_message_id, reviewed_by, reviewed_at, review_note)
                VALUES (?, ?, ?, ?, 'waiting', NULL, NULL, NULL, NULL)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET trainee_since=excluded.trainee_since,
                eligible_at=excluded.eligible_at,
                review_status='waiting', review_message_id=NULL, reviewed_by=NULL, reviewed_at=NULL, review_note=NULL""",
                (str(guild_id), str(user_id), trainee_since, eligible_at),
            )

    def set_trainee_start(self, guild_id: int, user_id: int, trainee_since: str) -> None:
        """Set/backfill the role assignment date and derive its eligibility date."""
        eligible_at = (datetime.fromisoformat(trainee_since) + timedelta(days=7)).isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO trainee_tracking
                (guild_id, user_id, trainee_since, eligible_at, review_status,
                 review_message_id, reviewed_by, reviewed_at, review_note)
                VALUES (?, ?, ?, ?, 'waiting', NULL, NULL, NULL, NULL)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    trainee_since=excluded.trainee_since,
                    eligible_at=excluded.eligible_at,
                    review_status='waiting', review_message_id=NULL,
                    reviewed_by=NULL, reviewed_at=NULL, review_note=NULL""",
                (str(guild_id), str(user_id), trainee_since, eligible_at),
            )

    def get_trainee(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM trainee_tracking WHERE guild_id=? AND user_id=?",
                (str(guild_id), str(user_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_trainees(self, guild_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM trainee_tracking WHERE guild_id=?"
        params: list[Any] = [str(guild_id)]
        if status is not None:
            query += " AND review_status=?"
            params.append(status)
        query += " ORDER BY trainee_since"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_trainee(self, guild_id: int, user_id: int, **changes: Any) -> dict[str, Any] | None:
        allowed = {"trainee_since", "eligible_at", "review_status", "review_message_id", "reviewed_by", "reviewed_at", "review_note"}
        if changes.keys() - allowed:
            raise ValueError("Unknown trainee field")
        if changes:
            assignments = ", ".join(f"{key}=?" for key in changes)
            values = [str(value) if value is not None else None for value in changes.values()]
            with self._connect() as db:
                db.execute(
                    f"UPDATE trainee_tracking SET {assignments} WHERE guild_id=? AND user_id=?",
                    (*values, str(guild_id), str(user_id)),
                )
        return self.get_trainee(guild_id, user_id)

    def record_message(self, guild_id: int, user_id: int, sent_at: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO member_messages (guild_id, user_id, sent_at) VALUES (?, ?, ?)",
                (str(guild_id), str(user_id), sent_at),
            )

    def start_voice_session(self, guild_id: int, user_id: int, channel_id: int, started_at: str) -> None:
        with self._connect() as db:
            active = db.execute(
                "SELECT id FROM voice_sessions WHERE guild_id=? AND user_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                (str(guild_id), str(user_id)),
            ).fetchone()
            if active:
                db.execute("UPDATE voice_sessions SET ended_at=? WHERE id=?", (started_at, active["id"]))
            db.execute(
                "INSERT INTO voice_sessions (guild_id, user_id, channel_id, started_at) VALUES (?, ?, ?, ?)",
                (str(guild_id), str(user_id), str(channel_id), started_at),
            )

    def end_voice_session(self, guild_id: int, user_id: int, ended_at: str) -> None:
        with self._connect() as db:
            active = db.execute(
                "SELECT id FROM voice_sessions WHERE guild_id=? AND user_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                (str(guild_id), str(user_id)),
            ).fetchone()
            if active:
                db.execute("UPDATE voice_sessions SET ended_at=? WHERE id=?", (ended_at, active["id"]))

    def get_member_activity(self, guild_id: int, user_id: int, since: str) -> dict[str, Any]:
        with self._connect() as db:
            message_row = db.execute(
                """SELECT COUNT(*) AS count, COUNT(DISTINCT substr(sent_at, 1, 10)) AS active_days,
                MAX(sent_at) AS last_message_at FROM member_messages WHERE guild_id=? AND user_id=? AND sent_at>=?""",
                (str(guild_id), str(user_id), since),
            ).fetchone()
            sessions = db.execute(
                """SELECT started_at, ended_at FROM voice_sessions
                WHERE guild_id=? AND user_id=? AND started_at<? AND (ended_at IS NULL OR ended_at>?)""",
                (str(guild_id), str(user_id), datetime_now_iso(), since),
            ).fetchall()

        cutoff = datetime.fromisoformat(since)
        now = datetime.now(timezone.utc)
        durations: list[float] = []
        for session in sessions:
            started = datetime.fromisoformat(session["started_at"])
            ended = datetime.fromisoformat(session["ended_at"]) if session["ended_at"] else now
            duration = max(0.0, (min(now, ended) - max(cutoff, started)).total_seconds())
            if duration > 0:
                durations.append(duration)
        return {
            "messages": int(message_row["count"] if message_row else 0),
            "active_days": int(message_row["active_days"] if message_row else 0),
            "last_message_at": message_row["last_message_at"] if message_row else None,
            "voice_seconds": sum(durations),
            "voice_sessions": len(durations),
            "average_session_seconds": sum(durations) / len(durations) if durations else 0.0,
            "active_sessions": sum(1 for session in sessions if session["ended_at"] is None),
        }

    def prune_activity(self, guild_id: int, before: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM member_messages WHERE guild_id=? AND sent_at<?", (str(guild_id), before))
            db.execute(
                "DELETE FROM voice_sessions WHERE guild_id=? AND ended_at IS NOT NULL AND ended_at<?",
                (str(guild_id), before),
            )

    def get_heartbeat(self, guild_id: int) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT heartbeat_at FROM bot_heartbeats WHERE guild_id=?", (str(guild_id),)).fetchone()
        return row["heartbeat_at"] if row else None

    def set_heartbeat(self, guild_id: int, heartbeat_at: str) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO bot_heartbeats (guild_id, heartbeat_at) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at""",
                (str(guild_id), heartbeat_at),
            )

    def recover_voice_sessions(self, guild_id: int, ended_at: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE voice_sessions
                SET ended_at=CASE WHEN started_at>? THEN started_at ELSE ? END
                WHERE guild_id=? AND ended_at IS NULL""",
                (ended_at, ended_at, str(guild_id)),
            )

    @staticmethod
    def _ticket_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        ticket = dict(row)
        ticket["answers"] = json.loads(ticket["answers"])
        return ticket


def datetime_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
