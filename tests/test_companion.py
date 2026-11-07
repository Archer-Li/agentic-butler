from copy import deepcopy
from datetime import datetime
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from proactive_coding_orchestrator.companion import CompanionServer, snapshot, evaluate_projects
from proactive_coding_orchestrator.core import connect, ingest_event, propose_pending, mute_kind
from proactive_coding_orchestrator.settings import DEFAULTS, validate, load
from proactive_coding_orchestrator.receivers import receive, snippets
from proactive_coding_orchestrator.notifications import deliver_batch, quiet_now, native_notice


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULTS)
        self.config['database'] = str(self.root / 'state.sqlite3')
        self.config['projects'] = [{'name': 'personal', 'workspace': str(self.root), 'tools': ['codex', 'claude']}]
        self.path = self.root / 'pco.json'
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        self.conn = connect(self.config['database'])
        self.server = CompanionServer(self.config)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.conn.close()
        self.temp.cleanup()

    def request(self, method, path, data=None, *, token=True, extra=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json', 'Origin': self.server.origin}
        if token:
            headers['X-PCO-Token'] = self.server.token
        headers.update(extra or {})
        conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
        response = conn.getresponse()
        status, body, mime = response.status, response.read(), response.getheader('Content-Type')
        conn.close()
        return status, json.loads(body) if 'application/json' in mime else body.decode()

    def proposal(self, repo='personal', key='one'):
        ingest_event(self.conn, kind='ci_failed', subject='<script>untrusted fixture</script>', repo=repo, event_key=key)
        return propose_pending(self.conn)[0]

    def action(self, key, action, scope=None):
        return self.request('POST', '/api/action', {'id': key, 'action': action, 'scope_hash': scope})

    def test_live_http_scope_gate_and_one_shot(self):
        from proactive_coding_orchestrator import live_agent
        self.config['agent']['enabled']=True
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        self.server.config_path=str(self.path.resolve())
        with patch.object(live_agent.shutil,'which',return_value=sys.executable):
            code,job=self.request('POST','/api/agent',{'action':'prepare','project':'personal','prompt':'Text fixture'})
        self.assertEqual(code,200)
        payload={'id':job['id'],'action':'run','scope_hash':job['scope_hash']}
        self.assertEqual(self.request('POST','/api/agent',payload)[0],400)
        payload['action']='approve'
        self.assertEqual(self.request('POST','/api/agent',payload,token=False)[0],403)
        self.assertEqual(self.request('POST','/api/agent',payload)[1]['status'],'approved')
        payload['action']='run'
        with patch.object(live_agent,'invoke',return_value=('completed','fixture')) as invoke:
            self.assertEqual(self.request('POST','/api/agent',payload)[1]['status'],'completed')
            self.assertEqual(self.request('POST','/api/agent',payload)[0],400)
            self.assertEqual(invoke.call_count,1)
    def test_native_test_is_opt_in_and_never_approval(self):
        data={'id':None,'action':'test-native','scope_hash':None}
        self.assertEqual(self.request('POST','/api/action',data)[0],400)
        self.config['notifications'].update(enabled=True,backend='native')
        with patch('proactive_coding_orchestrator.notifications.native_notice') as notice:
            self.assertEqual(self.request('POST','/api/action',data)[1]['status'],'submitted')
            self.assertEqual(notice.call_args.args[0],1)
            self.assertTrue(notice.call_args.args[1].startswith(self.server.origin+'/review#notice='))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0],0)
    def memory_setup(self):
        self.config['memory'].update(enabled=True,projects=['personal'],debounce_seconds=0)
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        self.server.config_path=str(self.path.resolve())

    def memory_record(self):
        return self.request('POST','/api/memory',{'action':'record','project':'personal',
            'session':'session-one','delivery_id':'checkpoint-one','final':True,
            'notes':[{'kind':'lesson','text':'Use the documented project test command.',
                'conditions':'Local test runs','verified':True,'verification':'Fixture passed',
                'incident_id':'attempt-one'}]})

    def test_memory_http_review_apply_and_cross_origin_gate(self):
        self.memory_setup()
        self.assertEqual(self.memory_record()[0],200)
        code,draft=self.request('POST','/api/memory',{'action':'dream','project':'personal'})
        self.assertEqual(code,200)
        payload={'action':'apply','project':'personal','id':draft['id'],'scope_hash':draft['scope_hash']}
        self.assertEqual(self.request('POST','/api/memory',payload)[0],400)
        payload['action']='approve'
        self.assertEqual(self.request('POST','/api/memory',payload,token=False)[0],403)
        self.assertEqual(self.request('POST','/api/memory',payload,extra={'Origin':'https://foreign.example'})[0],403)
        self.assertEqual(self.request('POST','/api/memory',payload)[1]['status'],'approved')
        payload['action']='apply'
        self.assertEqual(self.request('POST','/api/memory',payload)[1]['status'],'applied')
        state=self.request('GET','/api/state')[1]
        self.assertEqual(state['memories'][0]['revision'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0],0)
        self.assertIn('renderMemory',self.request('GET','/memory.js')[1])

    def test_memory_opt_out_from_disk_hides_state_and_blocks_apply(self):
        self.memory_setup();self.memory_record()
        _,draft=self.request('POST','/api/memory',{'action':'dream','project':'personal'})
        payload={'action':'approve','project':'personal','id':draft['id'],'scope_hash':draft['scope_hash']}
        self.assertEqual(self.request('POST','/api/memory',payload)[0],200)
        changed=json.loads(self.path.read_text(encoding='utf-8'))
        changed['memory']['enabled']=False
        self.path.write_text(json.dumps(changed),encoding='utf-8')
        payload['action']='apply'
        self.assertEqual(self.request('POST','/api/memory',payload)[0],400)
        self.assertEqual(self.request('GET','/api/state')[1]['memories'],[])

    def test_background_worker_only_prepares_memory_drafts(self):
        import time
        self.memory_setup();self.memory_record()
        worker=self.server.start_worker()
        try:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                rows=self.conn.execute('SELECT status FROM memory_changes').fetchall()
                if rows:break
                time.sleep(.05)
            self.assertTrue(rows)
            self.assertEqual(rows[0]['status'],'pending')
            self.assertEqual(self.conn.execute('SELECT revision FROM memory_projects').fetchone()[0],0)
        finally:
            self.server.stop_event.set();worker.join(timeout=3)

    def test_http_review_approve_revoke_and_preview_flow(self):
        first = self.proposal()
        _, state = self.request('GET', '/api/state')
        self.assertEqual(state['pending'], 1)
        scope = state['proposals'][0]['scope_hash']
        self.assertEqual(self.action(first, 'preview')[1]['status'], 'nothing_approved')
        self.assertEqual(self.action(first, 'approve', 'stale')[0], 400)
        self.assertEqual(self.action(first, 'approve', scope)[1]['status'], 'approved')
        self.assertEqual(self.action(first, 'revoke')[1]['status'], 'revoked')
        self.assertEqual(self.action(first, 'preview')[1]['status'], 'nothing_approved')
        second = self.proposal(key='two')
        _, state = self.request('GET', '/api/state')
        scope = next(p['scope_hash'] for p in state['proposals'] if p['id'] == second)
        self.action(second, 'approve', scope)
        self.assertIn('SIMULATED ONLY', self.action(second, 'preview')[1]['output'])
        self.assertEqual(self.action(second, 'preview')[1]['status'], 'nothing_approved')
        self.assertIn('SIMULATED ONLY', next(p['output'] for p in self.request('GET', '/api/state')[1]['proposals'] if p['id'] == second))

    def test_api_rejects_missing_token_foreign_origin_host_and_extra_fields(self):
        self.assertEqual(self.request('GET', '/api/state', token=False)[0], 403)
        self.assertEqual(self.request('GET', '/api/state', extra={'Origin': 'https://foreign.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', extra={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/action', {'command': 'shell'})[0], 400)
        self.assertEqual(self.request('GET', '/api/action')[0], 404)
        self.assertEqual(self.request('GET', '/../../.env')[0], 404)

    def test_foreign_projects_never_visible_or_actionable(self):
        foreign = self.proposal(repo='not-enabled')
        self.assertEqual(self.request('GET', '/api/state')[1]['proposals'], [])
        self.assertEqual(self.action(foreign, 'approve', 'any')[0], 400)

    def test_state_does_not_claim_a_live_agent_connection(self):
        self.assertEqual(snapshot(self.conn, self.config)['adapters'][0]['observation'], 'no_events')
        page = self.request('GET', '/')[1]
        self.assertIn('native Codex status-bar embedding is not installed', page)
        self.assertIn(self.server.token, page)
        script = self.request('GET', '/app.js')[1]
        self.assertNotIn('innerHTML', script)
        self.assertIn('textContent', script)

    def test_disabled_notifications_do_not_claim_proposals(self):
        self.proposal()
        result = self.action(None, 'notify')[1]
        self.assertEqual(result['status'], 'disabled')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)

    def test_diagnostics_report_configured_live_opt_in_without_launching(self):
        from proactive_coding_orchestrator.worker import doctor
        with patch('subprocess.run', side_effect=AssertionError('diagnostics must never launch a host')):
            self.assertFalse(doctor(self.conn)['live_agent_enabled'])
            self.config['agent']['enabled'] = True
            result = doctor(self.conn, self.config)
            self.assertTrue(result['live_agent_enabled'])
            self.assertTrue(result['live_agent_requires_task_approval'])
            self.assertIn('claude_cli_found', result)
            self.assertEqual(result['execution'], 'preview_only')
            self.assertIn('model_inference', result['checks_not_performed'])

    def test_browser_notifications_are_durable_and_rate_limited(self):
        self.proposal()
        self.config['notifications']['enabled'] = True
        self.assertEqual(self.action(None, 'notify')[1]['count'], 1)
        self.assertEqual(self.action(None, 'notify')[1]['count'], 0)

    def test_native_failure_records_status_without_retry(self):
        self.proposal()
        self.config['notifications'].update(enabled=True, backend='native')
        def failure(count, url):
            raise RuntimeError('secret')
        result = deliver_batch(self.conn, self.config, self.server.origin, backend=failure)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(deliver_batch(self.conn, self.config, self.server.origin, backend=failure)['count'], 0)

    def test_notification_link_get_never_mutates_and_post_never_approves(self):
        from proactive_coding_orchestrator.notifications import review_link
        key=self.proposal()
        link=review_link(self.conn,self.server.origin,'native')
        code,page=self.request('GET','/review')
        self.assertEqual(code,200)
        self.assertIn('notification-result',page)
        self.assertIsNone(self.conn.execute('SELECT opened_at FROM notification_links').fetchone()[0])
        body={'notification_id':link['notification_id']}
        self.assertEqual(self.request('POST','/api/notification-open',body,token=False)[0],403)
        self.assertEqual(self.request('POST','/api/notification-open',body,extra={'Origin':'https://foreign.example'})[0],403)
        self.assertEqual(self.request('POST','/api/notification-open',body)[1]['status'],'review_opened')
        first=self.conn.execute('SELECT opened_at FROM notification_links').fetchone()[0]
        self.assertEqual(self.request('POST','/api/notification-open',body)[1]['status'],'already_opened')
        self.assertEqual(first,self.conn.execute('SELECT opened_at FROM notification_links').fetchone()[0])
        self.assertEqual(self.conn.execute('SELECT status FROM proposals WHERE id=?',(key,)).fetchone()[0],'pending')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0],0)
    def test_forged_expired_and_foreign_notification_links_fail_closed(self):
        from proactive_coding_orchestrator.notifications import review_link,record_open
        link=review_link(self.conn,self.server.origin,'native')
        for value in ('bogus','A'*32,{'bad':'object'}):
            self.assertEqual(self.request('POST','/api/notification-open',{'notification_id':value})[0],400)
        with self.assertRaises(ValueError):record_open(self.conn,'http://127.0.0.1:1',link['notification_id'])
        with self.conn:self.conn.execute("UPDATE notification_links SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.request('POST','/api/notification-open',{'notification_id':link['notification_id']})[0],400)
        self.assertIsNone(self.conn.execute('SELECT opened_at FROM notification_links').fetchone()[0])
    def test_notification_links_cannot_target_external_sites(self):
        from proactive_coding_orchestrator.notifications import review_link
        for origin in ('https://example.com','javascript:alert(1)','http://127.0.0.1:80@evil.example','http://127.0.0.1:80/redirect','http://127.0.0.1:80?next=external'):
            with self.assertRaises(ValueError):review_link(self.conn,origin,'native')
    def test_batch_deep_link_records_only_open_not_display(self):
        self.proposal();self.config['notifications'].update(enabled=True,backend='native')
        with patch('proactive_coding_orchestrator.notifications.native_notice') as notice:
            result=deliver_batch(self.conn,self.config,self.server.origin)
            self.assertEqual(result['status'],'submitted')
            self.assertEqual(notice.call_args.args[1],result['url'])
            self.assertIn('/review#notice=',result['url'])
        row=self.conn.execute('SELECT * FROM notification_links').fetchone()
        self.assertEqual(row['id'],result['notification_id']);self.assertIsNone(row['opened_at'])
        _,state=self.request('GET','/api/state');self.assertIsNone(state['notification_link']['opened_at'])
    def test_notification_diagnostics_cli_never_opens_database(self):
        import contextlib,io
        from proactive_coding_orchestrator.cli import main
        with patch('proactive_coding_orchestrator.cli.connect',side_effect=AssertionError('read-only check cannot open state')):
            with patch('proactive_coding_orchestrator.notifications.native_diagnostics',return_value={'os_setting':'Enabled','display_verified':False}):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(['--db',str(self.root/'absent.sqlite3'),'notification-check']),0)
        self.assertFalse(json.loads(output.getvalue())['display_verified'])
        self.assertFalse((self.root/'absent.sqlite3').exists())
    def test_windows_blocks_are_reported_without_claiming_display(self):
        from proactive_coding_orchestrator.notifications import NotificationBlocked,native_diagnostics
        with patch('sys.platform','win32'),patch('subprocess.run') as run:
            run.return_value.stdout=json.dumps({'status':'blocked','os_setting':'DisabledForApplication'})
            with self.assertRaises(NotificationBlocked):native_notice(1,self.server.origin)
            run.return_value.stdout=json.dumps({'os_setting':'Enabled'})
            result=native_diagnostics()
            self.assertEqual(result['os_setting'],'Enabled')
            self.assertFalse(result['display_verified'])
            self.assertNotIn('.Show(',run.call_args.args[0][-1])
    def test_blocked_native_delivery_is_durable_and_not_retried(self):
        from proactive_coding_orchestrator.notifications import NotificationBlocked
        self.proposal();self.config['notifications'].update(enabled=True,backend='native')
        with patch('proactive_coding_orchestrator.notifications.native_notice',side_effect=NotificationBlocked('DisabledForUser')) as notice:
            result=deliver_batch(self.conn,self.config,self.server.origin)
            self.assertEqual(result['status'],'blocked')
            self.assertFalse(result['display_verified'])
            self.assertEqual(deliver_batch(self.conn,self.config,self.server.origin)['count'],0)
            notice.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT status FROM notifications').fetchone()[0],'blocked')
    def test_test_notification_reports_block_without_success(self):
        from proactive_coding_orchestrator.notifications import NotificationBlocked
        self.config['notifications'].update(enabled=True,backend='native')
        with patch('proactive_coding_orchestrator.notifications.native_notice',side_effect=NotificationBlocked('DisabledByGroupPolicy')):
            code,result=self.action(None,'test-native')
        self.assertEqual(code,200);self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['os_setting'],'DisabledByGroupPolicy')
    def test_invalid_windows_result_and_diagnostics_fail_closed(self):
        from proactive_coding_orchestrator.notifications import native_diagnostics
        with patch('sys.platform','win32'),patch('subprocess.run') as run:
            run.return_value.stdout=json.dumps({'status':'submitted','os_setting':'mystery'})
            with self.assertRaises(ValueError):native_notice(1,self.server.origin)
            self.assertEqual(native_diagnostics()['os_setting'],'unavailable')
    def test_native_windows_and_mac_build_bounded_programs(self):
        with patch('sys.platform', 'win32'), patch('subprocess.run') as run:
            run.return_value.stdout=json.dumps({'status':'submitted','os_setting':'Enabled'})
            native_notice(1, 'http://127.0.0.1:1234')
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
            self.assertNotIn('Bypass', str(run.call_args))
            self.assertIn('<toast', run.call_args.kwargs['input'])
        with patch('sys.platform', 'darwin'), patch('shutil.which', return_value=None), patch('subprocess.run') as run:
            native_notice(1, 'http://127.0.0.1:1234')
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/osascript')

    def test_both_configured_receivers_record_minimized_callbacks(self):
        payloads = [('codex', {'type': 'agent-turn-complete', 'thread-id': 'thread1', 'turn-id': 'turn1'}),
                    ('claude', {'hook_event_name': 'Stop', 'session_id': 'thread2', 'stop_hook_active': False,
                                'last_assistant_message': 'sensitive synthetic answer', 'transcript_path': '/never/read'})]
        for tool, payload in payloads:
            payload['cwd'] = str(self.root)
            for attempt in range(2):
                argv = snippets(str(self.path), 'personal', tool, sys.executable)['argv']
                result = subprocess.run(argv + ([json.dumps(payload)] if tool == 'codex' else []),
                    input=json.dumps(payload) if tool == 'claude' else None, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '')
        rows = [dict(r) for r in self.conn.execute('SELECT * FROM events')]
        self.assertEqual(len(rows), 2)
        self.assertNotIn('sensitive', json.dumps(rows))
        self.assertNotIn('/never/read', json.dumps(rows))
        generated = snippets(str(self.path), 'personal', 'claude', sys.executable)['config']
        self.assertIn('args', generated['hooks']['Stop'][0]['hooks'][0])

    def test_disabled_or_foreign_receiver_never_opens_database(self):
        payload = {'cwd': str(self.root / 'elsewhere'), 'type': 'agent-turn-complete'}
        with patch('proactive_coding_orchestrator.receivers.connect') as connect_mock:
            self.assertIsNone(receive(self.config, 'personal', 'codex', payload))
            connect_mock.assert_not_called()
        self.config['projects'][0]['tools'] = ['codex']
        with self.assertRaises(ValueError):
            receive(self.config, 'personal', 'claude', {'cwd': str(self.root)})

    def test_tool_opt_out_blocks_existing_pending_and_approved_suggestions(self):
        for key in ('pending', 'approved'):
            ingest_event(self.conn, kind='session_idle', subject='Synthetic Claude event',
                         repo='personal', source='claude-stop', event_key=key)
        ids = propose_pending(self.conn)
        state = self.request('GET', '/api/state')[1]
        approved = ids[0]
        scope = next(p['scope_hash'] for p in state['proposals'] if p['id'] == approved)
        self.assertEqual(self.action(approved, 'approve', scope)[0], 200)
        self.config['projects'][0]['tools'] = ['codex']
        self.assertEqual(self.request('GET', '/api/state')[1]['proposals'], [])
        for key in ids:
            self.assertEqual(self.action(key, 'approve', scope)[0], 400)
            self.assertEqual(self.action(key, 'preview')[0], 400)
        self.config['notifications']['enabled'] = True
        self.assertEqual(self.action(None, 'notify')[1]['count'], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM executions').fetchone()[0], 0)

    def test_disabled_tool_does_not_generate_proposals_or_spend_triage_budget(self):
        ingest_event(self.conn, kind='session_idle', subject='Synthetic Claude event',
                     repo='personal', source='claude-stop', event_key='disabled')
        self.config['projects'][0]['tools'] = ['codex']
        self.config['triage'].update(enabled=True, consent=True)
        with patch('proactive_coding_orchestrator.integrations.triage') as triage_mock:
            self.assertEqual(evaluate_projects(self.conn, self.config), [])
            triage_mock.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0], 0)

    def test_muted_events_are_consumed_without_model_calls_or_later_replay(self):
        ingest_event(self.conn, kind='session_idle', subject='Muted fixture', repo='personal',
                     source='codex-notify', event_key='muted')
        mute_kind(self.conn, 'session_idle', mute=True)
        self.config['triage'].update(enabled=True, consent=True)
        with patch('proactive_coding_orchestrator.integrations.triage') as triage_mock:
            self.assertEqual(evaluate_projects(self.conn, self.config), [])
            triage_mock.assert_not_called()
        self.assertIsNotNone(self.conn.execute('SELECT processed_at FROM events').fetchone()[0])
        mute_kind(self.conn, 'session_idle', mute=False)
        self.assertEqual(evaluate_projects(self.conn, self.config), [])


