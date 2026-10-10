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

    def test_empty_workspace_only_offers_initial_creation(self):
        def create(env):
            names={v['properties']['action']['const'] for v in env.action_schema['oneOf']}
            self.assertEqual(names,{'write'})
            return {'action':'write','project':project(GOOD)}
        data,_=self.run_actions([create,{'action':'run'},{'action':'finish','summary':'Done'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')

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
        r=RunRecord({'model_requests':[]},lambda:None,lambda e:None,RunLimits())
        called=[]
        m=OllamaModelAdapter('fixture','another-compatible-model',2048,lambda *args:called.append(args),
                             nullcontext,lambda _:nullcontext(),r,code_lab.provider_metrics,lambda *args:None,lambda:{})
        with self.assertRaisesRegex(RuntimeError,'budget'):m.call('create','instructions','x'*2000,{},time.monotonic()+10)
        self.assertEqual(called,[])

    def test_measured_prefix_prevents_false_context_overflow_and_preserves_request(self):
        from lab_model import OllamaModelAdapter
        r=RunRecord({'model_requests':[],'output_characters':0},lambda:None,lambda e:None,RunLimits())
        payloads=[]
        def stream(endpoint,model,payload,deadline):
            payloads.append(payload)
            yield {'message':{'content':'{"action":"read"}'},'done':True,
                   'prompt_eval_count':5000,'eval_count':5}
        adapter=OllamaModelAdapter('fixture','compatible-worker',32768,stream,nullcontext,lambda _:nullcontext(),
                                  r,code_lab.provider_metrics,lambda *args:None,lambda:{})
        messages=[{'role':'system','content':'Generic instructions'},{'role':'user','content':'x'*23000}]
        r.step('GENERATING','model','Choose action',lambda:adapter.query(messages,{},time.monotonic()+10))
        messages.extend([{'role':'assistant','content':'read'}, {'role':'user','content':'New observation '+ 'y'*3000}])
        r.step('GENERATING','model','Choose action',lambda:adapter.query(messages,{},time.monotonic()+10))
        last=r.data['model_requests'][-1]
        self.assertGreater(last['context_bytes'],24576)
        self.assertLess(last['input_token_bound'],10000)
        self.assertEqual(last['context_budget_basis'],'provider_prefix_plus_utf8_margin')
        self.assertEqual(payloads[-1]['messages'],messages)
        self.assertEqual(len(payloads[0]['messages']),2)
        changed_schema={'type':'object','description':'Updated available actions'}
        r.step('GENERATING','model','Tools changed',lambda:adapter.query(messages,changed_schema,time.monotonic()+10))
        self.assertEqual(payloads[-1]['format'],changed_schema)
        self.assertLess(r.data['model_requests'][-1]['input_token_bound'],10000)
        large='a'*50000
        previous=r.data['model_requests'][-1]
        previous['request']['messages']=[{'role':'system','content':'Generic instructions'},{'role':'user','content':large}]
        previous['input_tokens']=10000
        messages=json.loads(json.dumps(previous['request']['messages']))
        messages.append({'role':'user','content':'small observation'})
        r.step('GENERATING','model','Large measured prefix',lambda:adapter.query(messages,changed_schema,time.monotonic()+10))
        self.assertGreater(r.data['model_requests'][-1]['context_bytes'],48000)
        self.assertLess(r.data['model_requests'][-1]['input_token_bound'],24576)
        messages.append({'role':'user','content':'z'*26000})
        with self.assertRaisesRegex(RuntimeError,'budget'):
            r.step('GENERATING','model','Too large',lambda:adapter.query(messages,{},time.monotonic()+10))
        self.assertEqual(len(payloads),4)

    def test_patch_mismatch_reports_actual_source_and_can_recover(self):
        source=GOOD.replace('</body>','<script>\n// Application logic goes here\n</script></body>')
        wrong={'path':'index.html','search':'<!-- Application logic goes here -->','replace':'wrong'}
        def fix(env):
            return {'action':'patch','revision':env.read_revision,'edits':[
                {'path':'index.html','search':'// Application logic goes here','replace':'const initialized = true;'}]}
        data,calls=self.run_actions([{'action':'write','project':project(source)},{'action':'read'},
            lambda e:{'action':'patch','revision':e.read_revision,'edits':[wrong]},fix,
            {'action':'run'},{'action':'finish','summary':'Fixed'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        error=json.loads(calls[3][-1]['content'])['observation']['error']
        self.assertIn('"matches": 0',error)
        self.assertIn('// Application logic goes here',error)
        self.assertIn('closest_actual_lines',error)
        self.assertEqual(data['repairs'],1)
        self.assertEqual(len(data['attempts']),2)

    def test_patch_is_only_offered_after_current_file_read(self):
        offered=[]
        def capture(env):
            offered.append({v['properties']['action']['const'] for v in env.action_schema['oneOf']})
        def read(env):
            capture(env)
            self.assertNotIn('write',offered[-1])
            return {'action':'read'}
        def edit(env):
            capture(env)
            return {'action':'patch','revision':env.read_revision,'edits':[
                {'path':'index.html','search':'Counter test','replace':'Updated'}]}
        def run(env):
            capture(env);return {'action':'run'}
        data,_=self.run_actions([{'action':'write','project':project(GOOD)},read,edit,run,{'action':'finish','summary':'Done'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertNotIn('patch',offered[0]);self.assertIn('patch',offered[1]);self.assertNotIn('patch',offered[2])

    def test_elapsed_time_does_not_stop_productive_agent_loop(self):
        clock=[0]
        r=RunRecord({'model_requests':[],'attempts':[]},lambda:None,lambda e:None,RunLimits(),clock=lambda:clock[0])
        def query(*args):
            clock[0]+=100000
            return {'reason':'Done','action':'finish','summary':'Done'}
        env=SimpleNamespace(instructions='Generic',action_schema={},execute=lambda *args:{'finished':True})
        LabAgent(SimpleNamespace(query=query),env,SimpleNamespace(code=None),r).run_task('Generic request')
        self.assertEqual(r.data['stop_reason'],'VERIFIED')
        self.assertNotIn('wall_seconds',r.data['limits'])
        self.assertNotIn('call_seconds',r.data['limits'])
        self.assertNotIn('prompt_bytes',r.data['limits'])

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

    def transport_server(self, handler):
        from http.server import ThreadingHTTPServer
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:'+str(server.server_port)

    def test_actual_transport_keeps_valid_output_past_watchdog_interval(self):
        from http.server import BaseHTTPRequestHandler
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.end_headers()
                for _ in range(15):
                    self.wfile.write(b'{"response":"x"}\n');self.wfile.flush();time.sleep(.03)
                self.wfile.write(b'{"done":true}\n');self.wfile.flush()
        endpoint=self.transport_server(Handler)
        started=time.monotonic()
        chunks=list(ollama_stream(endpoint,'different-model',{},.2))
        self.assertGreater(time.monotonic()-started,.4)
        self.assertEqual(sum(bool(c.get('response')) for c in chunks),15)
        self.assertTrue(chunks[-1]['done'])

    def test_actual_transport_watchdog_stops_silent_and_empty_chunk_connections(self):
        from http.server import BaseHTTPRequestHandler
        for empty_chunks in [False,True]:
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    self.rfile.read(int(self.headers['Content-Length']))
                    self.send_response(200);self.end_headers();self.wfile.flush()
                    try:
                        for _ in range(20):
                            if empty_chunks:self.wfile.write(b'{}\n');self.wfile.flush()
                            time.sleep(.03)
                    except OSError:pass
            endpoint=self.transport_server(Handler)
            started=time.monotonic()
            with self.assertRaisesRegex(RuntimeError,'watchdog'):
                list(ollama_stream(endpoint,'different-model',{},.15))
            self.assertLess(time.monotonic()-started,.5)

    def test_runtime_repairs_reach_configured_limit(self):
        variants=[BROKEN,BROKEN.replace('Counter test','Counter one'),BROKEN.replace('Counter test','Counter two')]
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ARIADNE_LAB_ROOT':tmp,'ARIADNE_CODING_MODEL':'fixture'}),patch('lab_verification.request_browser',return_value={'passed':False,'errors':['missingFunction is not defined'],'checks':[]}):
            result,_=lab_for(variants).run({'prompt':'Counter'},lambda _:None)
            self.assertEqual(result['run']['stop_reason'],'REPAIR_LIMIT')
            self.assertEqual(result['run']['repair_attempts'],2)
            self.assertEqual(len(result['run']['attempts']),3)

if __name__=='__main__':unittest.main()
