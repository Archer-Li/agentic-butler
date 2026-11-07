"""Configuration-driven host callbacks. No source files or transcripts are read."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .core import connect, digest, ingest_event
from .codex_hook import normalize as codex_event
from .integrations import claude_stop
from .settings import load


def project_for(config, name, tool):
    project = next((p for p in config["projects"] if p["name"] == name), None)
    if project is None or tool not in project["tools"]:
        raise ValueError("tool/project is not opted in")
    return project


def receive(config, name, tool, payload):
    project = project_for(config, name, tool)
    if not isinstance(payload, dict):
        raise ValueError("callback must be an object")
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("callback needs an absolute cwd")
    if os.path.normcase(os.path.normpath(cwd)) != os.path.normcase(os.path.normpath(project["workspace"])):
        return None
    if tool == "codex":
        event = codex_event(payload, repo=name, workspace=project["workspace"])
    else:
        # Stop has no native delivery ID. Fingerprint the final answer in memory
        # only, after workspace filtering. Identical answers coalesce per session.
        answer = payload.get("last_assistant_message")
        if not isinstance(answer, str):
            raise ValueError("Claude Stop requires last_assistant_message for deduplication")
        event = claude_stop(payload, repo=name, delivery_id=digest(answer))
    if event is None:
        return None
    conn = connect(config["database"])
    try:
        result = ingest_event(conn, **event)
        from . import memory
        if memory.opted_in(config, name):
            memory.signal(conn, config, name, digest(event), 'turn_complete')
        return result
    finally:
        conn.close()


def snippets(config_path: str, name: str, tool: str, python: str, *, shell="posix") -> dict:
    config = load(config_path)
    project_for(config, name, tool)
    argv = [str(Path(python).resolve()), "-m", "proactive_coding_orchestrator.receivers",
            "--config", str(Path(config_path).resolve()), "--project", name, "--tool", tool]
    if tool == "codex":
        return {"tool": tool, "argv": argv, "config": "notify = " + json.dumps(argv, ensure_ascii=False), "installed": False}
    hook = {"type": "command", "command": argv[0], "args": argv[1:], "timeout": 10, "onFailure": "continue"}
    return {"tool": tool, "argv": argv, "config": {"hooks": {"Stop": [{"hooks": [hook]}]}},
            "installed": False}



def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--tool", choices=("codex", "claude"), required=True)
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(argv)
    try:
        raw = args.payload if args.payload is not None else sys.stdin.read(65537)
        if len(raw.encode("utf-8")) > 65536:
            raise ValueError("callback too large")
        receive(load(args.config), args.project, args.tool, json.loads(raw))
        return 0
    except Exception:
        print("PCO callback was not recorded; check configuration and workspace.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
