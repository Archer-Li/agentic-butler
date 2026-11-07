"""Explicitly approved, tool-free Codex turns. Separate from preview approvals."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from uuid import uuid4

from .core import canonical, digest, timestamp, utc_now
from .settings import load
from .receivers import project_for, snippets

DISABLED_FEATURES = ('shell_tool', 'apps', 'plugins', 'hooks', 'multi_agent',
                     'computer_use', 'browser_use', 'image_generation', 'skill_search',
                     'skill_mcp_dependency_install', 'memories', 'view_image', 'code_mode_host')
OUTPUT_LIMIT = 65536
DISABLED_HOST_DIAGNOSTIC = ('Code Mode is unavailable because code-mode host is disabled. '
    'Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`.')


def initialize(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS agent_jobs (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_hash TEXT NOT NULL,
        approved_hash TEXT, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, finished_at TEXT, output TEXT);
        CREATE TABLE IF NOT EXISTS agent_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
        status TEXT NOT NULL, at TEXT NOT NULL);""")


def audit(conn, key, status):
    conn.execute('INSERT INTO agent_audit(job_id,status,at) VALUES (?,?,?)', (key, status, timestamp()))


def binary_hash(path):
    value = sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def prepare(conn, config_path, project, prompt, *, memory_draft_input=None):
    config = load(config_path)
    if not config['agent']['enabled']:
        raise ValueError('live text-agent tasks are disabled in configuration')
    target = project_for(config, project, 'codex')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode('utf-8')) > 8000:
        raise ValueError('task text must contain 1–8000 UTF-8 bytes')
    cli = shutil.which('codex')
    if not cli or not Path(cli).is_file():
        raise ValueError('Codex CLI is not installed')
    if not Path(target['workspace']).is_dir():
        raise ValueError('enabled personal workspace does not exist')
    key = str(uuid4())
    scope = {'version': 1, 'id': key, 'project': project, 'workspace': target['workspace'],
        'prompt': prompt, 'provider': 'codex', 'allowed_tools': [], 'effect': 'text_generation',
        'data_disclosure': 'Task text is sent to OpenAI using the existing Codex login. No source files are supplied.',
        'timeout_seconds': config['agent']['timeout_seconds'], 'output_limit_bytes': OUTPUT_LIMIT,
        'expires_at': timestamp(utc_now() + timedelta(minutes=config['approval']['ttl_minutes'])),
        'config_path': str(Path(config_path).resolve()), 'config_hash': digest(config),
        'executable': str(Path(cli).resolve()), 'executable_hash': binary_hash(cli)}
    from . import memory
    if memory_draft_input is not None:
        scope['memory_draft_input'] = memory_draft_input
    elif memory.opted_in(config, project):
        scope['memory_context'] = memory.read(conn, config, project, query=prompt)
        if len(task_text(scope).encode('utf-8')) > 8000:
            raise ValueError('task plus memory exceeds 8000 bytes; shorten the task or context budget')
    initialize(conn)
    with conn:
        conn.execute('INSERT INTO agent_jobs(id,scope,scope_hash,status,created_at) VALUES (?,?,?,?,?)',
                     (key, canonical(scope), digest(scope), 'pending', timestamp()))
        audit(conn, key, 'pending')
    return inspect(conn, key)


def inspect(conn, key):
    initialize(conn)
    row = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (key,)).fetchone()
    if row is None:
        raise ValueError('agent task not found')
    result = dict(row)
    try:
        scope = json.loads(row['scope'])
        valid = digest(scope) == row['scope_hash']
        expired = datetime.fromisoformat(scope['expires_at']) <= utc_now()
    except (ValueError, TypeError, KeyError):
        scope, valid, expired = None, False, True
    result.update(scope=scope, integrity_valid=valid, expired=expired)
    result['audit'] = [dict(r) for r in conn.execute(
        'SELECT status,at FROM agent_audit WHERE job_id=? ORDER BY seq', (key,))]
    return result


