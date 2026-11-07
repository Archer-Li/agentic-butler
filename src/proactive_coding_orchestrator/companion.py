"""Loopback companion. UI decisions use the same transactional approval gate."""
from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import urlsplit

from .core import connect, decide, inspect_proposal, list_proposals, revoke, run_one, utc_now
from .settings import event_enabled

ASSETS = Path(__file__).parent / "web"


def snapshot(conn, config: dict, memory_config=None) -> dict:
    events = {r["id"]: r for r in conn.execute("SELECT id,repo,source FROM events")}
    rows = [row for row in list_proposals(conn) if event_enabled(config, events[row["event_id"]])]
    proposals = []
    for row in rows:
        view = inspect_proposal(conn, row["id"])
        execution = conn.execute("SELECT output FROM executions WHERE proposal_id=?", (row["id"],)).fetchone()
        proposals.append({"id": row["id"], "title": row["title"], "explanation": row["explanation"],
            "project": row["repo"], "session": row["session"], "status": row["status"],
            "scope": view["scope"], "scope_hash": row["scope_hash"], "expired": view["expired"],
            "integrity_valid": view["integrity_valid"], "can_execute": view["can_execute"], "audit": view["audit"],
            "output": execution[0] if execution else None})
    pending = sum(p["status"] == "pending" and not p["expired"] for p in proposals)
    approved = sum(p["can_execute"] for p in proposals)
    failures = sum(p["status"] in ("failed", "interrupted") for p in proposals)
    adapters = []
    for project in config["projects"]:
        for tool in project["tools"]:
            source = "codex-notify" if tool == "codex" else "claude-stop"
            event = conn.execute("SELECT MAX(created_at) FROM events WHERE repo=? AND source=?",
                                 (project["name"], source)).fetchone()[0]
            age = (utc_now() - datetime.fromisoformat(event)).total_seconds() if event else None
            adapters.append({"tool": tool, "project": project["name"], "last_event": event,
                "observation": "no_events" if event is None else ("recent_event" if age < 300 else "stale_event")})
    state = "attention" if failures else ("review" if pending else ("approved" if approved else "quiet"))
    from .live_agent import tasks
    allowed = {p['name'] for p in config['projects'] if 'codex' in p['tools']}
    live = [job for job in tasks(conn) if job['scope'] and job['scope']['project'] in allowed] if config['agent']['enabled'] else []
    agent_pending = sum(j['status'] == 'pending' and not j['expired'] for j in live)
    if agent_pending and state == 'quiet':
        state = 'review'
    from . import memory
    memory_config = memory_config or config
    memories = [memory.state(conn, memory_config, name) for name in memory_config['memory']['projects']
                if memory.opted_in(memory_config, name)]
    memory_pending = sum(change['status'] in ('pending', 'approved') and not change['expired'] and not change['stale']
                         for project in memories for change in project['changes'])
    if memory_pending and state == 'quiet':
        state = 'review'
    references = {project['project']: project['bundle'] for project in memories}
    for proposal in proposals:
        proposal['memory_context'] = references.get(proposal['project'])
    return {"agent_pending": agent_pending, "state": state, "pending": pending, "approved": approved, "failures": failures,
        "memory": memory_config['memory'], "memories": memories, "memory_pending": memory_pending,
        "agent": config['agent'], "agent_jobs": live,
        "proposals": proposals, "adapters": adapters, "appearance": config["appearance"],
        "notifications": config["notifications"], "execution": "preview_only",
        "projects": [{"name": p["name"], "tools": p["tools"]} for p in config["projects"]]}


def act(conn, config, proposal_id: str, action: str, scope_hash: str | None = None) -> dict:
    row = conn.execute("SELECT e.repo,e.source FROM proposals p JOIN events e ON e.id=p.event_id WHERE p.id=?", (proposal_id,)).fetchone()
    if row is None or not event_enabled(config, row):
        raise ValueError("proposal is not from an enabled project/tool")
    if action == "approve":
        if not isinstance(scope_hash, str) or not scope_hash:
            raise ValueError("reviewed scope hash required")
        return {"status": decide(conn, proposal_id, approve=True, expected_scope=scope_hash)}
    if action == "reject":
        return {"status": decide(conn, proposal_id, approve=False)}
    if action == "revoke":
        return {"status": revoke(conn, proposal_id)}
    if action == "preview":
        result = run_one(conn, proposal_id=proposal_id) or {"status": "nothing_approved"}
        from . import memory
        if memory.opted_in(config, row['repo']) and result.get('status') == 'completed':
            memory.signal(conn, config, row['repo'], proposal_id, 'workflow_complete')
        return result
    raise ValueError("unsupported action")


