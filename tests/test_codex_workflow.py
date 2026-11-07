import contextlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from proactive_coding_orchestrator import codex_hook
from proactive_coding_orchestrator.cli import main
from proactive_coding_orchestrator.core import (
    connect, decide, ingest_event, inspect_proposal, propose_pending, revoke, run_one)
from proactive_coding_orchestrator.worker import evaluate_once


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'personal state.sqlite3'
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def event(self, key='one'):
        return ingest_event(self.conn, kind='session_idle', subject='Synthetic event',
                            repo='personal', session='session-1', event_key=key)

    def call(self, *args):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--db', str(self.db), *args])
        return code, json.loads(output.getvalue())

    def payload(self, **extra):
        return {'type': 'agent-turn-complete', 'thread-id': 'thread-1', 'turn-id': 'turn-1',
                'cwd': str(self.root), 'input-messages': ['private prompt fixture'],
                'last-assistant-message': 'private response fixture', **extra}

    def test_generated_callback_runs_as_a_real_subprocess_and_deduplicates(self):
        argv = codex_hook.command(python=sys.executable, db=str(self.db), repo='personal', workspace=str(self.root))
        for _ in range(2):
            result = subprocess.run([*argv, json.dumps(self.payload())], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        rows = [dict(r) for r in self.conn.execute('SELECT * FROM events')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['session'], 'thread-1')
        self.assertNotIn('private prompt', json.dumps(rows))
        self.assertNotIn('private response', json.dumps(rows))
        self.assertNotIn(str(self.root), json.dumps(rows))

    def test_other_workspaces_and_event_types_are_ignored(self):
        for payload in (self.payload(cwd=str(self.root / 'unrelated')),
                        self.payload(type='approval-requested')):
            with patch('sys.stderr', StringIO()):
                self.assertEqual(codex_hook.main(['--db', str(self.db), '--repo', 'personal',
                    '--workspace', str(self.root), json.dumps(payload)]), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_hook_bad_payload_does_not_leak_content_or_persist(self):
        for payload in ('secret bad JSON', json.dumps(self.payload(**{'turn-id': None})),
                        json.dumps(self.payload(cwd='relative/path')), 'x' * 65537):
            stderr, stdout = StringIO(), StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                code = codex_hook.main(['--db', str(self.db), '--repo', 'personal',
                    '--workspace', str(self.root), payload])
            self.assertEqual(code, 1)
            self.assertNotIn('secret', stderr.getvalue())
            self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_config_is_parseable_toml_and_never_overwrites(self):
        output = self.root / 'notify.toml'
        code, result = self.call('codex-config', '--repo', 'personal-工作-🚀', '--workspace', str(self.root), '--output', str(output))
        self.assertEqual(code, 0)
        self.assertFalse(result['installed'])
        try:
            import tomllib
        except ImportError:
            # JSON string arrays are a TOML-compatible subset on Python 3.10.
            parsed = {'notify': json.loads(output.read_text(encoding='utf-8').removeprefix('notify = '))}
        else:
            parsed = tomllib.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(parsed['notify'], result['command'])
        before = output.read_bytes()
        self.assertEqual(self.call('codex-config', '--repo', 'personal', '--workspace', str(self.root), '--output', str(output))[0], 2)
        self.assertEqual(output.read_bytes(), before)

    def test_unicode_stdin_is_limited_by_bytes_not_characters(self):
        output = StringIO()
        payload = json.dumps({'kind': 'session_idle', 'subject': '界' * 23000}, ensure_ascii=False)
        with patch('sys.stdin', StringIO(payload)), contextlib.redirect_stdout(output):
            code = main(['--db', str(self.db), 'emit-json'])
        self.assertEqual(code, 2)
        self.assertIn('64 KiB', output.getvalue())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_review_hash_must_match_before_approval(self):
        self.event()
        proposal = propose_pending(self.conn)[0]
        with self.assertRaises(ValueError):
            decide(self.conn, proposal, approve=True, expected_scope='stale-review')
        view = inspect_proposal(self.conn, proposal)
        self.assertEqual(view['status'], 'pending')
        self.assertFalse(view['can_execute'])
        decide(self.conn, proposal, approve=True, expected_scope=view['scope_hash'])
        self.assertTrue(inspect_proposal(self.conn, proposal)['can_execute'])

    def test_revoke_prevents_execution_and_preserves_audit_order(self):
        self.event()
        proposal = propose_pending(self.conn)[0]
        decide(self.conn, proposal, approve=True)
        self.assertEqual(revoke(self.conn, proposal), 'revoked')
        self.assertIsNone(run_one(self.conn, proposal_id=proposal))
        view = inspect_proposal(self.conn, proposal)
        self.assertFalse(view['can_execute'])
        self.assertIsNone(view['approved_hash'])
        self.assertEqual([r['transition'] for r in view['audit']], ['proposed', 'approved', 'revoked'])
        with self.assertRaises(ValueError):
            decide(self.conn, proposal, approve=True)

    def test_targeted_run_does_not_run_another_approved_proposal(self):
        self.event('one')
        self.event('two')
        first, second = propose_pending(self.conn)
        decide(self.conn, first, approve=True)
        decide(self.conn, second, approve=True)
        self.assertIsNone(run_one(self.conn, proposal_id='missing'))
        self.assertEqual(run_one(self.conn, proposal_id=second)['proposal_id'], second)
        self.assertEqual(inspect_proposal(self.conn, first)['status'], 'approved')
        self.assertEqual([r['transition'] for r in inspect_proposal(self.conn, second)['audit']],
                         ['proposed', 'approved', 'running', 'completed'])

    def test_rejection_and_revocation_work_for_legacy_or_corrupt_proposals(self):
        self.event('one')
        self.event('two')
        first, second = propose_pending(self.conn)
        with self.conn:
            self.conn.execute('UPDATE proposals SET scope_json=NULL, scope_hash=NULL')
        self.assertEqual(decide(self.conn, first, approve=False), 'rejected')
        self.assertEqual(revoke(self.conn, second), 'revoked')
        self.assertIsNone(run_one(self.conn))

    def test_worker_refuses_missing_consent_before_generating_proposals(self):
        self.event()
        for options in ({'llm': True}, {'notifications': True}, {'desktop': True}, {'ttl_minutes': 121}):
            with self.assertRaises(ValueError):
                evaluate_once(self.conn, **options)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0], 0)

    def test_worker_never_runs_approved_work_or_recovers_other_processes(self):
        self.event()
        proposal = propose_pending(self.conn)[0]
        decide(self.conn, proposal, approve=True)
        code, result = self.call('serve', '--once', '--notify', '--notification-consent')
        self.assertEqual(code, 0)
        self.assertEqual(result['notification']['count'], 0)
        self.assertEqual(inspect_proposal(self.conn, proposal)['status'], 'approved')
        with self.conn:
            self.conn.execute("UPDATE proposals SET status='running'")
        self.call('serve', '--once')
        self.assertEqual(inspect_proposal(self.conn, proposal)['status'], 'running')

    def test_foreground_worker_picks_up_events_arriving_after_startup(self):
        from threading import Event, Thread
        proc = subprocess.Popen([sys.executable, '-m', 'proactive_coding_orchestrator.cli',
            '--db', str(self.db), 'serve', '--interval', '0.2', '--notify', '--notification-consent'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            started = Event()
            header = []
            def read_header():
                header.append(proc.stdout.readline())
                started.set()
            Thread(target=read_header, daemon=True).start()
            self.assertTrue(started.wait(timeout=8), 'worker did not start')
            self.assertIn('Foreground proposal worker', header[0])
            reported = Event()
            reports = []
            def read_report():
                reports.append(proc.stdout.readline())
                reported.set()
            Thread(target=read_report, daemon=True).start()
            self.event()
            self.assertTrue(reported.wait(timeout=8), 'worker did not report the incoming event')
            report = json.loads(reports[0])
            self.assertEqual(len(report['proposals_created']), 1)
            self.assertEqual(report['notification']['count'], 1)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0], 1)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0], 0)
        finally:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(stderr, '')

    def test_full_callback_review_revoke_and_preview_flow(self):
        event = codex_hook.normalize(self.payload(), repo='personal', workspace=str(self.root))
        ingest_event(self.conn, **event)
        _, batch = self.call('serve', '--once', '--notify', '--notification-consent')
        proposal = batch['proposals_created'][0]
        _, view = self.call('show', proposal)
        self.assertTrue(view['integrity_valid'])
        self.assertEqual(self.call('run', '--id', proposal)[1]['status'], 'nothing_approved')
        self.assertEqual(self.call('approve', proposal, '--expect-scope', view['scope_hash'])[1]['status'], 'approved')
        self.assertEqual(self.call('revoke', proposal)[1]['status'], 'revoked')
        self.assertEqual(self.call('run', '--id', proposal)[1]['status'], 'nothing_approved')
        ingest_event(self.conn, **codex_hook.normalize(self.payload(**{'turn-id': 'turn-2'}), repo='personal', workspace=str(self.root)))
        second = self.call('serve', '--once')[1]['proposals_created'][0]
        view = self.call('show', second)[1]
        self.call('approve', second, '--expect-scope', view['scope_hash'])
        self.assertIn('SIMULATED ONLY', self.call('run', '--id', second)[1]['output'])
        self.assertEqual(self.call('doctor')[1]['execution'], 'preview_only')