def tasks(conn):
    initialize(conn)
    keys = [r[0] for r in conn.execute('SELECT id FROM agent_jobs ORDER BY created_at DESC LIMIT 100')]
    return [inspect(conn, key) for key in keys]


def check_policy(scope, *, executable=False, conn=None):
    config = load(scope['config_path'])
    project_for(config, scope['project'], 'codex')
    if not config['agent']['enabled'] or digest(config) != scope['config_hash']:
        raise ValueError('configuration changed; create and review a new task')
    if scope['allowed_tools'] != [] or scope['effect'] != 'text_generation':
        raise ValueError('unsupported live task capability')
    if executable and binary_hash(scope['executable']) != scope['executable_hash']:
        raise ValueError('Codex executable changed; create a new task')
    if scope.get('memory_context'):
        from . import memory
        from .core import connect
        owned = conn is None
        if owned:
            conn = connect(config['database'])
        try:
            if not memory.bundle_valid(conn, config, scope['memory_context']):
                raise ValueError('reviewed memory was revised or retired; prepare a new task')
        finally:
            if owned:
                conn.close()
    if scope.get('memory_draft_input'):
        from . import memory
        from .core import connect
        owned = conn is None
        if owned:
            conn = connect(config['database'])
        try:
            if not memory.draft_input_valid(conn, config, scope['memory_draft_input']):
                raise ValueError('memory draft input changed; prepare a fresh semantic task')
        finally:
            if owned:
                conn.close()


def decide(conn, key, action, expected_scope=None):
    initialize(conn)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('agent task not found')
        status = row['status']
        if action == 'approve':
            scope = json.loads(row['scope'])
            if status != 'pending' or digest(scope) != row['scope_hash'] or expected_scope != row['scope_hash']:
                raise ValueError('task is not pending or reviewed scope does not match')
            check_policy(scope, conn=conn)
            status = 'expired' if datetime.fromisoformat(scope['expires_at']) <= utc_now() else 'approved'
            conn.execute('UPDATE agent_jobs SET status=?,approved_hash=? WHERE id=?',
                         (status, row['scope_hash'] if status == 'approved' else None, key))
        elif action == 'revoke' and status in ('pending', 'approved'):
            status = 'revoked'
            conn.execute('UPDATE agent_jobs SET status=?,approved_hash=NULL WHERE id=?', (status, key))
        elif action == 'cancel' and status == 'running':
            status = 'cancelling'
            conn.execute('UPDATE agent_jobs SET cancel_requested=1 WHERE id=?', (key,))
        else:
            raise ValueError('action is not valid for the current task state')
        audit(conn, key, status)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {'id': key, 'status': status}


def command(scope):
    notify = snippets(scope['config_path'], scope['project'], 'codex', os.sys.executable)
    argv = [scope['executable'], 'exec', '--ignore-user-config', '--ephemeral', '--sandbox', 'read-only',
            '--skip-git-repo-check', '--cd', scope['workspace'], '--json', '--color', 'never']
    for feature in DISABLED_FEATURES:
        argv += ['--disable', feature]
    argv += ['-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"', '-c', 'approval_policy="on-request"', '-c',
             'notify=' + json.dumps(notify['argv'], ensure_ascii=False)]
    return argv


def task_text(scope):
    context = scope.get('memory_context')
    if context:
        return '<project_reference>\n' + context['text'] + '</project_reference>\n\nTask:\n' + scope['prompt']
    return scope['prompt']


def stop_process(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True, timeout=5,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)


