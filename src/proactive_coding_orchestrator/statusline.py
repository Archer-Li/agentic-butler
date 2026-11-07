"""Read-only Claude Code statusLine protocol. Never authorizes or runs tasks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys

from .core import timestamp
from .receivers import project_for
from .settings import load


def same_workspace(left, right):
    return isinstance(left, str) and os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def render(config_path, project, payload):
    config = load(config_path)
    target = project_for(config, project, 'claude')
    workspace = payload.get('workspace', {})
    cwd = workspace.get('current_dir') if isinstance(workspace, dict) else None
    cwd = cwd or payload.get('cwd')
    if not same_workspace(cwd, target['workspace']):
        return ''
    database = Path(config['database'])
    if not database.is_file():
        return '[PCO] ' + project + ' | No events yet'
    conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=0.2)
    try:
        rows = conn.execute('''SELECT p.status,COUNT(*) FROM proposals p JOIN events e ON e.id=p.event_id
            WHERE e.repo=? AND p.expires_at>? AND (e.source NOT IN ('codex-notify','claude-stop')
            OR e.source='claude-stop' OR (? AND e.source='codex-notify'))
            GROUP BY p.status''', (project, timestamp(), 'codex' in target['tools'])).fetchall()
        counts = dict(rows)
    finally:
        conn.close()
    color = config['appearance']['accent'].lstrip('#')
    rgb = ';'.join(str(int(color[i:i+2], 16)) for i in (0, 2, 4))
    # No provider text, URLs or control characters can enter the terminal.
    return f'\x1b[38;2;{rgb}m[PCO] {project} | {counts.get("pending", 0)} need review | {counts.get("approved", 0)} preview ready\x1b[0m'


def snippet(config_path, project, shell):
    path = str(Path(config_path).resolve())
    project_for(load(path), project, 'claude')
    argv = [sys.executable, '-m', 'proactive_coding_orchestrator.statusline', '--config', path, '--project', project]
    if shell == 'powershell':
        # Claude invokes shell commands through its configured shell. Explicitly launch
        # PowerShell so this also works when the Windows host uses Git Bash.
        script = '& ' + ' '.join("'" + part.replace("'", "''") + "'" for part in argv)
        import base64
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        command = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
    elif shell == 'posix':
        command = shlex.join(argv)
    else:
        raise ValueError('unsupported status-line shell')
    return {'statusLine': {'type': 'command', 'command': command, 'padding': 1}}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--project', required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            return 0
        payload = json.loads(raw)
        if isinstance(payload, dict):
            value = render(args.config, args.project, payload)
            if value:
                print(value)
    except (ValueError, OSError, sqlite3.Error, TypeError):
        pass  # Host footer must remain usable when PCO is unavailable.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
