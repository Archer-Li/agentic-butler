"""Opt-in lifecycle adapters. Host transcripts and arbitrary paths stay unopened."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import sys

from . import memory
from .core import connect, digest
from .receivers import project_for
from .settings import load

EVENTS = ('SessionStart', 'SubagentStart', 'Stop', 'SubagentStop', 'PreCompact', 'SessionEnd')


def receive(config, project_name, tool, payload, *, config_path=None):
    target = project_for(config, project_name, tool)
    if not memory.opted_in(config, project_name):
        return {}
    if not isinstance(payload, dict):
        raise ValueError('memory hook input must be an object')
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError('memory hook needs an absolute cwd')
    if os.path.normcase(os.path.normpath(cwd)) != os.path.normcase(os.path.normpath(target['workspace'])):
        return {}
    event = payload.get('hook_event_name')
    if event not in EVENTS:
        return {}
    session = payload.get('session_id')
    if not isinstance(session, str) or not session or len(session.encode('utf-8')) > 250:
        raise ValueError('memory hook needs a bounded session ID')
    if payload.get('stop_hook_active') is True:
        return {}
    agent = payload.get('agent_id', '')
    if not isinstance(agent, str) or len(agent.encode('utf-8')) > 250:
        raise ValueError('invalid agent ID')
    answer = payload.get('last_assistant_message')
    checkpoint = None
    if event in ('Stop', 'SubagentStop') and isinstance(answer, str):
        try:
            candidate = json.loads(answer)
            if isinstance(candidate, dict) and set(candidate) == {'pco_checkpoint'}:
                checkpoint = candidate['pco_checkpoint']
        except ValueError:
            pass
    identity = {key: payload.get(key) for key in ('session_id', 'agent_id', 'turn_id', 'hook_event_name', 'source', 'reason', 'trigger')}
    identity.update(tool=tool, answer_hash=digest(answer) if isinstance(answer, str) else None)
    delivery = digest(identity)
    conn = connect(config['database'], timeout=0.15)
    try:
        if event in ('SessionStart', 'SubagentStart'):
            hint = memory.CHECKPOINT_HINT
            bounded = deepcopy(config)
            bounded['memory']['bundle_bytes'] -= len(hint.encode('utf-8'))
            bundle = memory.read(conn, bounded, project_name, session=session)
            protocol = ''
            if config_path:
                command = [sys.executable, '-m', 'proactive_coding_orchestrator.cli', '--config',
                           str(Path(config_path).resolve()), 'memory-record', '--project', project_name,
                           '--session', session, '--agent', agent, '--tool', tool, '--delivery-id', 'unique-checkpoint-id']
                example = {'notes': [{'kind': 'lesson', 'text': 'finding', 'conditions': 'applicability',
                    'verified': False, 'verification': '', 'incident_id': 'task-attempt-id'}]}
                detail = ('\nCheckpoint CLI argv: ' + json.dumps(command) + '\nJSON stdin example: ' +
                          json.dumps(example) + '\nReplace placeholder IDs, finding and conditions. Mark verified only with an explicit verification result.\n')
                if len(detail.encode('utf-8')) <= 1500:
                    protocol = detail
            return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': hint + bundle['text'] + protocol}}
        if checkpoint is not None:
            if not isinstance(checkpoint, dict) or set(checkpoint) != {'notes'}:
                raise ValueError('invalid checkpoint envelope')
            memory.checkpoint(conn, config, project_name, tool=tool, session=session, agent=agent,
                              delivery_id=delivery, notes=checkpoint['notes'])
        memory.signal(conn, config, project_name, delivery, event)
        return {}
    finally:
        conn.close()


def snippets(config_path, project_name, tool, python):
    config = load(config_path)
    project_for(config, project_name, tool)
    memory._target(config, project_name)
    argv = [str(Path(python).resolve()), '-m', 'proactive_coding_orchestrator.memory_hooks',
            '--config', str(Path(config_path).resolve()), '--project', project_name, '--tool', tool]
    if tool == 'claude':
        handler = {'type': 'command', 'command': argv[0], 'args': argv[1:], 'timeout': 1, 'onFailure': 'continue'}
    else:
        script = '& ' + ' '.join("'" + value.replace("'", "''") + "'" for value in argv)
        windows = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        handler = {'type': 'command', 'command': shlex.join(argv), 'commandWindows': windows, 'timeout': 1}
    hooks = {}
    for event in EVENTS:
        event_handler = dict(handler)
        if event in ('SessionStart', 'SubagentStart'):
            event_handler['timeout'] = 5
        hooks[event] = [{'hooks': [event_handler]}]
    return {'tool': tool, 'installed': False, 'config': {'hooks': hooks}, 'argv': argv}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--tool', choices=('codex', 'claude'), required=True)
    args = parser.parse_args(argv)
    result = {}
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('memory hook payload exceeds 64 KiB')
        result = receive(load(args.config), args.project, args.tool, json.loads(raw), config_path=args.config)
    except Exception:
        print('PCO memory checkpoint unavailable; inspect local configuration or retry the checkpoint.', file=sys.stderr)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