class ConfigTests(unittest.TestCase):
    def test_dangerous_unknown_and_malformed_config_is_rejected(self):
        mutations = [lambda c:c['approval'].update(execution='live'), lambda c:c.update(token='secret'),
                     lambda c:c['triage'].update(enabled=True), lambda c:c['appearance'].update(accent='red;script'),
                     lambda c:c['approval'].update(ttl_minutes=0), lambda c:c['notifications'].update(quiet_hours=['25:00','08:00']),
                     lambda c:c['notifications'].update(enabled='yes')]
        for mutation in mutations:
            config = deepcopy(DEFAULTS)
            mutation(config)
            with self.assertRaises(ValueError):
                validate(config)

    def test_relative_database_uses_configuration_directory(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'pco.json'
            path.write_text(json.dumps(DEFAULTS), encoding='utf-8')
            self.assertEqual(load(path)['database'], str(Path(root).resolve()/'pco.sqlite3'))

    def test_quiet_hours_cross_midnight_and_daytime(self):
        self.assertTrue(quiet_now(['22:00','08:00'], datetime(2026,1,1,23)))
        self.assertTrue(quiet_now(['22:00','08:00'], datetime(2026,1,1,7)))
        self.assertFalse(quiet_now(['22:00','08:00'], datetime(2026,1,1,12)))
        self.assertTrue(quiet_now(['09:00','17:00'], datetime(2026,1,1,12)))
