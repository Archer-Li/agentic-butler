"""Foreground observation pipeline; deliberately has no execution entry point."""
from __future__ import annotations

import importlib.util
import shutil
import sys

from .core import propose_pending
from .integrations import notify, triage


def evaluate_once(conn, *, ttl_minutes=120, llm=False, llm_consent=False,
                  max_calls=10, notifications=False, notification_consent=False,
                  desktop=False) -> dict:
    # Validate all options before any model call or database mutation.
    if type(ttl_minutes) is not int or not 1 <= ttl_minutes <= 120:
        raise ValueError("ttl_minutes must be between 1 and 120")
    if llm and not llm_consent:
        raise ValueError("local model triage requires --llm-consent")
    if type(max_calls) is not int or not 1 <= max_calls <= 100:
        raise ValueError("triage call ceiling must be 1-100")
    if notifications and not notification_consent:
        raise ValueError("notifications require --notification-consent")
    if desktop and not notifications:
        raise ValueError("--desktop requires --notify")
    eligible = None
    if llm:
        events = conn.execute("""SELECT * FROM events WHERE processed_at IS NULL
            AND kind NOT IN (SELECT kind FROM muted_kinds) ORDER BY created_at, id""").fetchall()
        eligible = {event["id"] for event in events if triage(conn, dict(event),
            consent=True, max_calls=max_calls)}
    result = {"proposals_created": propose_pending(conn, ttl_minutes=ttl_minutes, eligible=eligible)}
    if llm:
        result["triage"] = {row[0]: row[1] for row in conn.execute(
            "SELECT decision, COUNT(*) FROM triage_calls GROUP BY decision")}
        result["triage"]["call_ceiling"] = max_calls
    if notifications:
        result["notification"] = notify(conn, consent=True, desktop=desktop)
    return result


def doctor(conn, config=None) -> dict:
    check = conn.execute("PRAGMA quick_check").fetchone()[0]
    return {"database_ok": check == "ok", "python": sys.version.split()[0],
            "codex_cli_found": shutil.which("codex") is not None,
            "claude_cli_found": shutil.which("claude") is not None,
            "ollama_cli_found": shutil.which("ollama") is not None,
            "tkinter_available": importlib.util.find_spec("tkinter") is not None,
            "execution": "preview_only", "live_agent_enabled": bool(config and config['agent']['enabled']),
            "live_agent_requires_task_approval": True,
            "checks_not_performed": ["live_hook_delivery", "model_inference", "desktop_display"],
            "triage_reservations": conn.execute("SELECT COUNT(*) FROM triage_calls").fetchone()[0],
            "notification_failures": conn.execute("SELECT COUNT(*) FROM notifications WHERE status='failed'").fetchone()[0]}
