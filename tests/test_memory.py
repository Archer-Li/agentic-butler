from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from proactive_coding_orchestrator import memory, memory_hooks, live_agent
from proactive_coding_orchestrator.core import connect, canonical
from proactive_coding_orchestrator.settings import DEFAULTS, validate


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULTS)
        self.config['database'] = str(self.root / 'state.sqlite3')
        self.config['projects'] = [
            {'name': 'personal', 'workspace': str(self.root), 'tools': ['codex', 'claude']},
            {'name': 'other', 'workspace': str(self.root / 'other'), 'tools': ['codex', 'claude']}]
        self.config['memory'].update(enabled=True, projects=['personal', 'other'], debounce_seconds=0)
        self.path = self.root / 'pco.json'
        self.save()
        self.conn = connect(self.config['database'])

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')

    def note(self, text='Use explicit UTF-8 for project text.', verified=True, incident='attempt-one'):
        return {'kind': 'lesson', 'text': text, 'conditions': 'Windows', 'verified': verified,
                'verification': 'Independent fixture passed' if verified else '', 'incident_id': incident}

    def record(self, key='one', session='session', notes=None, agent=''):
        return memory.checkpoint(self.conn, self.config, 'personal', session=session,
            delivery_id=key, notes=notes or [self.note()], agent=agent, final=True)

    def promote(self, draft):
        memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])
        memory.act(self.conn, self.config, 'personal', draft['id'], 'apply', draft['scope_hash'])

    def learned(self):
        self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        self.promote(draft)
        return memory.state(self.conn, self.config, 'personal')['records'][0]

    def test_old_settings_migrate_with_memory_disabled(self):
        old = deepcopy(DEFAULTS)
        old.pop('memory')
        old.pop('agent')
        self.assertFalse(validate(old)['memory']['enabled'])
        self.config['memory']['projects'] = ['foreign']
        with self.assertRaises(ValueError):
            validate(self.config)

    def test_checkpoint_deduplication_and_conflicting_retry(self):
        original = self.record()
        self.assertEqual(self.record(), {'id': original['id'], 'created': False})
        with self.assertRaises(ValueError):
            self.record(notes=[self.note('Different content')])
        self.assertEqual(memory.state(self.conn, self.config, 'personal')['observations'], 1)

    def test_dream_merges_evidence_without_publishing(self):
        self.record('first', session='session-one')
        self.record('second', session='session-two')
        draft = memory.dream(self.conn, self.config, 'personal')
        self.assertEqual(len(draft['scope']['operations']), 1)
        self.assertEqual(len(draft['scope']['after'][0]['evidence']), 2)
        self.assertIn('2 independent', draft['scope']['operations'][0]['reason'])
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 0)
        with self.assertRaises(ValueError):
            memory.act(self.conn, self.config, 'personal', draft['id'], 'apply', draft['scope_hash'])
        self.promote(draft)
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 1)
        self.assertIn('UTF-8', memory.read(self.conn, self.config, 'personal')['text'])

    def test_subagents_sharing_one_incident_do_not_inflate_recurrence(self):
        self.record('first', agent='reviewer-one')
        self.record('second', agent='reviewer-two')
        draft = memory.dream(self.conn, self.config, 'personal')
        self.assertIn('1 independent', draft['scope']['operations'][0]['reason'])

    def test_mixed_checkpoint_learns_only_verified_notes(self):
        self.record(notes=[self.note(), self.note('Unverified lead', verified=False)])
        draft = memory.dream(self.conn, self.config, 'personal')
        self.assertEqual(len(draft['scope']['operations']), 1)
        self.promote(draft)
        state = memory.state(self.conn, self.config, 'personal')
        self.assertEqual(state['observations'], 1)
        self.assertIn('Unverified lead', state['bundle']['text'])
        self.assertEqual(memory.dream(self.conn, self.config, 'personal')['status'], 'no_verified_changes')

    def test_project_isolation_and_opt_out(self):
        self.learned()
        self.assertEqual(memory.read(self.conn, self.config, 'other')['records'], [])
        draft = memory.rollback(self.conn, self.config, 'personal', 0)
        with self.assertRaises(ValueError):
            memory.inspect(self.conn, self.config, 'other', draft['id'])
        self.config['memory']['enabled'] = False
        with self.assertRaises(ValueError):
            memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])

    def test_workspace_rebinding_is_rejected_and_rename_preserves_identity(self):
        original = memory.read(self.conn, self.config, 'personal')
        self.config['projects'][0]['workspace'] = str(self.root / 'different')
        with self.assertRaises(ValueError):
            memory.read(self.conn, self.config, 'personal')
        self.config['projects'][0]['workspace'] = str(self.root)
        self.config['projects'][0]['name'] = 'renamed'
        self.config['memory']['projects'] = ['renamed', 'other']
        renamed = memory.read(self.conn, self.config, 'renamed')
        self.assertEqual(original['project_id'], renamed['project_id'])

    def test_tampered_scope_and_changed_config_cannot_be_approved(self):
        self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        with self.assertRaises(ValueError):
            memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', 'wrong')
        self.config['appearance']['compact'] = False
        with self.assertRaises(ValueError):
            memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])
        self.config['appearance']['compact'] = True
        scope = draft['scope']
        scope['after'][0]['text'] = 'Modified after review'
        with self.conn:
            self.conn.execute('UPDATE memory_changes SET scope=? WHERE id=?', (canonical(scope), draft['id']))
        with self.assertRaises(ValueError):
            memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])

    def test_interrupted_apply_rolls_back_revision_and_evidence(self):
        self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])
        with patch.object(memory, '_consume_notes', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):
                memory.act(self.conn, self.config, 'personal', draft['id'], 'apply', draft['scope_hash'])
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 0)
        self.assertEqual(memory.inspect(self.conn, self.config, 'personal', draft['id'])['status'], 'approved')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM memory_note_usage').fetchone()[0], 0)
        memory.act(self.conn, self.config, 'personal', draft['id'], 'apply', draft['scope_hash'])
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 1)

    def test_lowered_context_budget_keeps_review_ui_recoverable(self):
        operation = {'action': 'add', 'record': {'kind': 'convention', 'text': 'x' * 1000, 'pinned': True}, 'reason': 'User convention'}
        draft = memory.propose(self.conn, self.config, 'personal', [operation], allow_pinned=True)
        self.promote(draft)
        self.config['memory']['bundle_bytes'] = 512
        state = memory.state(self.conn, self.config, 'personal')
        self.assertIn('capacity_error', state['bundle'])
        self.assertEqual(len(state['records']), 1)
        record = state['records'][0]
        replacement = {**record, 'text': 'Short pinned convention'}
        draft = memory.propose(self.conn, self.config, 'personal',
            [{'action': 'revise', 'id': record['id'], 'record': replacement, 'reason': 'Fit the reviewed context budget'}], allow_pinned=True)
        self.promote(draft)
        self.assertIn('Short pinned', memory.read(self.conn, self.config, 'personal')['text'])

    def test_reused_lifecycle_event_can_queue_new_checkpoints(self):
        self.record()
        self.assertTrue(memory.signal(self.conn, self.config, 'personal', 'same-session-exit', 'SessionEnd')['queued'])
        self.assertFalse(memory.signal(self.conn, self.config, 'personal', 'same-session-exit', 'SessionEnd')['queued'])
        self.record('new-note')
        self.assertTrue(memory.signal(self.conn, self.config, 'personal', 'same-session-exit', 'SessionEnd')['queued'])

    def test_concurrent_apply_has_one_winner_and_one_revision(self):
        self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        memory.act(self.conn, self.config, 'personal', draft['id'], 'approve', draft['scope_hash'])
        barrier = threading.Barrier(2)
        def apply():
            conn = connect(self.config['database'])
            try:
                barrier.wait(timeout=5)
                try:
                    return memory.act(conn, self.config, 'personal', draft['id'], 'apply', draft['scope_hash'])['status']
                except ValueError:
                    return 'refused'
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: apply(), range(2)))
        self.assertCountEqual(results, ['applied', 'refused'])
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 1)

    def test_rejection_consumes_selected_notes_and_preserves_unverified_handoff(self):
        self.record(notes=[self.note(), self.note('Still investigating', verified=False)])
        draft = memory.dream(self.conn, self.config, 'personal')
        memory.act(self.conn, self.config, 'personal', draft['id'], 'reject')
        self.assertEqual(memory.dream(self.conn, self.config, 'personal')['status'], 'no_verified_changes')
        self.assertIn('Still investigating', memory.read(self.conn, self.config, 'personal')['text'])

    def test_rollback_creates_reviewed_revision_and_preserves_history(self):
        self.learned()
        rollback = memory.rollback(self.conn, self.config, 'personal', 0)
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 1)
        self.promote(rollback)
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['records'], [])
        restore = memory.rollback(self.conn, self.config, 'personal', 1)
        self.promote(restore)
        state = memory.state(self.conn, self.config, 'personal')
        self.assertEqual(state['revision'], 3)
        self.assertEqual(len(state['records']), 1)
        self.assertEqual(len(state['revisions']), 4)

    def test_pinned_changes_and_startup_capacity_require_review(self):
        operation = {'action': 'add', 'record': {'kind': 'convention', 'text': 'Pinned project convention', 'pinned': True}, 'reason': 'User project convention'}
        with self.assertRaises(ValueError):
            memory.propose(self.conn, self.config, 'personal', [operation])
        draft = memory.propose(self.conn, self.config, 'personal', [operation], allow_pinned=True)
        self.promote(draft)
        record = memory.state(self.conn, self.config, 'personal')['records'][0]
        with self.assertRaises(ValueError):
            memory.propose(self.conn, self.config, 'personal', [{'action': 'retire', 'id': record['id'], 'reason': 'Outdated'}])
        self.config['memory']['bundle_bytes'] = 512
        operation['record']['text'] = 'x' * 1000
        with self.assertRaises(memory.MemoryCapacityError):
            memory.propose(self.conn, self.config, 'personal', [operation], allow_pinned=True)

    def test_context_budget_selects_relevant_guidance(self):
        self.config['memory']['bundle_bytes'] = 512
        operations = [
            {'action': 'add', 'record': {'kind': 'lesson', 'text': term + 'x' * 200}, 'reason': 'A verified lesson'}
            for term in ('Windows ', 'Linux ')]
        draft = memory.propose(self.conn, self.config, 'personal', operations)
        self.promote(draft)
        bundle = memory.read(self.conn, self.config, 'personal', query='Linux')
        self.assertLessEqual(len(bundle['text'].encode('utf-8')), 512)
        self.assertIn('Linux', bundle['text'])
        self.assertTrue(bundle['omitted'])

    def test_new_checkpoints_arriving_during_review_are_not_consumed(self):
        self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        later = self.record('later', notes=[self.note('Another useful finding')])
        self.promote(draft)
        next_draft = memory.tick(self.conn, self.config)[0]
        self.assertEqual(next_draft['scope']['observations'], [later['id']])
        self.assertEqual(next_draft['scope']['base_revision'], 1)

    def test_dismiss_requires_withdrawing_its_active_draft(self):
        checkpoint = self.record()
        draft = memory.dream(self.conn, self.config, 'personal')
        with self.assertRaises(ValueError):
            memory.dismiss(self.conn, self.config, 'personal', checkpoint['id'])
        memory.act(self.conn, self.config, 'personal', draft['id'], 'revoke')
        memory.dismiss(self.conn, self.config, 'personal', checkpoint['id'])
        self.assertEqual(memory.state(self.conn, self.config, 'personal')['observations'], 0)

    def test_live_task_approval_includes_context_and_retirement_blocks_launch(self):
        record = self.learned()
        self.config['agent']['enabled'] = True
        self.save()
        with patch.object(live_agent.shutil, 'which', return_value=sys.executable):
            job = live_agent.prepare(self.conn, self.path, 'personal', 'Explain our text convention.')
        self.assertIn('UTF-8', live_agent.task_text(job['scope']))
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        draft = memory.propose(self.conn, self.config, 'personal', [{'action': 'retire', 'id': record['id'], 'reason': 'Convention superseded'}])
        self.promote(draft)
        with patch.object(live_agent, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                live_agent.run(self.conn, job['id'])
            invoke.assert_not_called()

    def semantic_job(self):
        self.record()
        self.config['agent']['enabled'] = True
        self.save()
        with patch.object(live_agent.shutil, 'which', return_value=sys.executable):
            job = memory.semantic_prepare(self.conn, self.path, 'personal')
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        observation = job['scope']['memory_draft_input']['notes'][0]
        output = {'operations': [{'action': 'add', 'record': {'kind': 'lesson',
            'text': 'Explicit UTF-8 avoids the verified decoding failure.', 'conditions': 'Windows',
            'evidence': [observation['observation_id']]}, 'reason': 'Consolidated supplied verification'}],
            'incorporate': [{'observation_id': observation['observation_id'], 'note_index': observation['note_index']}]}
        with patch.object(live_agent, 'invoke', return_value=('completed', json.dumps(output))):
            self.assertEqual(live_agent.run(self.conn, job['id'])['status'], 'completed')
        return job, output

    def test_approved_semantic_output_still_requires_memory_review(self):
        job, _ = self.semantic_job()
        self.assertEqual(memory.tick(self.conn, self.config), [])
        draft = memory.semantic_import(self.conn, self.path, 'personal', job['id'])
        self.assertEqual(draft['status'], 'pending')
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 0)
        self.promote(draft)
        self.assertEqual(memory.read(self.conn, self.config, 'personal')['revision'], 1)

    def test_semantic_output_cannot_use_unreviewed_records_or_notes(self):
        job, output = self.semantic_job()
        output['incorporate'][0]['note_index'] = 999
        with self.conn:
            self.conn.execute('UPDATE agent_jobs SET output=? WHERE id=?', (json.dumps(output), job['id']))
        with self.assertRaises(ValueError):
            memory.semantic_import(self.conn, self.path, 'personal', job['id'])
        output['incorporate'] = []
        output['operations'] = [{'action': 'retire', 'id': 'unreviewed-record', 'reason': 'Unsupported'}]
        with self.conn:
            self.conn.execute('UPDATE agent_jobs SET output=? WHERE id=?', (json.dumps(output), job['id']))
        with self.assertRaises(ValueError):
            memory.semantic_import(self.conn, self.path, 'personal', job['id'])

    def test_semantic_input_changes_block_import(self):
        job, _ = self.semantic_job()
        deterministic = memory.dream(self.conn, self.config, 'personal')
        self.promote(deterministic)
        with self.assertRaises(ValueError):
            memory.semantic_import(self.conn, self.path, 'personal', job['id'])

    def test_hook_start_and_stop_use_structured_checkpoints_only(self):
        payload = {'cwd': str(self.root), 'session_id': 'host-session', 'hook_event_name': 'SessionStart', 'transcript_path': '/forbidden/transcript'}
        for tool in ('codex', 'claude'):
            with patch('builtins.open', side_effect=AssertionError('hooks cannot open transcripts')):
                result = memory_hooks.receive(self.config, 'personal', tool, payload)
            self.assertIn('additionalContext', result['hookSpecificOutput'])
        payload.update(hook_event_name='SubagentStop', agent_id='subagent', turn_id='turn1',
                       last_assistant_message=json.dumps({'pco_checkpoint': {'notes': [self.note()]}}))
        self.assertEqual(memory_hooks.receive(self.config, 'personal', 'codex', payload), {})
        self.assertEqual(memory.state(self.conn, self.config, 'personal')['observations'], 1)
        payload['stop_hook_active'] = True
        self.assertEqual(memory_hooks.receive(self.config, 'personal', 'codex', payload), {})
        payload['cwd'] = str(self.root / 'employer')
        with patch.object(memory_hooks, 'connect', side_effect=AssertionError('foreign workspace must not open DB')):
            self.assertEqual(memory_hooks.receive(self.config, 'personal', 'codex', payload), {})

    def test_hook_snippets_cover_lifecycle_and_are_not_installed(self):
        for tool in ('codex', 'claude'):
            result = memory_hooks.snippets(self.path, 'personal', tool, sys.executable)
            self.assertFalse(result['installed'])
            self.assertEqual(set(result['config']['hooks']), set(memory_hooks.EVENTS))
            handler = result['config']['hooks']['SessionEnd'][0]['hooks'][0]
            self.assertEqual(handler['timeout'], 1)
            self.assertNotIn('async', handler)

    def test_generated_hook_argv_runs_start_and_end_protocols(self):
        self.learned()
        for tool in ('codex', 'claude'):
            generated = memory_hooks.snippets(self.path, 'personal', tool, sys.executable)
            payload = {'cwd': str(self.root), 'session_id': 'protocol-' + tool,
                       'hook_event_name': 'SessionStart'}
            result = subprocess.run(generated['argv'], input=json.dumps(payload).encode('utf-8'),
                                    capture_output=True, timeout=10, check=True)
            self.assertEqual(result.stderr, b'')
            output = json.loads(result.stdout)
            self.assertIn('UTF-8', output['hookSpecificOutput']['additionalContext'])
            payload.update(hook_event_name='SubagentStop', agent_id='child',
                last_assistant_message=json.dumps({'pco_checkpoint': {'notes': [self.note('Protocol checkpoint')]}}))
            for _ in range(2):
                result = subprocess.run(generated['argv'], input=json.dumps(payload).encode('utf-8'),
                                        capture_output=True, timeout=10, check=True)
                self.assertEqual(result.stderr, b'')
                self.assertEqual(json.loads(result.stdout), {})
        state = memory.state(self.conn, self.config, 'personal')
        self.assertEqual(state['observations'], 2)
        self.assertEqual(len(state['records']), 1)

    def test_checkpoint_limits_and_malformed_input_preserve_state(self):
        with self.assertRaises(ValueError):
            self.record(notes=[self.note('x' * 1501)])
        bad = self.note()
        bad['verified'] = 'yes'
        with self.assertRaises(ValueError):
            self.record(notes=[bad])
        self.config['memory']['max_observations'] = 10
        for index in range(10):
            self.record(str(index), notes=[self.note(verified=False)])
        with self.assertRaises(ValueError):
            self.record('over-capacity')
        self.assertEqual(memory.state(self.conn, self.config, 'personal')['observations'], 10)


if __name__ == '__main__':
    unittest.main()
