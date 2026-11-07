"""Codex notify receiver. Receives one JSON argv value; never launches an agent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from .core import connect, digest, ingest_event

MAX_PAYLOAD_BYTES = 65536


def normalize(payload: object, *, repo: str, workspace: str) -> dict | None:
    if not isinstance(payload, dict):
        raise ValueError("notification must be an object")
    if payload.get("type") != "agent-turn-complete":
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(workspace).is_absolute():
        raise ValueError("notification needs an absolute workspace")
    # Lexical comparison only: do not follow vendor paths or read their files.
    if os.path.normcase(os.path.normpath(cwd)) != os.path.normcase(os.path.normpath(workspace)):
        return None
    for key in ("thread-id", "turn-id"):
        value = payload.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("notification needs valid thread-id and turn-id")
    if not isinstance(repo, str) or not repo.strip() or len(repo) > 250:
        raise ValueError("explicit repository label required")
    return {"kind": "session_idle", "subject": "Codex finished a turn",
            "source": "codex-notify", "repo": repo, "session": payload["thread-id"],
            "event_key": digest([repo, payload["thread-id"], payload["turn-id"]])}


def command(*, python: str, db: str, repo: str, workspace: str) -> list[str]:
    if not Path(python).is_absolute() or not Path(db).is_absolute():
        raise ValueError("Python and database paths must be absolute")
    # JSON arrays of strings are valid TOML arrays of basic strings. No shell.
    normalize({"type": "agent-turn-complete", "thread-id": "check", "turn-id": "check", "cwd": workspace},
              repo=repo, workspace=workspace)
    return [python, "-m", "proactive_coding_orchestrator.codex_hook",
            "--db", db, "--repo", repo, "--workspace", workspace]


def config_snippet(*, python: str, db: str, repo: str, workspace: str) -> str:
    return "notify = " + json.dumps(command(python=python, db=db, repo=repo, workspace=workspace), ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pco-codex-notify")
    parser.add_argument("--db", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("payload", help="JSON argument appended by Codex")
    args = parser.parse_args(argv)
    conn = None
    try:
        if len(args.payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("notification too large")
        event = normalize(json.loads(args.payload), repo=args.repo, workspace=args.workspace)
        if event is None:
            return 0
        conn = connect(args.db)
        ingest_event(conn, **event)
        return 0
    except Exception:
        # Vendor payload/error details may contain source text or credentials.
        print("PCO could not record the notification; inspect pco doctor.", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
