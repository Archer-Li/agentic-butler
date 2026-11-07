from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from proactive_coding_orchestrator import live_agent as live
from proactive_coding_orchestrator.core import connect, canonical, digest, ingest_event, propose_pending
from proactive_coding_orchestrator.settings import DEFAULTS, validate
from proactive_coding_orchestrator.statusline import render, snippet

class LiveAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=deepcopy(DEFAULTS)
        self.config['database']=str(self.root/'state.sqlite3')
        self.config['agent']['enabled']=True
        self.config['projects']=[{'name':'personal','workspace':str(self.root),'tools':['codex','claude']}]
        self.path=self.root/'pco.json'
        self.save()
        self.conn=connect(self.config['database'])
    def save(self): self.path.write_text(json.dumps(self.config),encoding='utf-8')
    def tearDown(self):
        self.conn.close(); self.temp.cleanup()
    def prepare(self):
        with patch.object(live.shutil,'which',return_value=sys.executable):
            return live.prepare(self.conn,self.path,'personal','Reply TEST_OK')
    def approve(self, job): return live.decide(self.conn,job['id'],'approve',job['scope_hash'])
    def test_parser_accepts_disabled_host_diagnostic_but_rejects_actions(self):
        events=[{'type':'item.completed','item':{'type':'error','message':live.DISABLED_HOST_DIAGNOSTIC}},
                {'type':'item.completed','item':{'type':'agent_message','text':'OK'}}, {'type':'turn.completed'}]
        raw=lambda: '\n'.join(json.dumps(e) for e in events).encode()
        self.assertEqual(live.parse_output(raw()),('completed','OK'))
        events[0]['item']={'type':'command_execution'}
        self.assertEqual(live.parse_output(raw())[0],'failed')
        events[0]['item']={'type':'error','message':'Unrelated error'}
        self.assertEqual(live.parse_output(raw())[0],'failed')
    def test_cancel_running_discards_result_and_prevents_replay(self):
        job=self.prepare();self.approve(job)
        def invoke(scope,cancelled):
            other=connect(self.config['database'])
            try: live.decide(other,job['id'],'cancel')
            finally: other.close()
            self.assertTrue(cancelled());return 'completed','discard'
        with patch.object(live,'invoke',side_effect=invoke):
            self.assertEqual(live.run(self.conn,job['id'])['status'],'cancelled')
        with self.assertRaises(ValueError):live.run(self.conn,job['id'])
    def test_runtime_enforces_output_limit_and_timeout(self):
        scope=self.prepare()['scope'];scope['output_limit_bytes']=64
        with patch.object(live,'command',return_value=[sys.executable,'-c','import sys;sys.stdin.read();print("x"*1000)']):
            self.assertEqual(live.invoke(scope,lambda:False)[0],'failed')
        scope['timeout_seconds']=0.1
        with patch.object(live,'command',return_value=[sys.executable,'-c','import sys,time;sys.stdin.read();time.sleep(10)']):
            self.assertEqual(live.invoke(scope,lambda:False)[0],'failed')
    def test_runtime_cancellation_stops_child(self):
        scope=self.prepare()['scope']
        with patch.object(live,'command',return_value=[sys.executable,'-c','import sys,time;sys.stdin.read();time.sleep(10)']):
            self.assertEqual(live.invoke(scope,lambda:True),('cancelled',''))
    def test_pending_never_invokes(self):
        job=self.prepare()
        with patch.object(live,'invoke') as invoke:
            with self.assertRaises(ValueError): live.run(self.conn,job['id'])
            invoke.assert_not_called()
    def test_scope_match_and_one_shot(self):
        job=self.prepare()
        with self.assertRaises(ValueError): live.decide(self.conn,job['id'],'approve','wrong')
        self.approve(job)
        with patch.object(live,'invoke',return_value=('completed','TEST_OK')) as invoke:
            self.assertEqual(live.run(self.conn,job['id'])['output'],'TEST_OK')
            with self.assertRaises(ValueError): live.run(self.conn,job['id'])
            self.assertEqual(invoke.call_count,1)
        self.assertEqual([a['status'] for a in live.inspect(self.conn,job['id'])['audit']],['pending','approved','running','completed'])
    def test_revocation_blocks_launch(self):
        job=self.prepare();self.approve(job);live.decide(self.conn,job['id'],'revoke')
        with patch.object(live,'invoke') as invoke:
            with self.assertRaises(ValueError): live.run(self.conn,job['id'])
            invoke.assert_not_called()
    def test_modified_scope_blocks_launch(self):
        job=self.prepare();self.approve(job)
        scope=job['scope'];scope['prompt']='Changed after approval'
        with self.conn: self.conn.execute('UPDATE agent_jobs SET scope=? WHERE id=?',(canonical(scope),job['id']))
        with self.assertRaises(ValueError): live.run(self.conn,job['id'])
    def test_config_optout_blocks_launch(self):
        job=self.prepare();self.approve(job);self.config['agent']['enabled']=False;self.save()
        with self.assertRaises(ValueError): live.run(self.conn,job['id'])
    def test_configuration_change_cancels_active_task(self):
        job=self.prepare();self.approve(job)
        def invoke(scope,cancelled):
            self.config['agent']['enabled']=False;self.save()
            self.assertTrue(cancelled());return 'completed','discarded'
        with patch.object(live,'invoke',side_effect=invoke):
            self.assertEqual(live.run(self.conn,job['id']),{'id':job['id'],'status':'cancelled','output':''})
    def test_expiry_and_binary_change_block_launch(self):
        job=self.prepare();self.approve(job)
        with patch.object(live,'binary_hash',return_value='changed'):
            with self.assertRaises(ValueError):live.run(self.conn,job['id'])
        scope=job['scope'];scope['expires_at']='2000-01-01T00:00:00+00:00'
        with self.conn:self.conn.execute('UPDATE agent_jobs SET scope=?,scope_hash=?,approved_hash=? WHERE id=?',(canonical(scope),digest(scope),digest(scope),job['id']))
        with self.assertRaises(ValueError):live.run(self.conn,job['id'])
    def test_disabled_by_default_and_old_config_migration(self):
        old=deepcopy(DEFAULTS);old.pop('agent');self.assertFalse(validate(old)['agent']['enabled'])
        self.config['agent']['enabled']=False;self.save()
        with self.assertRaises(ValueError): self.prepare()
    def test_runtime_command_restricts_capabilities(self):
        command=live.command(self.prepare()['scope'])
        self.assertIn('--ignore-user-config',command);self.assertIn('read-only',command)
        for feature in live.DISABLED_FEATURES:self.assertIn(feature,command)
        self.assertIn('web_search="disabled"',command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
    def test_readonly_statusline_filters_workspace_and_tool(self):
        self.assertEqual(render(self.path,'personal',{'cwd':str(self.root/'elsewhere')}),'')
        ingest_event(self.conn,kind='ci_failed',subject='fixture',repo='personal',event_key='x')
        propose_pending(self.conn)
        line=render(self.path,'personal',{'workspace':{'current_dir':str(self.root)}})
        self.assertIn('1 need review',line);self.assertIn('\x1b[38;2;',line)
        self.config['projects'][0]['tools']=['codex'];self.save()
        with self.assertRaises(ValueError):render(self.path,'personal',{'cwd':str(self.root)})
    def test_statusline_snippets_are_explicit_and_readonly(self):
        self.assertIn('statusline',snippet(self.path,'personal','posix')['statusLine']['command'])
        self.assertIn('-EncodedCommand',snippet(self.path,'personal','powershell')['statusLine']['command'])
        self.assertEqual(live.tasks(self.conn),[])

if __name__=='__main__':unittest.main()
