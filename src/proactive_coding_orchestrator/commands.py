"""Configured local commands with one-shot, scope-bound approval."""
from __future__ import annotations

from datetime import datetime, timedelta
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
from .live_agent import binary_hash, stop_process
from .settings import load

OUTPUT_LIMIT = 65536
EFFECT = ('Runs project code as your current OS user. Tests can read or write files, '
          'access the network and start child processes. Output is stored locally. '
          'This runner provides no OS isolation or privilege elevation.')


def initialize(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS command_jobs (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_hash TEXT NOT NULL,
        approved_hash TEXT, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, finished_at TEXT, output TEXT, exit_code INTEGER);
        CREATE TABLE IF NOT EXISTS command_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
        status TEXT NOT NULL, at TEXT NOT NULL);""")


def audit(conn, key, status):
    conn.execute('INSERT INTO command_audit(job_id,status,at) VALUES (?,?,?)',
                 (key, status, timestamp()))


def definition(config, name):
    if not config['commands']['enabled']:
        raise ValueError('local commands are disabled')
    entry = next((item for item in config['commands']['allowlist'] if item['name'] == name), None)
    if entry is None:
        raise ValueError('command is not configured')
    project = next(p for p in config['projects'] if p['name'] == entry['project'])
    workspace = Path(project['workspace']).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('configured workspace must be a directory')
    executable = shutil.which(entry['argv'][0])
    if not executable or not Path(executable).is_absolute():
        raise ValueError('command executable must resolve to an absolute path')
    executable = str(Path(executable).resolve(strict=True))
    if Path(executable).suffix.lower() in ('.cmd', '.bat', '.ps1', '.sh') or Path(executable).stem.lower() in (
            'sudo', 'su', 'doas', 'pkexec', 'runas', 'cmd', 'powershell', 'pwsh', 'sh', 'bash', 'zsh'):
        raise ValueError('shell and elevation launchers are unsupported; use a direct executable')
    return entry, str(workspace), executable


def prepare(conn, config_path, name):
    config = load(config_path)
    entry, workspace, executable = definition(config, name)
    key = str(uuid4())
    scope = {'version': 1, 'id': key, 'name': name, 'project': entry['project'],
             'workspace': workspace, 'argv': [executable, *entry['argv'][1:]],
             'executable_hash': binary_hash(executable), 'effect': EFFECT,
             'timeout_seconds': entry['timeout_seconds'], 'output_limit_bytes': OUTPUT_LIMIT,
             'config_path': str(Path(config_path).resolve()), 'config_hash': digest(config),
             'expires_at': timestamp(utc_now() + timedelta(minutes=config['approval']['ttl_minutes']))}
    initialize(conn)
    with conn:
        conn.execute('INSERT INTO command_jobs(id,scope,scope_hash,status,created_at) VALUES (?,?,?,?,?)',
                     (key, canonical(scope), digest(scope), 'pending', timestamp()))
        audit(conn, key, 'pending')
    return inspect(conn, key)


def inspect(conn, key):
    initialize(conn)
    row = conn.execute('SELECT * FROM command_jobs WHERE id=?', (key,)).fetchone()
    if row is None:
        raise ValueError('command task not found')
    result = dict(row)
    try:
        scope = json.loads(row['scope'])
        valid = digest(scope) == row['scope_hash']
        expired = datetime.fromisoformat(scope['expires_at']) <= utc_now()
    except (ValueError, TypeError, KeyError):
        scope, valid, expired = None, False, True
    result.update(scope=scope, integrity_valid=valid, expired=expired)
    result['audit'] = [dict(r) for r in conn.execute(
        'SELECT status,at FROM command_audit WHERE job_id=? ORDER BY seq', (key,))]
    return result


def tasks(conn):
    initialize(conn)
    keys = [row[0] for row in conn.execute('SELECT id FROM command_jobs ORDER BY created_at DESC LIMIT 100')]
    return [inspect(conn, key) for key in keys]


def check_policy(scope, *, executable=False):
    config = load(scope['config_path'])
    if digest(config) != scope['config_hash']:
        raise ValueError('configuration changed; prepare and review a new command')
    entry, workspace, binary = definition(config, scope['name'])
    if (scope['argv'] != [binary, *entry['argv'][1:]] or workspace != scope['workspace']
            or entry['project'] != scope['project'] or scope['effect'] != EFFECT):
        raise ValueError('command scope changed; prepare and review a new command')
    if executable and binary_hash(binary) != scope['executable_hash']:
        raise ValueError('executable changed; prepare and review a new command')


def decide(conn, key, action, expected_scope=None):
    initialize(conn)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM command_jobs WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('command task not found')
        status = row['status']
        if action == 'approve':
            scope = json.loads(row['scope'])
            if status != 'pending' or digest(scope) != row['scope_hash'] or expected_scope != row['scope_hash']:
                raise ValueError('command is not pending or reviewed scope does not match')
            check_policy(scope, executable=True)
            status = 'expired' if datetime.fromisoformat(scope['expires_at']) <= utc_now() else 'approved'
            conn.execute('UPDATE command_jobs SET status=?,approved_hash=? WHERE id=?',
                         (status, row['scope_hash'] if status == 'approved' else None, key))
        elif action == 'revoke' and status in ('pending', 'approved'):
            status = 'revoked'
            conn.execute('UPDATE command_jobs SET status=?,approved_hash=NULL WHERE id=?', (status, key))
        elif action == 'cancel' and status == 'running':
            status = 'cancelling'
            conn.execute('UPDATE command_jobs SET cancel_requested=1 WHERE id=?', (key,))
        else:
            raise ValueError('action is not valid for the current command state')
        audit(conn, key, status)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {'id': key, 'status': status}


def invoke(scope, cancelled):
    # Exclude inherited API keys, interpreter options and shell startup settings.
    allowed = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.Popen(scope['argv'], cwd=scope['workspace'], shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        start_new_session=os.name != 'nt',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    output = bytearray()
    overflow = threading.Event()
    finished = threading.Event()

    def reader():
        try:
            while block := proc.stdout.read(1024):
                room = max(0, scope['output_limit_bytes'] - len(output))
                output.extend(block[:room])
                if len(block) > room:
                    overflow.set()
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    started = time.monotonic()
    status, diagnostic = None, ''
    try:
        while proc.poll() is None or not finished.is_set():
            if cancelled():
                status, diagnostic = 'cancelled', 'Cancellation requested.'
                break
            if overflow.is_set():
                status, diagnostic = 'failed', 'Output limit reached.'
                break
            if time.monotonic() - started >= scope['timeout_seconds']:
                status, diagnostic = 'failed', 'Execution timed out.'
                break
            time.sleep(0.05)
        if overflow.is_set() and status is None:
            status, diagnostic = 'failed', 'Output limit reached.'
    finally:
        if status is not None or proc.poll() is None:
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            else:
                stop_process(proc)
        thread.join(timeout=2)
        if finished.is_set():
            proc.stdout.close()
    status = status or ('completed' if proc.returncode == 0 else 'failed')
    text = bytes(output).decode('utf-8', errors='replace')
    if diagnostic:
        text += '\n' + diagnostic
    return status, text, proc.returncode


def run(conn, key, expected_scope):
    initialize(conn)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM command_jobs WHERE id=?', (key,)).fetchone()
        if row is None or row['status'] != 'approved':
            raise ValueError('an explicitly approved command is required')
        scope = json.loads(row['scope'])
        if (digest(scope) != row['scope_hash'] or row['approved_hash'] != row['scope_hash']
                or expected_scope != row['scope_hash']):
            raise ValueError('approved command scope does not match')
        if datetime.fromisoformat(scope['expires_at']) <= utc_now():
            raise ValueError('command approval expired')
        check_policy(scope, executable=True)
        conn.execute("UPDATE command_jobs SET status='running' WHERE id=?", (key,))
        audit(conn, key, 'running')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    def cancelled():
        if conn.execute('SELECT cancel_requested FROM command_jobs WHERE id=?', (key,)).fetchone()[0]:
            return True
        try:
            check_policy(scope)
            return False
        except (ValueError, OSError):
            return True

    try:
        if cancelled():
            status, output, code = 'cancelled', 'Cancelled before launch.', None
        else:
            status, output, code = invoke(scope, cancelled)
    except Exception:
        status, output, code = 'failed', 'Command invocation failed. Check the configured executable and workspace.', None
    if cancelled():
        status = 'cancelled'
    with conn:
        conn.execute('UPDATE command_jobs SET status=?,output=?,exit_code=?,finished_at=? WHERE id=?',
                     (status, output, code, timestamp(), key))
        audit(conn, key, status)
    return {'id': key, 'status': status, 'output': output, 'exit_code': code}