def invoke(scope, cancelled):
    # Inherit only OS/login/runtime paths; never inherit cloud API keys/endpoints.
    allowed = {'PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','USERPROFILE','HOME',
               'APPDATA','LOCALAPPDATA','TEMP','TMP','LANG','LC_ALL','CODEX_HOME'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.Popen(command(scope), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=scope['workspace'], env=env, start_new_session=os.name != 'nt',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    output = bytearray()
    total = [0]
    lock = threading.Lock()
    overflow = threading.Event()
    def reader(stream, retain):
        while block := stream.read(1024):
            with lock:
                total[0] += len(block)
                if total[0] > scope['output_limit_bytes']:
                    overflow.set()
                elif retain:
                    output.extend(block)
    readers = [threading.Thread(target=reader, args=(proc.stdout, True), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, False), daemon=True)]
    for thread in readers:
        thread.start()
    prompt = 'Analyze only the task text below. Use no tools, read no files and request no privileges. Return a concise textual answer.\n\n' + task_text(scope)
    try:
        proc.stdin.write(prompt.encode('utf-8'))
        proc.stdin.close()
        start = time.monotonic()
        while proc.poll() is None:
            if cancelled():
                stop_process(proc)
                return 'cancelled', ''
            if overflow.is_set() or time.monotonic() - start > scope['timeout_seconds']:
                stop_process(proc)
                return 'failed', 'Output limit reached or execution timed out.'
            time.sleep(0.1)
        for thread in readers:
            thread.join(timeout=2)
        if overflow.is_set() or proc.returncode:
            return 'failed', 'Codex failed; verify CLI login and compatibility. Raw stderr was not retained.'
        return parse_output(bytes(output))
    finally:
        if proc.poll() is None:
            stop_process(proc)
        for stream in (proc.stdout, proc.stderr):
            stream.close()


def run(conn, key):
    initialize(conn)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (key,)).fetchone()
        if row is None or row['status'] != 'approved':
            raise ValueError('an explicitly approved agent task is required')
        scope = json.loads(row['scope'])
        if digest(scope) != row['scope_hash'] or row['approved_hash'] != row['scope_hash']:
            raise ValueError('approved task integrity failed')
        if datetime.fromisoformat(scope['expires_at']) <= utc_now():
            raise ValueError('agent approval expired')
        check_policy(scope, executable=True, conn=conn)
        conn.execute("UPDATE agent_jobs SET status='running' WHERE id=?", (key,))
        audit(conn, key, 'running')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    def cancelled():
        if conn.execute('SELECT cancel_requested FROM agent_jobs WHERE id=?', (key,)).fetchone()[0]:
            return True
        try:
            check_policy(scope, conn=conn)
            return False
        except Exception:
            return True
    try:
        status, output = invoke(scope, cancelled)
    except Exception:
        status, output = 'failed', 'Agent invocation failed. No raw error or credentials were retained.'
    if cancelled():
        status, output = 'cancelled', ''
    with conn:
        conn.execute('UPDATE agent_jobs SET status=?,output=?,finished_at=? WHERE id=?', (status, output, timestamp(), key))
        audit(conn, key, status)
    return {'id': key, 'status': status, 'output': output}


def parse_output(raw):
    messages = []
    completed = False
    for line in raw.decode('utf-8', errors='replace').splitlines():
        event = json.loads(line)
        if event.get('type') == 'turn.completed':
            completed = True
        if event.get('type') == 'item.completed':
            item = event.get('item', {})
            if item.get('type') == 'agent_message':
                messages.append(item.get('text', ''))
            elif item.get('type') == 'error' and item.get('message') == DISABLED_HOST_DIAGNOSTIC:
                # Known startup diagnostic in 0.165 alpha: host deliberately disabled.
                # This exact diagnostic describes absence of a capability, not an action.
                continue
            elif item.get('type') != 'reasoning':
                kind = str(item.get('type', 'missing'))
                kind = ''.join(c for c in kind[:60] if c.isascii() and (c.isalnum() or c == '_'))
                return 'failed', 'Unexpected item type (' + kind + '); text-only contract violated.'
    if not completed or not messages:
        return 'failed', 'Codex did not return a complete textual result.'
    return 'completed', '\n'.join(messages)[:16000]
