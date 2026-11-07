"""Durable event -> proposal -> explicit approval -> execution state machine.

No access to source code, shell, network services or commercial agent runtimes is
performed by this module. The first executor is intentionally a simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol
from uuid import uuid4


MAX_SUBJECT_LENGTH = 250
EVENT_KINDS = frozenset({"pr_opened", "ci_failed", "plan_saved", "session_idle", "deploy_finished"})

# Original public-facing examples; not derived from any private implementation.
WORKFLOWS: dict[str, tuple[str, str, tuple[dict[str, str], ...]]] = {
    "pr_opened": (
        "Review the new pull request",
        "A pull request may benefit from a concise review before merging.",
        ({"action": "inspect_change", "scope": "read_only"},
         {"action": "summarize_risks", "scope": "read_only"}),
    ),
    "ci_failed": (
        "Investigate the failing CI run",
        "A failed check may block the developer workflow.",
        ({"action": "inspect_failure", "scope": "read_only"},
         {"action": "draft_recovery_plan", "scope": "read_only"}),
    ),
    "plan_saved": (
        "Review the new implementation plan",
        "A second pass can uncover missing tests and edge cases.",
        ({"action": "read_plan", "scope": "read_only"},
         {"action": "critique_plan", "scope": "read_only"}),
    ),
    "session_idle": (
        "Summarize unfinished session work",
        "An idle session may contain unresolved next steps.",
        ({"action": "summarize_context", "scope": "read_only"},),
    ),
    "deploy_finished": (
        "Check the deployment outcome",
        "A recently completed deployment may warrant validation.",
        ({"action": "review_release_signal", "scope": "read_only"},
         {"action": "draft_validation_checklist", "scope": "read_only"}),
    ),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="microseconds")


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(data: Any) -> str:
    return sha256(canonical(data).encode("utf-8")).hexdigest()


def connect(db_path: str | Path, *, timeout: float = 15) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            repo TEXT NOT NULL,
            session TEXT NOT NULL,
            created_at TEXT NOT NULL,
            processed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            event_id TEXT UNIQUE NOT NULL REFERENCES events(id),
            title TEXT NOT NULL,
            explanation TEXT NOT NULL,
            steps TEXT NOT NULL,
            steps_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY,
            proposal_id TEXT UNIQUE NOT NULL REFERENCES proposals(id),
            executor TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            output TEXT
        );
        CREATE TABLE IF NOT EXISTS muted_kinds (
            kind TEXT PRIMARY KEY
        );
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    for name in ("scope_json", "scope_hash", "approved_hash"):
        if name not in columns:
            conn.execute(f"ALTER TABLE proposals ADD COLUMN {name} TEXT")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL REFERENCES proposals(id),
            transition TEXT NOT NULL, scope_hash TEXT, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS triage_calls (
            event_id TEXT PRIMARY KEY REFERENCES events(id), decision TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications (
            proposal_id TEXT PRIMARY KEY REFERENCES proposals(id), at TEXT NOT NULL, status TEXT NOT NULL);
    """)
    return conn


def ingest_event(conn: sqlite3.Connection, *, kind: str, subject: str,
                 source: str = "manual", repo: str = "", session: str = "",
                 event_key: str | None = None) -> tuple[str, bool]:
    if any(not isinstance(x, str) for x in (kind, subject, source, repo, session)):
        raise ValueError("event fields must be strings")
    if event_key is not None and not isinstance(event_key, str):
        raise ValueError("event key must be a string")
    if kind not in EVENT_KINDS:
        raise ValueError(f"unsupported event type {kind!r}; valid: {', '.join(sorted(EVENT_KINDS))}")
    if not subject.strip() or len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"subject must contain 1-{MAX_SUBJECT_LENGTH} characters")
    if not source.strip() or any(len(x) > 250 for x in (source, repo, session)):
        raise ValueError("source must be nonempty and all identifier fields <=250 characters")
    if event_key is not None and (not event_key.strip() or len(event_key) > 500):
        raise ValueError("event key must contain 1-500 characters")
    event_id = (sha256(f"{source}\0{event_key}".encode()).hexdigest()
                if event_key is not None else str(uuid4()))
    with conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO events
            (id, source, kind, subject, repo, session, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event_id, source, kind, subject.strip(), repo, session, timestamp()))
        if cursor.rowcount == 0:
            old = conn.execute("SELECT kind, subject, repo, session FROM events WHERE id=?", (event_id,)).fetchone()
            if tuple(old) != (kind, subject.strip(), repo, session):
                raise ValueError("event key reused with conflicting metadata")
    return event_id, cursor.rowcount > 0


