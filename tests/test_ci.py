from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from proactive_coding_orchestrator import ci, commands, live_agent
from proactive_coding_orchestrator.core import connect, ingest_event, propose_pending
from proactive_coding_orchestrator.settings import DEFAULTS, validate
from proactive_coding_orchestrator.companion import snapshot
from proactive_coding_orchestrator.notifications import deliver_batch
from proactive_coding_orchestrator.cli import main


class CITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.source = self.workspace / 'calc.py'
        self.source.write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        self.config = deepcopy(DEFAULTS)
        self.config['database'] = str(self.root / 'state.sqlite3')
        self.config['projects'] = [{'name': 'personal', 'workspace': str(self.workspace), 'tools': ['codex']}]
        self.config['ci']['enabled'] = True
        self.config['agent']['enabled'] = True
        self.config['commands'] = {'enabled': True, 'allowlist': [{
            'name': 'tests', 'project': 'personal', 'argv': [sys.executable, '-c',
                'from calc import add; assert add(2, 2) == 4; print("CHECK_PASSED")'],
            'timeout_seconds': 5}]}
        self.path = self.root / 'pco.json'
        self.save()
        self.conn = connect(self.config['database'])
        ingest_event(self.conn, kind='ci_failed', subject='Synthetic failure', repo='personal', event_key='ci-one')
        self.proposal = propose_pending(self.conn)[0]
        self.log = 'AssertionError: -0 != 4\nExpected add(2, 2) to equal 4.\n'
        self.case = ci.open_case(self.conn, self.path, self.proposal, self.log, ['calc.py'])

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')

    def prepare(self, stage='diagnose'):
        with patch.object(live_agent.shutil, 'which', return_value=sys.executable):
            return ci.prepare(self.conn, self.path, self.case['id'], stage)

    def model_result(self, stage='diagnose'):
        evidence = [{'source': 'calc.py', 'line': 2}, {'source': 'log', 'line': 1}]
        if stage == 'diagnose':
            return {'summary': 'Addition returns a difference.', 'findings': [{
                'cause': 'The add implementation subtracts b.', 'confidence': 'supported', 'evidence': evidence}],
                'next_steps': ['Change subtraction to addition and rerun the check.']}
        return {'summary': 'Correct addition.', 'changes': [{
            'path': 'calc.py', 'proposal': 'Replace return a - b with return a + b.', 'evidence': evidence}],
            'validation': ['Run the configured tests command.']}

    def complete(self, job, result):
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        with patch.object(live_agent, 'invoke', return_value=('completed', json.dumps(result))):
            self.assertEqual(live_agent.run(self.conn, job['id'])['status'], 'completed')
        return ci.import_result(self.conn, self.path, self.case['id'], job['id'])

    def test_diagnosis_then_separately_approved_plan(self):
        job = self.prepare()
        self.assertIn(self.log.strip().splitlines()[0], job['scope']['prompt'])
        self.assertIn('return a - b', job['scope']['prompt'])
        self.assertIn('selected failure log', job['scope']['data_disclosure'])
        self.assertEqual(job['scope']['allowed_tools'], [])
        with patch.object(live_agent, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                live_agent.run(self.conn, job['id'])
            invoke.assert_not_called()
        view = self.complete(job, self.model_result())
        self.assertEqual(view['state'], 'diagnosed')
        plan = self.prepare('plan')
        self.assertEqual(plan['status'], 'pending')
        self.assertNotEqual(plan['scope_hash'], job['scope_hash'])
        view = self.complete(plan, self.model_result('plan'))
        self.assertEqual(view['state'], 'plan_ready')
        self.assertIn('a - b', self.source.read_text(encoding='utf-8'))

    def test_human_edit_invalidates_approval_and_refreshes_evidence(self):
        job = self.prepare()
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        self.source.write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        self.assertEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'changed')
        self.assertTrue(live_agent.inspect(self.conn, job['id'])['stale'])
        with patch.object(live_agent, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                live_agent.run(self.conn, job['id'])
            invoke.assert_not_called()
        updated = self.prepare()
        self.assertIn('return a + b', updated['scope']['prompt'])
        self.assertNotEqual(updated['scope']['ci_input']['fingerprint'], job['scope']['ci_input']['fingerprint'])

    def test_edit_during_analysis_cancels_and_prevents_import(self):
        job = self.prepare()
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        def invoke(scope, cancelled):
            self.source.write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
            self.assertTrue(cancelled())
            return 'completed', json.dumps(self.model_result())
        with patch.object(live_agent, 'invoke', side_effect=invoke):
            self.assertEqual(live_agent.run(self.conn, job['id'])['status'], 'cancelled')
        with self.assertRaises(ValueError):
            ci.import_result(self.conn, self.path, self.case['id'], job['id'])

    def test_fix_report_reduces_queue_and_verified_check_closes_case(self):
        self.source.write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        view = ci.report_fix(self.conn, self.path, self.case['id'], True)
        self.assertEqual(view['state'], 'needs_verification')
        state = snapshot(self.conn, self.config, config_path=str(self.path.resolve()))
        self.assertEqual(state['pending'], 0)
        self.assertTrue(state['proposals'][0]['collaboration_quiet'])
        self.assertIn(self.proposal, ci.quiet_proposals(self.conn, self.config))
        self.config['notifications']['enabled'] = True
        self.save()
        self.assertEqual(deliver_batch(self.conn, self.config, 'http://127.0.0.1:8765', browser=True)['count'], 0)
        job = ci.prepare_verification(self.conn, self.path, self.case['id'], 'tests')
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], job['scope_hash'])
        commands.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        result = commands.run(self.conn, job['id'], job['scope_hash'])
        self.assertEqual(result['status'], 'completed')
        self.assertIn('CHECK_PASSED', result['output'])
        self.assertEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'verified')
        self.source.write_text('def add(a, b):\n    return a * b\n', encoding='utf-8')
        self.assertNotEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'verified')

    def test_verification_is_bound_to_selected_files_and_failure_is_not_resolution(self):
        job = ci.prepare_verification(self.conn, self.path, self.case['id'], 'tests')
        commands.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        self.assertEqual(commands.run(self.conn, job['id'], job['scope_hash'])['status'], 'failed')
        self.assertNotEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'verified')
        job = ci.prepare_verification(self.conn, self.path, self.case['id'], 'tests')
        commands.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        self.source.write_text('changed after command approval\n', encoding='utf-8')
        self.assertTrue(commands.inspect(self.conn, job['id'])['stale'])
        with patch.object(commands, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                commands.run(self.conn, job['id'], job['scope_hash'])
            invoke.assert_not_called()

    def test_resume_reopens_direct_successful_verification(self):
        self.source.write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        job = ci.prepare_verification(self.conn, self.path, self.case['id'], 'tests')
        commands.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        self.assertEqual(commands.run(self.conn, job['id'], job['scope_hash'])['status'], 'completed')
        verified = ci.inspect(self.conn, self.path, self.case['id'])
        self.assertEqual(verified['state'], 'verified')
        resumed = ci.report_fix(self.conn, self.path, self.case['id'], False)
        self.assertNotEqual(resumed['state'], 'verified')
        self.assertGreater(resumed['generation'], verified['generation'])
        self.assertIsNone(resumed['verification_job'])

    def test_bad_citations_and_unselected_plan_paths_rejected(self):
        for stage in ('diagnose', 'plan'):
            result = self.model_result(stage)
            key = 'findings' if stage == 'diagnose' else 'changes'
            result[key][0]['evidence'][0]['line'] = 999
            with self.assertRaises(ValueError):
                ci.validate_result(json.dumps(result), stage, self.case['material'])
        result = self.model_result('plan')
        result['changes'][0]['path'] = 'unselected.py'
        with self.assertRaises(ValueError):
            ci.validate_result(json.dumps(result), 'plan', self.case['material'])
        job = self.prepare()
        live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        with patch.object(live_agent, 'invoke', return_value=('completed', '{"summary":"invalid"}')):
            live_agent.run(self.conn, job['id'])
        with self.assertRaises(ValueError):
            ci.import_result(self.conn, self.path, self.case['id'], job['id'])
        self.assertIsNone(ci.inspect(self.conn, self.path, self.case['id'])['diagnosis'])

    def test_file_boundaries_missing_binary_and_size_limits(self):
        for paths in (['../pco.json'], [str(self.source)], ['.git/config'], ['calc.py', 'calc.py']):
            with self.assertRaises(ValueError):
                ci.read_files(self.workspace, paths)
        self.source.write_bytes(b'\x00binary')
        with self.assertRaises(ValueError):
            ci.read_files(self.workspace, ['calc.py'])
        self.source.write_text('x' * 4001, encoding='utf-8')
        self.assertEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'evidence_unavailable')
        self.source.unlink()
        self.assertEqual(ci.inspect(self.conn, self.path, self.case['id'])['state'], 'evidence_unavailable')

    def test_config_optout_old_config_and_cross_config_case_boundary(self):
        old = deepcopy(DEFAULTS)
        old.pop('ci')
        self.assertFalse(validate(old)['ci']['enabled'])
        other = self.root / 'other.json'
        other.write_text(json.dumps(self.config), encoding='utf-8')
        with self.assertRaises(ValueError):
            ci.inspect(self.conn, other, self.case['id'])
        self.assertEqual(ci.cases(self.conn, other), [])
        job = self.prepare()
        self.config['ci']['enabled'] = False
        self.save()
        self.assertEqual(ci.cases(self.conn, self.path), [])
        with self.assertRaises(ValueError):
            live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])

    def test_reported_fix_invalidates_pending_analysis_and_can_resume(self):
        job = self.prepare()
        ci.report_fix(self.conn, self.path, self.case['id'], True)
        with self.assertRaises(ValueError):
            live_agent.decide(self.conn, job['id'], 'approve', job['scope_hash'])
        ci.report_fix(self.conn, self.path, self.case['id'], False)
        self.assertTrue(live_agent.inspect(self.conn, job['id'])['stale'])
        self.assertFalse(self.prepare()['stale'])

    def test_plan_invalidated_when_diagnosis_is_replaced(self):
        self.complete(self.prepare(), self.model_result())
        plan = self.prepare('plan')
        changed = self.model_result()
        changed['summary'] = 'A newer diagnosis'
        self.complete(self.prepare(), changed)
        with self.assertRaises(ValueError):
            live_agent.decide(self.conn, plan['id'], 'approve', plan['scope_hash'])

    def test_cli_imports_failed_command_with_provenance(self):
        command = commands.prepare(self.conn, self.path, 'tests')
        commands.decide(self.conn, command['id'], 'approve', command['scope_hash'])
        self.assertEqual(commands.run(self.conn, command['id'], command['scope_hash'])['status'], 'failed')
        ingest_event(self.conn, kind='ci_failed', subject='Second failure', repo='personal', event_key='ci-two')
        proposal = propose_pending(self.conn)[0]
        out = StringIO()
        with redirect_stdout(out):
            code = main(['--config', str(self.path), 'ci-open', '--proposal', proposal,
                         '--file', 'calc.py', '--command-id', command['id']])
        self.assertEqual(code, 0)
        case = json.loads(out.getvalue())
        self.assertEqual(case['source_command'], command['id'])
        self.assertIn('AssertionError', case['log'])
        self.assertEqual(case['state'], 'needs_analysis')