def evaluate_projects(conn, config):
    # Apply mute and tool opt-out before any model request/budget reservation.
    events = conn.execute("SELECT * FROM events WHERE processed_at IS NULL ORDER BY created_at,id").fetchall()
    events = [event for event in events if event_enabled(config, event)]
    muted = {row[0] for row in conn.execute("SELECT kind FROM muted_kinds")}
    muted_ids = {event["id"] for event in events if event["kind"] in muted}
    events = [event for event in events if event["kind"] not in muted]
    if config["triage"]["enabled"]:
        from .integrations import triage
        events = [event for event in events if triage(conn, dict(event),
            consent=config["triage"]["consent"], max_calls=config["triage"]["max_calls"])]
    from .core import propose_pending
    return propose_pending(conn, eligible={event["id"] for event in events} | muted_ids,
                           ttl_minutes=config["approval"]["ttl_minutes"])


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: dict, port: int = 0, config_path=None):
        self.config = config
        self.config_path = str(Path(config_path).resolve()) if config_path else None
        self.token = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(24)
        self.worker_error = False
        self.stop_event = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def memory_config(self):
        if not self.config_path:
            return self.config
        from .settings import load
        current = load(self.config_path)
        if Path(current['database']).resolve() != Path(self.config['database']).resolve():
            raise ValueError('restart the companion after changing its database')
        return current

    def worker(self):
        while not self.stop_event.is_set():
            conn = None
            try:
                conn = connect(self.config["database"])
                evaluate_projects(conn, self.config)
                from .memory import tick
                tick(conn, self.memory_config())
                notices = self.config["notifications"]
                if notices["enabled"] and notices["backend"] != "browser":
                    from .notifications import deliver_batch
                    deliver_batch(conn, self.config, self.origin)
                self.worker_error = False
            except Exception:
                self.worker_error = True
            finally:
                if conn is not None:
                    conn.close()
            self.stop_event.wait(2)

    def start_worker(self):
        thread = threading.Thread(target=self.worker, daemon=True)
        thread.start()
        return thread

    def server_close(self):
        self.stop_event.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # never log tokens, proposal data or callback payloads

    def send(self, status, content, mime="application/json"):
        raw = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime if mime == "image/png" else mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'nonce-{self.server.nonce}'; style-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self, api=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send(403, {"error": "invalid host"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self.send(403, {"error": "foreign origin"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and (api or self.command != "GET"):
            self.send(403, {"error": "cross-site request"})
            return False
        if api and not secrets.compare_digest(self.headers.get("X-PCO-Token", ""), self.server.token):
            self.send(403, {"error": "invalid session"})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(api=path.startswith("/api/")):
            return
        if path == "/api/state":
            conn = None
            try:
                conn = connect(self.server.config["database"])
                result = snapshot(conn, self.server.config, self.server.memory_config())
                from .notifications import link_status
                result['notification_link'] = link_status(conn, self.server.origin)
                result["worker_error"] = self.server.worker_error
                self.send(200, result)
            except (ValueError, OSError, sqlite3.Error):
                self.send(503, {"error": "local state unavailable"})
            finally:
                if conn is not None:
                    conn.close()
        elif path in ("/", "/review"):
            page = (ASSETS / "index.html").read_text(encoding="utf-8")
            page = page.replace("__NONCE__", self.server.nonce).replace("__TOKEN__", self.server.token)
            self.send(200, page.encode(), "text/html")
        elif path in ("/app.js", "/memory.js", "/style.css"):
            self.send(200, (ASSETS / path[1:]).read_bytes(), "text/javascript" if path.endswith(".js") else "text/css")
        elif path in ("/brand-icon.png", "/favicon.png"):
            self.send(200, (ASSETS / path[1:]).read_bytes(), "image/png")
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed(api=True):
            return
        conn = None
        try:
            if self.headers.get("Content-Type") != "application/json":
                raise ValueError("JSON required")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("invalid request size")
            data = json.loads(self.rfile.read(size))
            if self.path == '/api/memory':
                from . import memory
                config = self.server.memory_config()
                if not isinstance(data, dict) or not isinstance(data.get('project'), str):
                    raise ValueError('memory action needs an enabled project')
                project = data['project']
                action = data.get('action')
                conn = connect(config['database'])
                if action == 'record' and set(data) == {'action', 'project', 'session', 'delivery_id', 'notes', 'final'}:
                    result = memory.checkpoint(conn, config, project, session=data['session'],
                        delivery_id=data['delivery_id'], notes=data['notes'], final=data['final'])
                elif action == 'dream' and set(data) == {'action', 'project'}:
                    result = memory.dream(conn, config, project)
                elif action == 'model-prepare' and set(data) == {'action', 'project'} and self.server.config_path:
                    result = memory.semantic_prepare(conn, self.server.config_path, project)
                elif action == 'model-import' and set(data) == {'action', 'project', 'agent_id'} and self.server.config_path:
                    if not isinstance(data['agent_id'], str):
                        raise ValueError('semantic task ID is required')
                    result = memory.semantic_import(conn, self.server.config_path, project, data['agent_id'])
                elif action == 'propose' and set(data) == {'action', 'project', 'operations', 'allow_pinned', 'expected_revision'}:
                    if type(data['expected_revision']) is not int:
                        raise ValueError('reviewed base revision is required')
                    result = memory.propose(conn, config, project, data['operations'],
                        allow_pinned=data['allow_pinned'], expected_revision=data['expected_revision'])
                elif action == 'rollback' and set(data) == {'action', 'project', 'revision', 'allow_pinned'}:
                    result = memory.rollback(conn, config, project, data['revision'], allow_pinned=data['allow_pinned'])
                elif set(data) == {'action', 'project', 'id', 'scope_hash'} and isinstance(data['id'], str):
                    if action == 'dismiss':
                        result = memory.dismiss(conn, config, project, data['id'])
                    else:
                        result = memory.act(conn, config, project, data['id'], action, data['scope_hash'])
                else:
                    raise ValueError('invalid memory action fields')
                self.send(200, result)
                return
            if self.path == '/api/notification-open':
                from .notifications import record_open
                if not isinstance(data, dict) or set(data) != {'notification_id'}:
                    raise ValueError('invalid notification receipt fields')
                conn = connect(self.server.config['database'])
                self.send(200, record_open(conn, self.server.origin, data['notification_id']))
                return
            if self.path == '/api/agent':
                from . import live_agent
                if not self.server.config_path or not self.server.config['agent']['enabled']:
                    raise ValueError('live text tasks are disabled')
                conn = connect(self.server.config['database'])
                if isinstance(data, dict) and set(data) == {'action', 'project', 'prompt'} and data['action'] == 'prepare':
                    result = live_agent.prepare(conn, self.server.config_path, data['project'], data['prompt'])
                elif isinstance(data, dict) and set(data) == {'id', 'action', 'scope_hash'}:
                    job = live_agent.inspect(conn, data['id'])
                    if not job['scope'] or job['scope']['config_path'] != self.server.config_path:
                        raise ValueError('task belongs to another configuration')
                    if data['action'] == 'run':
                        result = live_agent.run(conn, data['id'])
                    else:
                        result = live_agent.decide(conn, data['id'], data['action'], data['scope_hash'])
                else:
                    raise ValueError('invalid live task fields')
                self.send(200, result)
                return
            if not isinstance(data, dict) or set(data) != {"id", "action", "scope_hash"}:
                raise ValueError("invalid action fields")
            if self.path != "/api/action":
                self.send(404, {"error": "not found"})
                return
            conn = connect(self.server.config["database"])
            if data == {'id': None, 'action': 'test-native', 'scope_hash': None}:
                from .notifications import native_notice, review_link, NotificationBlocked
                if not self.server.config['notifications']['enabled'] or self.server.config['notifications']['backend'] != 'native':
                    raise ValueError('native notifications are not enabled')
                link = review_link(conn, self.server.origin, 'native')
                try:
                    metadata = native_notice(1, link['url'])
                except NotificationBlocked as exc:
                    self.send(200, {'status': 'blocked', 'os_setting': exc.setting, 'display_verified': False, **link})
                    return
                except Exception:
                    self.send(502, {'error': 'OS notification submission failed'})
                    return
                self.send(200, {**(metadata if isinstance(metadata, dict) else {}), 'status': 'submitted', 'display_verified': False, **link})
                return
            if data["id"] is None and data["action"] == "notify" and data["scope_hash"] is None:
                from .notifications import deliver_batch
                self.send(200, deliver_batch(conn, self.server.config, self.server.origin, browser=True))
                return
            if not isinstance(data["id"], str) or not isinstance(data["action"], str):
                raise ValueError("invalid action")
            result = act(conn, self.server.config, data["id"], data["action"], data["scope_hash"])
            self.send(200, result)
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except (OSError, sqlite3.Error):
            self.send(503, {"error": "local state unavailable"})
        finally:
            if conn is not None:
                conn.close()