def mute_kind(conn: sqlite3.Connection, kind: str, *, mute: bool) -> None:
    if kind not in EVENT_KINDS:
        raise ValueError(f"unsupported event type {kind!r}")
    with conn:
        if mute:
            conn.execute("INSERT OR IGNORE INTO muted_kinds (kind) VALUES (?)", (kind,))
        else:
            conn.execute("DELETE FROM muted_kinds WHERE kind=?", (kind,))


def propose_pending(conn: sqlite3.Connection, *, ttl_minutes: int = 120, eligible: set[str] | None = None) -> list[str]:
    if type(ttl_minutes) is not int or not 1 <= ttl_minutes <= 120:
        raise ValueError("ttl_minutes must be between 1 and 120")
    proposals: list[str] = []
    # Serializes evaluation and ensures that two workers cannot create two proposals.
    conn.execute("BEGIN IMMEDIATE")
    try:
        events = conn.execute("""
            SELECT e.* FROM events e WHERE e.processed_at IS NULL
            ORDER BY e.created_at, e.id
        """).fetchall()
        muted = {row[0] for row in conn.execute("SELECT kind FROM muted_kinds")}
        for event in events:
            if eligible is not None and event["id"] not in eligible:
                continue
            if event["kind"] not in muted:
                title, explanation, actions = WORKFLOWS[event["kind"]]
                steps = [{**step, "event_id": event["id"]} for step in actions]
                proposal_id = str(uuid4())
                created = utc_now()
                conn.execute("""
                    INSERT INTO proposals
                    (id, event_id, title, explanation, steps, steps_hash,
                     status, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """, (proposal_id, event["id"], title, explanation,
                      canonical(steps), digest(steps), timestamp(created),
                      timestamp(created + timedelta(minutes=ttl_minutes))))
                row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
                envelope = scope_envelope(row, event)
                conn.execute("UPDATE proposals SET scope_json=?, scope_hash=? WHERE id=?",
                             (canonical(envelope), digest(envelope), proposal_id))
                record_transition(conn, proposal_id, "proposed", digest(envelope))
                proposals.append(proposal_id)
            conn.execute("UPDATE events SET processed_at=? WHERE id=?",
                         (timestamp(), event["id"]))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return proposals


