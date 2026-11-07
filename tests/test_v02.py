import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proactive_coding_orchestrator.core import (
    connect, decide, ingest_event, list_proposals, propose_pending, run_one)
from proactive_coding_orchestrator.integrations import claude_stop, triage, notify
from proactive_coding_orchestrator.cli import main
import test_cli


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'state.sqlite3'
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def event(self, key='one'):
        event_id, _ = ingest_event(self.conn, kind='session_idle', subject='Fixture',
                                  repo='personal', session='session-1', event_key=key)
        return dict(self.conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())

    def proposal(self):
        self.event()
        return propose_pending(self.conn)[0]

    def test_hook_minimizes_and_deduplicates(self):
        payload = {'hook_event_name': 'Stop', 'session_id': 'session-1',
                   'stop_hook_active': False, 'transcript_path': '/never/read',
                   'last_assistant_message': 'secret', 'cwd': '/never/read'}
        event = claude_stop(payload, repo='personal', delivery_id='turn-1')
        self.assertNotIn('secret', json.dumps(event))
        self.assertNotIn('/never/read', json.dumps(event))
        self.assertTrue(ingest_event(self.conn, **event)[1])
        self.assertFalse(ingest_event(self.conn, **event)[1])
        payload['stop_hook_active'] = True
        self.assertIsNone(claude_stop(payload, repo='personal', delivery_id='turn-1'))

    def test_hook_rejects_forged_shape(self):
        for payload in ([], {}, {'hook_event_name': 'PermissionRequest'},
                        {'hook_event_name': 'Stop', 'session_id': 'ok', 'stop_hook_active': 'false'}):
            with self.assertRaises(ValueError):
                claude_stop(payload, repo='personal', delivery_id='one')

    def test_scope_changes_block_before_approval(self):
        p = self.proposal()
        with self.conn:
            self.conn.execute("UPDATE events SET repo='other'")
        with self.assertRaises(ValueError):
            decide(self.conn, p, approve=True)

    def test_scope_changes_block_after_approval(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        with self.conn:
            self.conn.execute("UPDATE events SET session='different'")
        self.assertEqual(run_one(self.conn)['status'], 'failed_integrity')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0], 0)

    def test_scope_ttl_change_blocks_execution(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        with self.conn:
            self.conn.execute("UPDATE proposals SET expires_at='2099-01-01T00:00:00+00:00'")
        self.assertEqual(run_one(self.conn)['status'], 'failed_integrity')

    def test_legacy_proposal_requires_new_approval(self):
        p = self.proposal()
        with self.conn:
            self.conn.execute('UPDATE proposals SET scope_json=NULL, scope_hash=NULL')
        with self.assertRaises(ValueError):
            decide(self.conn, p, approve=True)

    def test_malformed_steps_fail_closed(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        with self.conn:
            self.conn.execute("UPDATE proposals SET steps='invalid json'")
        self.assertEqual(run_one(self.conn)['status'], 'failed_integrity')

    def test_workers_concurrently_create_only_one_proposal(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.event()
        ready = Barrier(2)
        def worker():
            conn = connect(self.db)
            try:
                ready.wait(timeout=5)
                return propose_pending(conn)
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(worker), pool.submit(worker)
            self.assertEqual(len(first.result()) + len(second.result()), 1)

    def test_unapproved_executor_never_called(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        class Forbidden:
            name = 'preview'
            def execute(self, steps):
                raise AssertionError('must never be called')
        with self.assertRaises(ValueError):
            run_one(self.conn, executor=Forbidden())
        self.assertEqual(list_proposals(self.conn)[0]['status'], 'approved')

    def test_conflicting_delivery_rejected(self):
        self.event()
        with self.assertRaises(ValueError):
            ingest_event(self.conn, kind='session_idle', subject='Different', event_key='one')

    def test_triage_consent_no_calls(self):
        event = self.event()
        with patch('proactive_coding_orchestrator.integrations.local_model') as transport:
            with self.assertRaises(ValueError):
                triage(self.conn, event, consent=False, transport=transport)
            transport.assert_not_called()

    def test_triage_privacy_cache_budget_and_approval(self):
        requests = []
        def transport(request):
            requests.append(request)
            return {'done': True, 'message': {'content': '{"suggest":true}'}}
        event = self.event()
        self.assertTrue(triage(self.conn, event, consent=True, max_calls=1, transport=transport))
        self.assertTrue(triage(self.conn, event, consent=True, max_calls=1, transport=transport))
        self.assertFalse(triage(self.conn, self.event('two'), consent=True, max_calls=1, transport=transport))
        self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads(requests[0]['messages'][1]['content']), {'kind': 'session_idle'})
        self.assertNotIn('Fixture', json.dumps(requests))
        self.assertEqual(requests[0]['options']['num_predict'], 32)
        propose_pending(self.conn, eligible={event['id']})
        self.assertIsNone(run_one(self.conn))

    def test_triage_bad_output_and_timeout_fail_closed(self):
        responses = ['{"suggest":true,"command":"run"}', '{"suggest":"yes"}', '[]', 'invalid']
        for i, content in enumerate(responses):
            event = self.event(str(i))
            self.assertFalse(triage(self.conn, event, consent=True,
                transport=lambda r: {'done': True, 'message': {'content': content}}))
        def timeout(r):
            raise TimeoutError('sensitive error')
        event = self.event('timeout')
        self.assertFalse(triage(self.conn, event, consent=True, transport=timeout))
        self.assertNotIn('sensitive', str([tuple(r) for r in self.conn.execute('SELECT * FROM triage_calls')]))

    def test_triage_budget_survives_reopen(self):
        event = self.event()
        def failure(r):
            raise TimeoutError()
        triage(self.conn, event, consent=True, max_calls=1, transport=failure)
        self.conn.close()
        self.conn = connect(self.db)
        with patch('proactive_coding_orchestrator.integrations.local_model') as transport:
            self.assertFalse(triage(self.conn, self.event('two'), consent=True, max_calls=1, transport=transport))
            transport.assert_not_called()

    def test_notification_consent_and_at_most_once(self):
        self.proposal()
        calls = []
        with self.assertRaises(ValueError):
            notify(self.conn, consent=False, backend=calls.append)
        self.assertEqual(notify(self.conn, consent=True, backend=calls.append)['count'], 1)
        self.assertEqual(notify(self.conn, consent=True, backend=calls.append)['count'], 0)
        self.assertEqual(calls, [1])
        self.assertIsNone(run_one(self.conn))

    def test_notification_failure_is_recorded_not_retried(self):
        self.proposal()
        def failure(n):
            raise RuntimeError('secret')
        self.assertEqual(notify(self.conn, consent=True, backend=failure)['status'], 'failed')
        self.assertEqual(notify(self.conn, consent=True, backend=failure)['count'], 0)


class NewCliTests(unittest.TestCase):
    setUp = test_cli.CliTests.setUp
    tearDown = test_cli.CliTests.tearDown
    call = test_cli.CliTests.call

    def test_hook_to_scope_approval_preview(self):
        code, event = self.call('hook-claude-stop', '--repo', 'personal', '--delivery-id', 'turn-1',
            stdin=json.dumps({'hook_event_name': 'Stop', 'session_id': 's1', 'stop_hook_active': False}))
        self.assertEqual(code, 0)
        self.call('evaluate')
        _, rows = self.call('list')
        self.assertEqual(rows[0]['scope']['repo'], 'personal')
        self.assertEqual(rows[0]['scope']['allowed_tools'], [])
        self.assertEqual(self.call('run')[1]['status'], 'nothing_approved')
        self.call('approve', rows[0]['id'])
        self.assertIn('SIMULATED ONLY', self.call('run')[1]['output'])

    def test_opt_ins_required(self):
        self.assertEqual(self.call('evaluate', '--llm-triage')[0], 2)
        self.assertEqual(self.call('notify')[0], 2)
        self.assertEqual(self.call('notify', '--consent')[0], 0)

    def test_hook_size_limit(self):
        code, result = self.call('hook-claude-stop', '--repo', 'personal', '--delivery-id', 'one', stdin='x' * 65537)
        self.assertEqual(code, 1)
        self.assertIn('64 KiB', result['error'])

    def test_hook_quiet_emits_no_agent_instructions(self):
        from io import StringIO
        import contextlib
        out = StringIO()
        payload = json.dumps({'hook_event_name': 'Stop', 'session_id': 's1', 'stop_hook_active': False})
        with patch('sys.stdin', StringIO(payload)), contextlib.redirect_stdout(out):
            code = main(['--db', self.db, 'hook-claude-stop', '--quiet', '--repo', 'personal', '--delivery-id', 'turn-1'])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), '')
