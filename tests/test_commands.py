from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from proactive_coding_orchestrator import commands
from proactive_coding_orchestrator.core import canonical, connect, digest
from proactive_coding_orchestrator.settings import DEFAULTS, load, validate
from proactive_coding_orchestrator.cli import main
from contextlib import redirect_stdout
from io import StringIO


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.config = deepcopy(DEFAULTS)
        self.config['database'] = str(self.root / 'state.sqlite3')
        self.config['projects'] = [{'name': 'personal', 'workspace': str(self.workspace), 'tools': ['codex']}]
        self.config['commands'] = {'enabled': True, 'allowlist': [{
            'name': 'tests', 'project': 'personal',
            'argv': [sys.executable, '-c', 'print("TEST_OK")'], 'timeout_seconds': 5}]}
        self.path = self.root / 'pco.json'
        self.save()
        self.conn = connect(self.config['database'])

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')

    def prepare(self):
        return commands.prepare(self.conn, self.path, 'tests')

    def approve(self, job):
        return commands.decide(self.conn, job['id'], 'approve', job['scope_hash'])

    def test_pending_and_wrong_hash_never_invoke(self):
        job = self.prepare()
        with patch.object(commands, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                commands.run(self.conn, job['id'], job['scope_hash'])
            with self.assertRaises(ValueError):
                commands.decide(self.conn, job['id'], 'approve', 'wrong')
            self.approve(job)
            with self.assertRaises(ValueError):
                commands.run(self.conn, job['id'], 'wrong')
            invoke.assert_not_called()

    def test_actual_process_scope_output_and_one_shot(self):
        self.config['commands']['allowlist'][0]['argv'] = [sys.executable, '-c',
            'import os; print(os.getcwd()); print(os.environ.get("BUTLER_TEST_SECRET", "absent"))']
        self.save()
        job = self.prepare()
        self.approve(job)
        with patch.dict('os.environ', {'BUTLER_TEST_SECRET': 'must-not-inherit'}):
            result = commands.run(self.conn, job['id'], job['scope_hash'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(Path(result['output'].splitlines()[0]).samefile(self.workspace))
        self.assertIn('absent', result['output'])
        self.assertNotIn('must-not-inherit', result['output'])
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], job['scope_hash'])
        view = commands.inspect(self.conn, job['id'])
        self.assertEqual([a['status'] for a in view['audit']], ['pending', 'approved', 'running', 'completed'])

    def test_failure_retains_stderr_and_exit_code(self):
        scope = self.prepare()['scope']
        scope['argv'] = [sys.executable, '-c', 'import sys; print("failure", file=sys.stderr); sys.exit(7)']
        status, output, code = commands.invoke(scope, lambda: False)
        self.assertEqual((status, code), ('failed', 7))
        self.assertIn('failure', output)

    def test_runtime_timeout_output_limit_and_cancellation(self):
        scope = self.prepare()['scope']
        scope['argv'] = [sys.executable, '-c', 'import time; time.sleep(10)']
        scope['timeout_seconds'] = 0.1
        self.assertEqual(commands.invoke(scope, lambda: False)[0], 'failed')
        self.assertEqual(commands.invoke(scope, lambda: True)[0], 'cancelled')
        scope['argv'] = [sys.executable, '-c', 'print("x" * 10000)']
        scope['timeout_seconds'] = 5
        scope['output_limit_bytes'] = 64
        status, output, _ = commands.invoke(scope, lambda: False)
        self.assertEqual(status, 'failed')
        self.assertLessEqual(len(output), 100)

    def test_revoke_and_expiry_block_execution(self):
        job = self.prepare()
        self.approve(job)
        commands.decide(self.conn, job['id'], 'revoke')
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], job['scope_hash'])
        job = self.prepare()
        scope = job['scope']
        scope['expires_at'] = '2000-01-01T00:00:00+00:00'
        with self.conn:
            self.conn.execute('UPDATE command_jobs SET scope=?,scope_hash=? WHERE id=?',
                              (canonical(scope), digest(scope), job['id']))
        self.assertEqual(commands.decide(self.conn, job['id'], 'approve', digest(scope))['status'], 'expired')
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], digest(scope))

    def test_config_change_tampering_and_binary_change_fail_closed(self):
        job = self.prepare()
        self.approve(job)
        with patch.object(commands, 'binary_hash', return_value='changed'):
            with self.assertRaises(ValueError):
                commands.run(self.conn, job['id'], job['scope_hash'])
        with self.conn:
            self.conn.execute('UPDATE command_jobs SET scope=? WHERE id=?', ('{}', job['id']))
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], job['scope_hash'])
        job = self.prepare()
        self.approve(job)
        self.config['commands']['enabled'] = False
        self.save()
        with self.assertRaises(ValueError):
            commands.run(self.conn, job['id'], job['scope_hash'])

    def test_cancel_and_opt_out_during_execution(self):
        for disable in (False, True):
            job = self.prepare()
            self.approve(job)
            def invoke(scope, cancelled):
                if disable:
                    self.config['commands']['enabled'] = False
                    self.save()
                else:
                    other = connect(self.config['database'])
                    try:
                        commands.decide(other, job['id'], 'cancel')
                    finally:
                        other.close()
                self.assertTrue(cancelled())
                return 'completed', 'partial output', 0
            with patch.object(commands, 'invoke', side_effect=invoke):
                result = commands.run(self.conn, job['id'], job['scope_hash'])
            self.assertEqual(result['status'], 'cancelled')

    def test_concurrent_run_claims_only_once(self):
        job = self.prepare()
        self.approve(job)
        def run():
            conn = connect(self.config['database'])
            try:
                return commands.run(conn, job['id'], job['scope_hash'])['status']
            except ValueError:
                return 'blocked'
            finally:
                conn.close()
        with patch.object(commands, 'invoke', return_value=('completed', 'fixture', 0)) as invoke:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run(), range(2)))
            self.assertCountEqual(results, ['completed', 'blocked'])
            self.assertEqual(invoke.call_count, 1)

    def test_default_migration_and_validation(self):
        old = deepcopy(DEFAULTS)
        old.pop('commands')
        self.assertFalse(validate(old)['commands']['enabled'])
        for change in (
                {'project': 'unknown'}, {'argv': 'python -m unittest'}, {'argv': []},
                {'timeout_seconds': True}, {'timeout_seconds': 301}):
            config = deepcopy(self.config)
            config['commands']['allowlist'][0].update(change)
            with self.assertRaises(ValueError):
                validate(config)
        self.assertTrue(load(self.path)['commands']['enabled'])
        with self.assertRaises(ValueError):
            commands.prepare(self.conn, self.path, 'not-registered')

    def test_cli_prepare_approve_run_and_failure_exit_status(self):
        def cli(*args):
            out = StringIO()
            with redirect_stdout(out):
                code = main(['--config', str(self.path), *args])
            return code, json.loads(out.getvalue())
        code, job = cli('command-prepare', '--name', 'tests')
        self.assertEqual(code, 0)
        self.assertNotEqual(cli('command-run', job['id'], '--expect-scope', job['scope_hash'])[0], 0)
        self.assertEqual(cli('command-approve', job['id'], '--expect-scope', job['scope_hash'])[0], 0)
        self.assertEqual(cli('command-run', job['id'], '--expect-scope', job['scope_hash'])[1]['status'], 'completed')
        self.config['commands']['allowlist'][0]['argv'] = [sys.executable, '-c', 'raise SystemExit(7)']
        self.save()
        _, job = cli('command-prepare', '--name', 'tests')
        cli('command-approve', job['id'], '--expect-scope', job['scope_hash'])
        code, result = cli('command-run', job['id'], '--expect-scope', job['scope_hash'])
        self.assertEqual((code, result['exit_code']), (1, 7))

    def test_no_shell_launcher_or_shell_argument_interpretation(self):
        (self.root / 'cmd.exe').write_bytes(b'blocked fixture')
        with patch.object(commands.shutil, 'which', return_value=str(self.root / 'cmd.exe')):
            with self.assertRaises(ValueError):
                self.prepare()
        scope = self.prepare()['scope']
        scope['argv'] = [sys.executable, '-c', 'import sys; print(sys.argv[1])', 'hello; echo unsafe']
        self.assertIn('hello; echo unsafe', commands.invoke(scope, lambda: False)[1])