def list_proposals(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT p.*, e.kind, e.subject, e.repo, e.session
             FROM proposals p JOIN events e ON p.event_id=e.id"""
    params: tuple[str, ...] = ()
    if status:
        sql += " WHERE p.status=?"
        params = (status,)
    sql += " ORDER BY p.created_at DESC"
    return [dict(row) for row in conn.execute(sql, params)]


def scope_envelope(row, event) -> dict:
    steps = json.loads(row["steps"])
    expected = [{**step, "event_id": event["id"]} for step in WORKFLOWS[event["kind"]][2]]
    if steps != expected or digest(steps) != row["steps_hash"]:
        raise ValueError("invalid workflow schema")
    return {"version": 1, "proposal_id": row["id"], "event_id": event["id"],
            "source": event["source"], "kind": event["kind"], "subject": event["subject"],
            "repo": event["repo"], "session": event["session"],
            "title": row["title"], "explanation": row["explanation"],
            "created_at": row["created_at"], "expires_at": row["expires_at"],
            "executor": "preview", "effect": "simulation", "allowed_tools": [],
            "agent_token_budget": 0, "steps": steps}


def valid_scope(conn, row) -> bool:
    try:
        event = conn.execute("SELECT * FROM events WHERE id=?", (row["event_id"],)).fetchone()
        envelope = scope_envelope(row, event)
        return canonical(envelope) == row["scope_json"] and digest(envelope) == row["scope_hash"]
    except (ValueError, TypeError, KeyError, IndexError):
        return False


def decide(conn: sqlite3.Connection, proposal_id: str, *, approve: bool, expected_scope: str | None = None) -> str:
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("proposal not found")
        if row["status"] != "pending":
            raise ValueError(f"proposal is not pending ({row['status']})")
        expired = datetime.fromisoformat(row["expires_at"]) <= utc_now()
        if approve and not expired and not valid_scope(conn, row):
            raise ValueError("proposal integrity or scope check failed; create a new proposal")
        if approve and expected_scope is not None and expected_scope != row["scope_hash"]:
            raise ValueError("reviewed scope does not match; inspect the proposal again")
        status = "expired" if expired else ("approved" if approve else "rejected")
        if status == "approved":
            conn.execute("UPDATE proposals SET approved_hash=scope_hash WHERE id=?", (proposal_id,))
        conn.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=?",
                     (status, timestamp(), proposal_id))
        record_transition(conn, proposal_id, status, row["scope_hash"])
        conn.commit()
        return status
    except BaseException:
        conn.rollback()
        raise


def record_transition(conn, proposal_id: str, transition: str, scope_hash: str | None) -> None:
    conn.execute("INSERT INTO audit_log (proposal_id, transition, scope_hash, at) VALUES (?, ?, ?, ?)",
                 (proposal_id, transition, scope_hash, timestamp()))


def inspect_proposal(conn, proposal_id: str) -> dict:
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ValueError("proposal not found")
    result = dict(row)
    try:
        result["scope"] = json.loads(row["scope_json"]) if row["scope_json"] else None
        expired = datetime.fromisoformat(row["expires_at"]) <= utc_now()
    except (ValueError, TypeError):
        result["scope"] = None
        expired = True
    result["integrity_valid"] = valid_scope(conn, row)
    result["expired"] = expired
    result["can_execute"] = (row["status"] == "approved" and not expired
        and result["integrity_valid"] and row["approved_hash"] == row["scope_hash"])
    result["audit"] = [dict(item) for item in conn.execute(
        "SELECT sequence, transition, scope_hash, at FROM audit_log WHERE proposal_id=? ORDER BY sequence", (proposal_id,))]
    return result


def revoke(conn, proposal_id: str) -> str:
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("proposal not found")
        if row["status"] not in {"pending", "approved"}:
            raise ValueError("only pending or approved proposals can be revoked; running work cannot be cancelled")
        conn.execute("UPDATE proposals SET status='revoked', approved_hash=NULL, decided_at=? WHERE id=?",
                     (timestamp(), proposal_id))
        record_transition(conn, proposal_id, "revoked", row["scope_hash"])
        conn.commit()
        return "revoked"
    except BaseException:
        conn.rollback()
        raise


class Executor(Protocol):
    name: str

    def execute(self, steps: list[dict[str, str]]) -> str: ...


@dataclass
class PreviewExecutor:
    """Safe reference executor: previews approved workflow without external actions."""
    name: str = "preview"

    def execute(self, steps: list[dict[str, str]]) -> str:
        return "SIMULATED ONLY: " + ", ".join(step["action"] for step in steps)


def run_one(conn: sqlite3.Connection, *, executor: Executor | None = None, proposal_id: str | None = None) -> dict[str, str] | None:
    executor = executor or PreviewExecutor()
    if type(executor) is not PreviewExecutor or executor.name != "preview":
        raise ValueError("only the built-in preview executor is authorized")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("""
            SELECT * FROM proposals WHERE status='approved' AND (? IS NULL OR id=?)
            ORDER BY decided_at, id LIMIT 1
        """, (proposal_id, proposal_id)).fetchone()
        if row is None:
            conn.commit()
            return None
        if datetime.fromisoformat(row["expires_at"]) <= utc_now():
            conn.execute("UPDATE proposals SET status='expired' WHERE id=?", (row["id"],))
            record_transition(conn, row["id"], "expired", row["scope_hash"])
            conn.commit()
            return {"proposal_id": row["id"], "status": "expired"}
        if not valid_scope(conn, row) or row["approved_hash"] != row["scope_hash"]:
            # No execution when the approved workflow is modified.
            conn.execute("UPDATE proposals SET status='failed' WHERE id=?", (row["id"],))
            record_transition(conn, row["id"], "failed_integrity", row["scope_hash"])
            conn.commit()
            return {"proposal_id": row["id"], "status": "failed_integrity"}
        steps = json.loads(row["steps"])
        execution_id = str(uuid4())
        conn.execute("UPDATE proposals SET status='running' WHERE id=?", (row["id"],))
        conn.execute("""
            INSERT INTO executions (id, proposal_id, executor, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
        """, (execution_id, row["id"], executor.name, timestamp()))
        record_transition(conn, row["id"], "running", row["scope_hash"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    try:
        output = executor.execute(steps)
        outcome = "completed"
    except Exception as exc:
        output = f"Executor failed: {type(exc).__name__}"  # avoid leaking secrets
        outcome = "failed"
    with conn:
        conn.execute("""
            UPDATE executions SET status=?, output=?, finished_at=? WHERE id=?
        """, (outcome, output[:2000], timestamp(), execution_id))
        conn.execute("UPDATE proposals SET status=? WHERE id=?", (outcome, row["id"]))
        record_transition(conn, row["id"], outcome, row["scope_hash"])
    return {"proposal_id": row["id"], "status": outcome, "output": output}


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """Fail closed after restart; never automatically replay possibly executed actions."""
    with conn:
        pending = conn.execute("SELECT id FROM proposals WHERE status='running'").fetchall()
        for row in pending:
            conn.execute("UPDATE proposals SET status='interrupted' WHERE id=?", (row["id"],))
            record_transition(conn, row["id"], "interrupted", None)
            conn.execute("""
                UPDATE executions SET status='interrupted', finished_at=?
                WHERE proposal_id=? AND status='running'
            """, (timestamp(), row["id"]))
    return len(pending)


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    values = {row[0]: row[1] for row in conn.execute(
        "SELECT status, COUNT(*) FROM proposals GROUP BY status")}
    values["events"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return values
