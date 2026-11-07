"""Opt-in input/notification adapters. No agent execution or source inspection."""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .core import EVENT_KINDS, canonical, digest, timestamp


def claude_stop(payload: object, *, repo: str, delivery_id: str) -> dict | None:
    """Consume documented Stop metadata; discard transcripts, paths and messages.

    delivery_id is supplied by the caller because Stop has no stable delivery ID.
    Never emit permission decisions or commands back to the coding agent.
    """
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("expected a Claude Code Stop event")
    session = payload.get("session_id")
    active = payload.get("stop_hook_active")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        raise ValueError("invalid session identifier")
    if type(active) is not bool:
        raise ValueError("stop_hook_active must be boolean")
    if not repo.strip() or len(repo) > 250:
        raise ValueError("an explicit repository label is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", delivery_id):
        raise ValueError("invalid delivery identifier")
    if active:
        return None
    return {"kind": "session_idle", "subject": "Coding agent finished a turn",
            "repo": repo, "session": session, "source": "claude-stop",
            "event_key": digest([repo, session, delivery_id])}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("triage redirects are prohibited")


def local_model(request: dict) -> dict:
    """Fixed loopback destination; no environment proxies, redirects or tools."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    req = Request("http://127.0.0.1:11434/api/chat",
                  data=canonical(request).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=10) as response:
        raw = response.read(16385)
    if len(raw) > 16384:
        raise ValueError("triage response too large")
    return json.loads(raw)


def triage(conn: sqlite3.Connection, event: dict, *, consent: bool,
           max_calls: int = 10, transport=local_model) -> bool:
    """Advisory local model: only an allowlisted event kind leaves the process.

    Durable reservations are consumed even on timeout/crash. The call ceiling is
    per database lifetime, not reset per evaluation. Failure defers the event.
    """
    if not consent:
        raise ValueError("local model triage requires explicit consent")
    if type(max_calls) is not int or not 1 <= max_calls <= 100:
        raise ValueError("triage call ceiling must be 1-100")
    if event.get("kind") not in EVENT_KINDS:
        raise ValueError("unsupported triage event")
    conn.execute("BEGIN IMMEDIATE")
    try:
        previous = conn.execute("SELECT decision FROM triage_calls WHERE event_id=?",
                                (event["id"],)).fetchone()
        if previous is not None:
            conn.commit()
            return previous[0] == "suggest"
        used = conn.execute("SELECT COUNT(*) FROM triage_calls").fetchone()[0]
        if used >= max_calls:
            conn.commit()
            return False
        conn.execute("INSERT INTO triage_calls VALUES (?, 'reserved', ?)",
                     (event["id"], timestamp()))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    decision = "failed"
    try:
        response = transport({"model": "qwen2.5:0.5b", "stream": False,
            "format": {"type": "object", "properties": {"suggest": {"type": "boolean"}},
                       "required": ["suggest"], "additionalProperties": False},
            "options": {"temperature": 0, "num_predict": 32, "num_ctx": 1024},
            "keep_alive": 0,
            "messages": [{"role": "system", "content":
                'Decide whether this developer event warrants a suggestion. Return only {"suggest":true} or {"suggest":false}. You cannot authorize actions.'},
                {"role": "user", "content": canonical({"kind": event["kind"]})}]})
        if response.get("done") is not True:
            raise ValueError("incomplete model result")
        result = json.loads(response["message"]["content"])
        if not isinstance(result, dict) or set(result) != {"suggest"} or type(result["suggest"]) is not bool:
            raise ValueError("invalid triage schema")
        decision = "suggest" if result["suggest"] else "skip"
    except Exception:
        pass  # no raw errors, prompts or model output persisted
    with conn:
        conn.execute("UPDATE triage_calls SET decision=? WHERE event_id=?", (decision, event["id"]))
    return decision == "suggest"


def desktop_notice(count: int) -> None:
    import tkinter
    from tkinter import messagebox
    root = tkinter.Tk()
    root.withdraw()
    try:
        messagebox.showinfo("Agentic Butler",
                            f"{count} proposal(s) await review. Open pco list to inspect and approve.", parent=root)
    finally:
        root.destroy()


def notify(conn: sqlite3.Connection, *, consent: bool, desktop: bool = False,
           backend=None) -> dict:
    """At-most-once batches with persisted 60-second cooldown; no approval UI."""
    if not consent:
        raise ValueError("notifications require explicit consent")
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("SELECT MAX(at) FROM notifications").fetchone()[0]
        from datetime import datetime
        from .core import utc_now
        if last and (utc_now() - datetime.fromisoformat(last)).total_seconds() < 60:
            conn.commit()
            return {"count": 0, "status": "cooldown"}
        rows = conn.execute("""SELECT id FROM proposals WHERE status='pending'
            AND expires_at>? AND id NOT IN (SELECT proposal_id FROM notifications)""",
                            (timestamp(),)).fetchall()
        for row in rows:
            conn.execute("INSERT INTO notifications VALUES (?, ?, 'claimed')", (row[0], timestamp()))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    count = len(rows)
    status = "empty"
    if count:
        try:
            if backend:
                backend(count)
            elif desktop:
                desktop_notice(count)
            status = "delivered"
        except Exception:
            status = "failed"
        with conn:
            conn.executemany("UPDATE notifications SET status=? WHERE proposal_id=?",
                             [(status, row[0]) for row in rows])
    return {"count": count, "status": status}
