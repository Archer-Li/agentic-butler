"""CLI for the independent proactive orchestrator prototype."""
from __future__ import annotations

import argparse
import json
import os
import math
import sqlite3
import sys
from pathlib import Path
import time

from .core import (EVENT_KINDS, connect, decide, ingest_event, list_proposals,
                   mark_interrupted, mute_kind, run_one, stats, inspect_proposal, revoke)

from .integrations import claude_stop, notify
from .codex_hook import config_snippet, command as codex_command
from .worker import evaluate_once, doctor

DEFAULT_DB = Path.home() / ".local" / "share" / "pco" / "state.sqlite3"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pco", description="Agentic Butler, an approval-first coding workflow companion")
    p.add_argument("--db", default=os.environ.get("PCO_DB", str(DEFAULT_DB)), help="SQLite state file")
    p.add_argument("--config", help="versioned application JSON configuration")
    sp = p.add_subparsers(dest="command", required=True)
    sp.add_parser("init", help="create local database")
    ci = sp.add_parser("config-init", help="write a new safe application configuration")
    ci.add_argument("path")
    cv = sp.add_parser("config-check", help="validate application settings")
    cv.add_argument("path")
    hc = sp.add_parser("host-config", help="generate a Codex or Claude hook snippet")
    hc.add_argument("--project", required=True)
    hc.add_argument("--tool", choices=("codex", "claude"), required=True)
    hc.add_argument("--output")
    sl = sp.add_parser("statusline-config", help="generate an opt-in Claude native status-line snippet")
    sl.add_argument("--project", required=True)
    sl.add_argument("--shell", choices=("posix", "powershell"), required=True)
    for name in ("read", "state", "record", "dream", "propose", "review", "approve", "apply",
                 "reject", "revoke", "rollback", "dismiss", "hook-config", "model-prepare", "model-import"):
        command = sp.add_parser("memory-" + name, help="project memory " + name)
        command.add_argument("--project", required=True)
        if name in ("review", "approve", "apply", "reject", "revoke", "dismiss"):
            command.add_argument("id")
        if name in ("approve", "apply"):
            command.add_argument("--expect-scope", required=True)
        if name in ("propose", "rollback"):
            command.add_argument("--allow-pinned", action="store_true")
        if name == "rollback":
            command.add_argument("--revision", type=int, required=True)
        if name == "model-import":
            command.add_argument("--agent-id", required=True)
        if name == "read":
            command.add_argument("--query", default="")
            command.add_argument("--session")
        if name == "record":
            command.add_argument("--session", required=True)
            command.add_argument("--delivery-id", required=True)
            command.add_argument("--tool", choices=("manual", "codex", "claude"), default="manual")
            command.add_argument("--agent", default="")
            command.add_argument("--final", action="store_true")
        if name == "hook-config":
            command.add_argument("--tool", choices=("codex", "claude"), required=True)
            command.add_argument("--output")
    ap = sp.add_parser("agent-prepare", help="prepare a separate live, tool-free Codex text task")
    ap.add_argument("--project", required=True)
    ap.add_argument("--prompt", required=True)
    sp.add_parser("agent-list", help="list live text tasks")
    for name in ("show", "approve", "run", "revoke", "cancel"):
        sub = sp.add_parser("agent-" + name)
        sub.add_argument("id")
        if name == "approve":
            sub.add_argument("--expect-scope", required=True)
    ui = sp.add_parser("ui", help="open a local companion status strip and review panel")
    ui.add_argument("--port", type=int, default=0)
    ui.add_argument("--open", action="store_true", help="open the companion in the default browser")
    e = sp.add_parser("emit", help="ingest an event without sending data externally")
    e.add_argument("kind", choices=sorted(EVENT_KINDS))
    e.add_argument("subject", help="short, non-sensitive event description")
    e.add_argument("--source", default="manual")
    e.add_argument("--repo", default="")
    e.add_argument("--session", default="")
    e.add_argument("--event-key", help="stable provider event id for deduplication")
    j = sp.add_parser("emit-json", help="ingest a normalized JSON event from stdin")
    j.add_argument("--source", default="local-hook", help="trusted adapter name")
    h = sp.add_parser("hook-claude-stop", help="ingest minimized Claude Code Stop metadata")
    h.add_argument("--quiet", action="store_true", help="suppress success output when called by Claude Code")
    h.add_argument("--repo", required=True)
    h.add_argument("--delivery-id", required=True, help="stable caller-generated ID; reuse on retry")
    config = sp.add_parser("codex-config", help="generate an opt-in Codex notify snippet without editing settings")
    config.add_argument("--repo", required=True)
    config.add_argument("--workspace", required=True)
    config.add_argument("--python", default=sys.executable)
    config.add_argument("--output", help="new snippet file; never overwrites an existing file")
    sp.add_parser("doctor", help="local diagnostics; no model calls or agent launch")
    sp.add_parser("notification-check", help="read OS notification availability; never send a notice")
    show = sp.add_parser("show", help="inspect exact approval scope, integrity and audit history")
    show.add_argument("id")
    cancel = sp.add_parser("revoke", help="withdraw pending or approved work before it starts")
    cancel.add_argument("id")
    n = sp.add_parser("notify", help="opt-in batch notification for pending proposals")
    n.add_argument("--consent", action="store_true")
    n.add_argument("--desktop", action="store_true", help="show a desktop dialog instead of JSON only")
    ev = sp.add_parser("evaluate", help="turn new events into approval-required proposals")
    ev.add_argument("--ttl-minutes", type=int, default=120)
    ev.add_argument("--llm-triage", action="store_true", help="use local Ollama; event kind only")
    ev.add_argument("--llm-consent", action="store_true")
    ev.add_argument("--llm-max-calls", type=int, default=10, help="lifetime database call ceiling (1-100)")
    l = sp.add_parser("list", help="view proposed workflows")
    l.add_argument("--status")
    a = sp.add_parser("approve", help="approve a proposed, immutable workflow")
    a.add_argument("id")
    a.add_argument("--expect-scope", help="require the scope hash shown during review")
    r = sp.add_parser("reject", help="dismiss a proposed workflow")
    r.add_argument("id")
    run = sp.add_parser("run", help="execute ONE approved workflow using safe preview executor")
    run.add_argument("--id", help="run only this approved proposal")
    sp.add_parser("stats", help="show proposal counts")
    sp.add_parser("recover", help="mark interrupted runs for manual review")
    m = sp.add_parser("mute", help="turn off suggestions for an event type")
    m.add_argument("kind", choices=sorted(EVENT_KINDS))
    u = sp.add_parser("unmute", help="turn suggestions back on for an event type")
    u.add_argument("kind", choices=sorted(EVENT_KINDS))
    d = sp.add_parser("serve", help="evaluate incoming events in foreground (Ctrl-C to stop)")
    d.add_argument("--interval", type=float, default=3.0)
    d.add_argument("--once", action="store_true", help="evaluate and notify once, then exit")
    d.add_argument("--notify", action="store_true")
    d.add_argument("--notification-consent", action="store_true")
    d.add_argument("--desktop", action="store_true")
    d.add_argument("--llm-triage", action="store_true")
    d.add_argument("--llm-consent", action="store_true")
    d.add_argument("--llm-max-calls", type=int, default=10)
    d.add_argument("--ttl-minutes", type=int, default=120)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    conn = None
    try:
        if args.command in ("config-init", "config-check"):
            from .settings import initialize, load
            result = initialize(args.path) if args.command == "config-init" else load(args.path)
            print(json.dumps({"valid": True, "configuration": result}, indent=2))
            return 0
        app_config = None
        if args.config:
            from .settings import load
            app_config = load(args.config)
            args.db = app_config["database"]
        if args.command == "host-config":
            if not args.config:
                raise ValueError("host-config requires --config")
            from .receivers import snippets
            result = snippets(args.config, args.project, args.tool, sys.executable)
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as output:
                    value = result["config"]
                    output.write(value if isinstance(value, str) else json.dumps(value, indent=2))
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.command == "statusline-config":
            if not args.config:
                raise ValueError("statusline-config requires --config")
            from .statusline import snippet
            print(json.dumps(snippet(args.config, args.project, args.shell), indent=2))
            return 0
        if args.command == 'notification-check':
            from .notifications import native_diagnostics
            print(json.dumps(native_diagnostics(), indent=2))
            return 0
        if args.command.startswith('memory-') and app_config is None:
            raise ValueError('memory commands require --config')
        if args.command == 'memory-hook-config':
            from .memory_hooks import snippets
            result = snippets(args.config, args.project, args.tool, sys.executable)
            if args.output:
                with Path(args.output).open('x', encoding='utf-8') as target:
                    target.write(json.dumps(result['config'], indent=2))
            print(json.dumps(result, indent=2))
            return 0
        conn = connect(args.db)
        if args.command.startswith('memory-'):
            from . import memory
            action = args.command.removeprefix('memory-')
            if action == 'read':
                result = memory.read(conn, app_config, args.project, query=args.query, session=args.session)
            elif action == 'state':
                result = memory.state(conn, app_config, args.project)
            elif action == 'record':
                raw = sys.stdin.buffer.read(65537)
                if len(raw) > 65536:
                    raise ValueError('checkpoint input exceeds 64 KiB')
                payload = json.loads(raw.decode('utf-8'))
                if not isinstance(payload, dict) or set(payload) != {'notes'}:
                    raise ValueError('checkpoint stdin must contain a notes array')
                result = memory.checkpoint(conn, app_config, args.project, session=args.session,
                    delivery_id=args.delivery_id, notes=payload['notes'], tool=args.tool,
                    agent=args.agent, final=args.final)
            elif action == 'dream':
                result = memory.dream(conn, app_config, args.project)
            elif action == 'model-prepare':
                result = memory.semantic_prepare(conn, args.config, args.project)
            elif action == 'model-import':
                result = memory.semantic_import(conn, args.config, args.project, args.agent_id)
            elif action == 'propose':
                raw = sys.stdin.buffer.read(65537)
                if len(raw) > 65536:
                    raise ValueError('memory operations exceed 64 KiB')
                result = memory.propose(conn, app_config, args.project, json.loads(raw.decode('utf-8')), allow_pinned=args.allow_pinned)
            elif action == 'review':
                result = memory.inspect(conn, app_config, args.project, args.id)
            elif action == 'rollback':
                result = memory.rollback(conn, app_config, args.project, args.revision, allow_pinned=args.allow_pinned)
            elif action == 'dismiss':
                result = memory.dismiss(conn, app_config, args.project, args.id)
            else:
                result = memory.act(conn, app_config, args.project, args.id, action, getattr(args, 'expect_scope', None))
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.command.startswith("agent-"):
            if not args.config:
                raise ValueError("live task commands require --config")
            from . import live_agent
            if args.command == "agent-prepare":
                result = live_agent.prepare(conn, args.config, args.project, args.prompt)
            elif args.command == "agent-list":
                result = live_agent.tasks(conn)
            elif args.command == "agent-show":
                result = live_agent.inspect(conn, args.id)
            elif args.command == "agent-run":
                result = live_agent.run(conn, args.id)
            else:
                result = live_agent.decide(conn, args.id, args.command.removeprefix("agent-"),
                                          getattr(args, "expect_scope", None))
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.command == "ui":
            if app_config is None:
                raise ValueError("ui requires --config; create one with config-init")
            if not 0 <= args.port <= 65535:
                raise ValueError("port must be 0-65535")
            from .companion import CompanionServer
            server = CompanionServer(app_config, args.port, args.config)
            worker = server.start_worker()
            print(json.dumps({"url": server.origin, "execution": "preview_only"}), flush=True)
            if args.open:
                import webbrowser
                webbrowser.open(server.origin)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                worker.join(timeout=12)
            return 0
        if args.command == "init":
            result: object = {"state": str(Path(args.db).expanduser()), "created": True}
        elif args.command == "emit":
            event_id, created = ingest_event(conn, kind=args.kind, subject=args.subject,
                                             source=args.source, repo=args.repo,
                                             session=args.session, event_key=args.event_key)
            result = {"event_id": event_id, "created": created}
        elif args.command == "hook-claude-stop":
            raw = sys.stdin.read(65537)
            if len(raw.encode("utf-8")) > 65536:
                raise ValueError("hook input exceeds 64 KiB")
            try:
                event = claude_stop(json.loads(raw), repo=args.repo, delivery_id=args.delivery_id)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("hook stdin must be JSON") from exc
            if event is None:
                result = {"ignored": True}
            else:
                event_id, created = ingest_event(conn, **event)
                result = {"event_id": event_id, "created": created}
        elif args.command == "codex-config":
            db = str(Path(args.db).expanduser().resolve())
            workspace = str(Path(args.workspace).expanduser().resolve())
            python = str(Path(args.python).expanduser().resolve())
            snippet = config_snippet(python=python, db=db, repo=args.repo, workspace=workspace)
            if args.output:
                try:
                    with Path(args.output).open("x", encoding="utf-8") as target:
                        target.write(snippet)
                except FileExistsError as exc:
                    raise ValueError("output already exists; no settings were overwritten") from exc
            result = {"config": snippet, "command": codex_command(python=python, db=db, repo=args.repo, workspace=workspace),
                      "output": args.output, "installed": False}
        elif args.command == "doctor":
            result = doctor(conn, app_config)
        elif args.command == "show":
            result = inspect_proposal(conn, args.id)
        elif args.command == "revoke":
            result = {"status": revoke(conn, args.id)}
        elif args.command == "notify":
            result = notify(conn, consent=args.consent, desktop=args.desktop)
        elif args.command == "emit-json":
            try:
                raw = sys.stdin.read(65537)
                if len(raw.encode("utf-8")) > 65536:
                    raise ValueError("event input exceeds 64 KiB")
                event = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("stdin must contain exactly one valid JSON object") from exc
            if not isinstance(event, dict):
                raise ValueError("stdin must contain a JSON object")
            allowed = {"kind", "subject", "repo", "session", "event_key"}
            extra = set(event) - allowed
            if extra:
                raise ValueError("unexpected fields in event input: " + ", ".join(sorted(extra)))
            if not isinstance(event.get("kind"), str) or not isinstance(event.get("subject"), str):
                raise ValueError("event.kind and event.subject must be strings")
            for optional in ("repo", "session", "event_key"):
                if optional in event and not isinstance(event[optional], str):
                    raise ValueError(f"event.{optional} must be a string")
            event_id, created = ingest_event(conn, source=args.source, kind=event["kind"],
                                             subject=event["subject"], repo=event.get("repo", ""),
                                             session=event.get("session", ""),
                                             event_key=event.get("event_key"))
            result = {"event_id": event_id, "created": created}
        elif args.command == "evaluate":
            result = evaluate_once(conn, ttl_minutes=args.ttl_minutes, llm=args.llm_triage,
                llm_consent=args.llm_consent, max_calls=args.llm_max_calls)
        elif args.command == "list":
            result = [{**row, "steps": json.loads(row["steps"]), "scope": json.loads(row["scope_json"]) if row["scope_json"] else None} for row in
                      list_proposals(conn, status=args.status)]
        elif args.command in ("approve", "reject"):
            result = {"status": decide(conn, args.id, approve=args.command == "approve",
                expected_scope=args.expect_scope if args.command == "approve" else None)}
        elif args.command == "run":
            result = run_one(conn, proposal_id=args.id) or {"status": "nothing_approved"}
        elif args.command == "stats":
            result = stats(conn)
        elif args.command == "recover":
            result = {"marked_interrupted": mark_interrupted(conn)}
        elif args.command in ("mute", "unmute"):
            mute_kind(conn, args.kind, mute=args.command == "mute")
            result = {"kind": args.kind, "muted": args.command == "mute"}
        elif args.command == "serve":
            if not math.isfinite(args.interval) or args.interval < 0.2:
                raise ValueError("interval must be finite and at least 0.2 seconds")
            def tick():
                return evaluate_once(conn, ttl_minutes=args.ttl_minutes, llm=args.llm_triage,
                    llm_consent=args.llm_consent, max_calls=args.llm_max_calls,
                    notifications=args.notify, notification_consent=args.notification_consent, desktop=args.desktop)
            if args.once:
                result = tick()
            else:
                print("Foreground proposal worker; execution requires separate approval and run commands.", flush=True)
                try:
                    while True:
                        result = tick()
                        if result["proposals_created"] or result.get("notification", {}).get("count"):
                            print(json.dumps(result), flush=True)
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    print("Stopped.")
                return 0
        else:
            raise AssertionError("unhandled command")
    except (ValueError, OSError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "local file operation failed; check paths and permissions"
        print(json.dumps({"error": message}))
        return 1 if args.command == "hook-claude-stop" else 2
    finally:
        if conn is not None:
            conn.close()
    if args.command == "hook-claude-stop" and args.quiet:
        return 0
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
