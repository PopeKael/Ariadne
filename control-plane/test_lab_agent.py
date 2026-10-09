"""Generic lifecycle, deadlines, patch safety and restart recovery."""
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from lab_agent import LabAgent, RunRecord, RunLimits
from lab_workspace import Workspace
from lab_model import ollama_stream
import code_lab
from test_lab_verification import GOOD, BROKEN, PLAN, project, lab_for
from lab_environment import BrowserEnvironment


class AgentTests(unittest.TestCase):
    def run_actions(self,actions,limits=RunLimits(),runtime=None):
        with tempfile.TemporaryDirectory() as tmp:
            data={'model_requests':[],'attempts':[],'run_id':'a'*32,'output_characters':0}
            r=RunRecord(data,lambda:None,lambda e:None,limits)
            w=Workspace(Path(tmp),data,code_lab.atomic_write,code_lab.validate_project)
            env=BrowserEnvironment(code_lab.SCHEMA,w,r,True)
            transcript=[];iterator=iter(actions)
            def query(messages,*args):
                transcript.append(json.loads(json.dumps(messages)))
                action=next(iterator)
                value=action(env) if callable(action) else action
                return {'reason':'Fixture action based on observation',**value} if isinstance(value,dict) and 'action' in value else value
            model=SimpleNamespace(query=query,capabilities={})
            with patch('lab_verification.request_browser',**({'side_effect':runtime} if runtime is not None else {'return_value':{'passed':True,'errors':[],'checks':[]}})):
                LabAgent(model,env,w,r).run_task('A generic request')
            return data,transcript

    def test_model_selects_actions_and_receives_observations(self):
        data,calls=self.run_actions([{'action':'write','project':project(GOOD)}, {'action':'run'}, {'action':'finish','summary':'Complete'}])
        self.assertEqual(data['stop_reason'],'VERIFIED');self.assertEqual(len(calls),3)
        self.assertEqual(json.loads(calls[1][-1]['content'])['observation']['path'],'index.html')
        self.assertEqual(calls[0][1]['content'],'A generic request')
        self.assertNotIn('task_contract',data)

    def test_invalid_action_is_an_observation_then_recovery(self):
        data,calls=self.run_actions([{'action':'unknown'}, {'action':'write','project':project(GOOD)}, {'action':'run'}, {'action':'finish','summary':'Complete'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertIn('error',json.loads(calls[1][-1]['content'])['observation'])

    def test_step_and_invalid_format_limits(self):
        self.assertEqual(self.run_actions([{'action':'read'}],RunLimits(steps=1))[0]['stop_reason'],'STEP_LIMIT')
        self.assertEqual(self.run_actions([{}]*3)[0]['stop_reason'],'FORMAT_ERROR_LIMIT')

    def test_rewording_rationale_is_not_progress(self):
        data,_=self.run_actions([{'action':'write','project':project(GOOD)},
            *[{'action':'read','reason':'Different explanation '+str(n)} for n in range(3)]])
        self.assertEqual(data['stop_reason'],'NO_PROGRESS')

    def test_finish_requires_current_execution_and_patch_requires_read(self):
        data,calls=self.run_actions([
            {'action':'write','project':project(GOOD)}, {'action':'finish','summary':'Too early'},
            {'action':'patch','revision':'wrong','edits':[]}, {'action':'run'}, {'action':'read'},
            lambda e:{'action':'patch','revision':e.read_revision,'edits':[{'path':'index.html','search':'Counter test','replace':'Updated counter'}]},
            {'action':'finish','summary':'Stale run'}, {'action':'run'}, {'action':'finish','summary':'Complete'}])
        self.assertEqual(data['stop_reason'],'VERIFIED');self.assertEqual(data['repairs'],1)
        observations=[json.loads(m['content'])['observation'] for m in data['trajectory'] if m['content'].startswith('{"observation":')]
        self.assertIn('successfully',observations[1]['error']);self.assertIn('Read',observations[2]['error'])
        self.assertIn('successfully',observations[6]['error'])

    def test_patch_limit_is_enforced(self):
        data,_=self.run_actions([{'action':'write','project':project(GOOD)},{'action':'read'},
            lambda e:{'action':'patch','revision':e.read_revision,'edits':[{'path':'index.html','search':'Counter test','replace':'Updated'}]}],RunLimits(repairs=0))
        self.assertEqual(data['stop_reason'],'REPAIR_LIMIT')

    def test_failed_test_cannot_be_erased_by_running_again(self):
        checks=[{k:v for k,v in c.items() if k not in {'mode','passed','evidence'}} for c in PLAN['checks']]
        passed={'passed':True,'errors':[],'checks':[]}
        failed={'passed':False,'errors':['Expected count 1, observed 0'],'checks':[{'passed':False}]}
        data,_=self.run_actions([{'action':'write','project':project(GOOD)}, {'action':'run'},
            {'action':'test','checks':checks}, {'action':'run'}, {'action':'finish','summary':'Cannot override'},
            {'action':'test','checks':checks}, {'action':'finish','summary':'Complete'}],
            runtime=[passed,failed,passed,passed])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertIn('failed or are stale',data['trajectory'][11]['content'])

    def test_interactive_page_requires_tests_and_observations_include_visible_text(self):
        runtime={'passed':True,'errors':[],'checks':[],
                 'controls':[{'visible':True,'disabled':False}], 'startup':{'initial':{'text':'Counter 0 Add Reset'}}}
        checks=[{k:v for k,v in c.items() if k not in {'mode','passed','evidence'}} for c in PLAN['checks']]
        data,calls=self.run_actions([{'action':'write','project':project(GOOD)},{'action':'run'},
            {'action':'finish','summary':'Not tested'}, {'action':'test','checks':checks},{'action':'finish','summary':'Complete'}],runtime=[runtime,runtime])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertIn('interactive controls',calls[3][-1]['content'])
        self.assertIn('Counter 0 Add Reset',calls[2][-1]['content'])

    def test_external_file_change_refuses_patch(self):
        def external_edit(env):
            (env.workspace.directory/'index.html').write_text('external change',encoding='utf-8')
            return {'action':'patch','revision':env.read_revision,'edits':[{'path':'index.html','search':'Counter test','replace':'Updated'}]}
        data,_=self.run_actions([{'action':'write','project':project(GOOD)},{'action':'read'},external_edit],RunLimits(steps=3))
        self.assertEqual(len(data['attempts']),1)
        self.assertIn('changed since read',data['trajectory'][-1]['content'])

    def test_read_preserves_windows_line_endings_for_exact_edits(self):
        source=GOOD.replace('<body>','<body>\r\n')
        data,calls=self.run_actions([{'action':'write','project':project(source)}, {'action':'read'},
            lambda e:{'action':'patch','revision':e.read_revision,'edits':[{'path':'index.html','search':'<body>\r\n','replace':'<body>\r\n<!-- edited -->'}]},
            {'action':'run'},{'action':'finish','summary':'Complete'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertIn('\\r\\n',calls[2][-1]['content'])

    def test_context_budget_rejects_before_transport(self):
        from lab_model import OllamaModelAdapter
        r=RunRecord({'model_requests':[]},lambda:None,lambda e:None,RunLimits(prompt_bytes=1000))
        called=[]
        m=OllamaModelAdapter('fixture','another-compatible-model',32768,lambda *args:called.append(args),
                             nullcontext,lambda _:nullcontext(),r,code_lab.provider_metrics,lambda *args:None,lambda:{})
        with self.assertRaisesRegex(RuntimeError,'budget'):m.call('create','instructions','x'*2000,{},time.monotonic()+10)
        self.assertEqual(called,[])

    def test_native_chat_requests_are_immutable_and_metrics_are_per_call(self):
        from lab_model import OllamaModelAdapter
        r=RunRecord({'model_requests':[],'output_characters':0},lambda:None,lambda e:None,RunLimits())
        payloads=[]
        def stream(endpoint,model,payload,deadline):
            payloads.append(payload)
            yield {'message':{'content':'{"action":"read"}'},'done':True,'done_reason':'stop',
                   'prompt_eval_count':20,'eval_count':5}
        adapter=OllamaModelAdapter('fixture','swappable-worker',32768,stream,nullcontext,lambda _:nullcontext(),
                                  r,code_lab.provider_metrics,lambda *args:None,lambda:{})
        messages=[{'role':'system','content':'Generic instructions'},{'role':'user','content':'Original task'}]
        r.step('GENERATING','model','Choose action',lambda:adapter.query(messages,{},time.monotonic()+10))
        messages.append({'role':'assistant','content':'Changed afterwards'})
        self.assertEqual(len(r.data['model_requests'][0]['request']['messages']),2)
        self.assertNotIn('prompt',payloads[0]);self.assertEqual(payloads[0]['model'],'swappable-worker')
        self.assertEqual(r.totals()['input_tokens'],20);self.assertEqual(r.totals()['output_tokens'],5)

    def test_wall_clock_limit(self):
        clock=[0]
        r=RunRecord({},lambda:None,lambda e:None,RunLimits(wall_seconds=1),clock=lambda:clock[0])
        clock[0]=2
        with self.assertRaisesRegex(RuntimeError,'wall-time'):r.step('EXECUTE','tool','test',lambda:None)

    def test_safe_atomic_patches_preserve_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            record={'attempts':[]};w=Workspace(Path(tmp),record,code_lab.atomic_write,code_lab.validate_project)
            w.candidate(project(GOOD))
            with self.assertRaises(ValueError):w.patch({'edits':[{'path':'index.html','search':'Counter test','replace':'Changed'}, {'path':'../escape','search':'x','replace':'y'}]})
            self.assertEqual(w.code,GOOD)
            self.assertEqual((Path(tmp)/'index.html').read_text(),GOOD)
            fixed=w.patch({'edits':[{'path':'index.html','search':'Counter test','replace':'Generic example'}]})
            self.assertIn('Generic example',fixed)
            self.assertEqual((Path(tmp)/'attempt-0.html').read_text(),GOOD)
            self.assertTrue((Path(tmp)/'patch-1.json').exists())
            with self.assertRaisesRegex(ValueError,'exactly once'):w.patch({'edits':[{'path':'index.html','search':'button','replace':'p'}]})

    def test_restart_marks_orphan_and_retains_source(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ARIADNE_LAB_ROOT':tmp}):
            directory=Path(tmp)/('a'*32);directory.mkdir()
            (directory/'run.json').write_text(json.dumps({'pending':True,'run_id':'a'*32}))
            (directory/'index.html').write_text(GOOD)
            result=code_lab.saved_result('a'*32)
            self.assertEqual(result['run']['stop_reason'],'CORE_RESTART')
            self.assertFalse(result['pending']);self.assertEqual(result['code'],GOOD)

    def test_active_run_is_not_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ARIADNE_LAB_ROOT':tmp}):
            directory=Path(tmp)/('a'*32);directory.mkdir()
            (directory/'run.json').write_text(json.dumps({'pending':True,'run_id':'a'*32}))
            code_lab._ACTIVE_RUNS.add('a'*32)
            try:self.assertTrue(code_lab.saved_result('a'*32)['pending'])
            finally:code_lab._ACTIVE_RUNS.discard('a'*32)

    def test_actual_transport_absolute_deadline_despite_chunks(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.end_headers()
                try:
                    while True:
                        self.wfile.write(b'{"response":"x"}\n');self.wfile.flush();time.sleep(.03)
                except OSError:pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError,'deadline'):
                list(ollama_stream('http://127.0.0.1:'+str(server.server_port),'different-model',{},started+.2))
            self.assertLess(time.monotonic()-started,1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_runtime_repairs_reach_configured_limit(self):
        variants=[BROKEN,BROKEN.replace('Counter test','Counter one'),BROKEN.replace('Counter test','Counter two')]
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ARIADNE_LAB_ROOT':tmp,'ARIADNE_CODING_MODEL':'fixture'}),patch('lab_verification.request_browser',return_value={'passed':False,'errors':['missingFunction is not defined'],'checks':[]}):
            result,_=lab_for(variants).run({'prompt':'Counter'},lambda _:None)
            self.assertEqual(result['run']['stop_reason'],'REPAIR_LIMIT')
            self.assertEqual(result['run']['repair_attempts'],2)
            self.assertEqual(len(result['run']['attempts']),3)

if __name__=='__main__':unittest.main()
