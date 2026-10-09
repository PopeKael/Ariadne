"""Verification regression tests; --serve runs an isolated browser acceptance fixture."""
import json
import os
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import code_lab
import lab_verification as verification

GOOD='''<!doctype html><html><head><style>body{background:#10232a;color:white;font:20px system-ui}button{padding:12px}</style></head><body><h1>Counter test</h1><p id="count">0</p><button id="add" onclick="count.textContent=Number(count.textContent)+1">Add</button><button id="reset" onclick="count.textContent=0">Reset</button></body></html>'''
BROKEN=GOOD.replace('count.textContent=Number(count.textContent)+1','missingFunction()')
SYNTAX=GOOD.replace('</body>',"<script>let message='You can't go that way.';</script></body>")
NOOP=GOOD.replace('count.textContent=Number(count.textContent)+1','void 0')
def project(source):
    return {'project_name':'Counter test','summary':'Isolated verification acceptance fixture','entrypoint':'index.html','files':[{'path':'index.html','content':source}]}
def check(description,actions,selector='#count',text='1'):
    return {'description':description,'mode':'browser','passed':True,'evidence':'','actions':actions,
            'selector':selector,'text':text,'min_count':1,'max_count':1,'changed':False}
PLAN={'checks':[check('Increment changes the visible count',[{'kind':'click','selector':'#add','value':''}]),
                check('Two more increments preserve script state',[{'kind':'click','selector':'#add','value':''},{'kind':'click','selector':'#add','value':''}],text='3'),
                check('Reset restores zero',[{'kind':'click','selector':'#reset','value':''}],text='0')]}
def scripted_actions(source, observations):
    if not observations:
        return {'action':'write','project':project(source)}
    last=observations[-1]
    if last.get('path')=='index.html' and 'content' not in last:
        return {'action':'run'}
    if last.get('passed') is True:
        # A run has no checks; explicitly test the controls next.
        return {'action':'finish','summary':'Counter controls tested.'} if last.get('checks') else {'action':'test','checks':[{k:v for k,v in c.items() if k not in {'mode','passed','evidence'}} for c in PLAN['checks']]}
    return {'action':'read'}


def lab_for(sources):
    iterator=iter(sources);current=[None]
    registry=SimpleNamespace(route=lambda _:SimpleNamespace(endpoint='fixture',model_id='fixture'))
    def generate(endpoint,model,payload):
        messages=payload['messages'][1:]
        observations=[json.loads(m['content'])['observation'] for m in messages if m['role']=='user' and m['content'].startswith('{"observation":')]
        previous=next((json.loads(m['content'])['action'] for m in reversed(messages) if m['role']=='assistant'),None)
        if not observations:
            current[0]=next(iterator);value={'action':'write','project':project(current[0])}
        elif previous=='read' and observations[-1].get('content') is not None:
            try:new=next(iterator)
            except StopIteration:new=current[0]
            value={'action':'patch','revision':observations[-1]['revision'],'edits':[{'path':'index.html','search':current[0],'replace':new}]};current[0]=new
        elif previous in {'write','patch'} and observations[-1].get('ok'):
            value={'action':'run'}
        elif previous=='run' and observations[-1].get('passed'):
            value={'action':'test','checks':[{k:v for k,v in c.items() if k not in {'mode','passed','evidence'}} for c in PLAN['checks']]}
        elif previous=='test' and observations[-1].get('passed'):
            value={'action':'finish','summary':'Counter tested.'}
        else:
            value={'action':'read'}
        yield {'response':json.dumps({'reason':'Scripted fixture action',**value}),'done':True,'done_reason':'stop'}
    return code_lab.CodeLab(registry,lambda _:{'available':True,'models':[{'name':'fixture'}]},generate,
                            nullcontext,lambda _:nullcontext(),lambda:{},lambda _:None)

class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'ARIADNE_LAB_ROOT':self.temp.name,'ARIADNE_CODING_MODEL':'fixture','ARIADNE_CODING_CONTEXT':'32768'})
        env.start();self.addCleanup(env.stop)

    def test_actual_javascript_root_error_and_no_execution(self):
        result=verification.check_source(SYNTAX)
        self.assertFalse(result['passed']);self.assertIn('SyntaxError',result['errors'][0]);self.assertIn('index.html:1',result['errors'][0])
        self.assertTrue(verification.check_source(GOOD)['passed'])
        self.assertTrue(verification.check_source('<html><script>throw Error("compile only")</script></html>')['passed'])

    def test_repairs_and_full_rechecks_before_ready(self):
        runtime={'passed':True,'errors':[],'checks':[{'description':'increment','passed':True}]}
        with patch.object(verification,'request_browser',return_value=runtime) as browser:
            result,_=lab_for([SYNTAX,GOOD]).run({'prompt':'Counter with Add and Reset'},lambda _:None)
        self.assertTrue(result['run']['verified']);self.assertEqual(result['run']['repair_attempts'],1)
        self.assertEqual(browser.call_count,2)
        directory=Path(result['run']['project_path'])
        self.assertEqual((directory/'attempt-0.html').read_text(),SYNTAX)
        self.assertIn('SyntaxError',result['run']['attempts'][0]['failures'][0])

    def test_unchanged_repair_is_rejected_and_keeps_source(self):
        runtime={'passed':False,'errors':['missingFunction is not defined'],'checks':[]}
        with patch.object(verification,'request_browser',return_value=runtime) as browser:
            result,_=lab_for([BROKEN]*3).run({'prompt':'Counter'},lambda _:None)
        self.assertEqual(browser.call_count,1);self.assertFalse(result['ok']);self.assertEqual(result['run']['state'],'NEEDS ATTENTION')
        self.assertEqual(result['run']['stop_reason'],'NO_PROGRESS');self.assertEqual(result['code'],BROKEN)
        self.assertEqual(code_lab.saved_result(result['run']['run_id'])['code'],BROKEN)

    def test_missing_browser_is_not_ready(self):
        result,_=lab_for([GOOD]*3).run({'prompt':'Counter'})
        self.assertFalse(result['ok']);self.assertIn('connected Workbench',result['message'])

    def test_stale_reports_and_unbounded_plans_rejected(self):
        with self.assertRaises(ValueError):verification.submit_report({'token':'expired','run_id':'a'*32,'report':{}})
        with self.assertRaises(ValueError):verification.validate_checks({'checks':[]},GOOD)
        with self.assertRaises(ValueError):verification.test_configuration('a'*32,'b'*32,-1)

    def test_one_browser_script_receives_all_assertions_in_order(self):
        def emit(event):
            config=verification.test_configuration(event['run_id'],event['token'],0)
            self.assertEqual(config['checks'],PLAN['checks'])
            with self.assertRaises(ValueError):verification.test_configuration(event['run_id'],event['token'],1)
            verification.submit_report({'run_id':event['run_id'],'token':event['token'],
                'report':{'usable':True,'smoke_passed':True,'errors':[],
                          'checks':[{'passed':True} for _ in PLAN['checks']]}})
        self.assertTrue(verification.request_browser('a'*32,PLAN,emit,timeout=.1)['passed'])

    def test_browser_timeout_is_a_failure_and_cleans_nonce(self):
        events=[]
        result=verification.request_browser('a'*32,PLAN,events.append,timeout=.01)
        self.assertFalse(result['passed']);self.assertIn('timed out',result['errors'][0]);self.assertNotIn(events[0]['token'],verification._PENDING)

    def test_preview_errors_do_not_overwrite_verification_state(self):
        with patch.object(verification,'request_browser',return_value={'passed':True,'errors':[],'checks':[]}):
            result,_=lab_for([GOOD]).run({'prompt':'Counter'},lambda _:None)
        saved=code_lab.record_preview({'run_id':result['run']['run_id'],'result':'ERROR','errors':['later failure']})
        self.assertEqual(saved['run']['state'],'NEEDS ATTENTION')
        self.assertFalse(saved['run']['verified'])
        self.assertEqual(saved['run']['preview']['result'],'ERROR')

    def test_css_unclosed_block_is_a_source_error(self):
        result=verification.check_source('<html><style>body { color: red;</style></html>')
        self.assertFalse(result['passed']);self.assertIn('unclosed CSS',result['errors'][0])



def serve():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlparse,parse_qs
    temporary=tempfile.TemporaryDirectory(prefix='ariadne-verification-')
    os.environ.update(ARIADNE_LAB_ROOT=temporary.name,ARIADNE_CODING_MODEL='fixture',ARIADNE_CODING_CONTEXT='32768')
    page='''<!doctype html><html><head><title>Lab verification acceptance</title><link rel="stylesheet" href="/chat.css"><style>body{background:#06131b;color:#cce7e7;font:16px system-ui;padding:24px}#workbench-content{max-width:700px}pre{white-space:pre-wrap}button{margin:6px}</style></head><body><h1>Isolated Lab verification acceptance</h1><button id="lab-toggle">The Lab</button><span id="model-name">fixture</span><span id="model-context">32K</span><p id="ask-status"></p><textarea id="ask-input">repair test</textarea><button id="ask-submit">Build fixture</button><main id="workbench-content"></main><script>function setWorkbenchOpen(){} window.ariadneLabChat={context:()=>({}),record:async()=>({}),save:async()=>({inbox_path:'fixture'})};document.querySelector('#ask-submit').onclick=()=>window.ariadneLab.build(document.querySelector('#ask-input').value,{});</script><script src="/code-lab.js"></script></body></html>'''
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,value,kind='application/json'):
            data=(json.dumps(value) if kind=='application/json' else value).encode();self.send_response(200);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(data)))
            if urlparse(self.path).path=='/api/code-lab/preview':self.send_header('Content-Security-Policy',code_lab.PREVIEW_CSP+'; sandbox allow-scripts')
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path;query=parse_qs(urlparse(self.path).query)
            if path=='/':self.send(page,'text/html')
            elif path in {'/chat.css','/code-lab.js'}:self.send((Path(__file__).parent/path[1:]).read_text(encoding='utf-8'),'text/css' if path.endswith('css') else 'text/javascript')
            elif path=='/api/code-lab/preview':self.send(code_lab.preview_html(query['run_id'][0],query['token'][0],int(query['test'][0]) if 'test' in query else None),'text/html')
            elif path=='/api/code-lab/result':self.send(code_lab.saved_result(query.get('run_id',[None])[0]))
            else:self.send({'installed':True,'model':'fixture','context_tokens':32768})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path=='/api/code-lab/preview':self.send(code_lab.record_preview(body))
            elif self.path=='/api/code-lab/verification':self.send(verification.submit_report(body))
            elif self.path=='/api/code-lab/stream':
                lab=lab_for([GOOD] if body['prompt']=='clean test' else [NOOP,NOOP.replace('void 0','void 1'),NOOP.replace('void 0','void 2')] if body['prompt']=='fail test' else [SYNTAX,BROKEN,GOOD])
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
                for event in code_lab.stream_events(lab,body,.25):self.wfile.write((json.dumps(event)+'\n').encode());self.wfile.flush()
            else:self.send({'ok':True,'model':'fixture','context_tokens':32768,'message':'Fixture ready'})
    print('Fixture: http://127.0.0.1:8799',flush=True)
    try:ThreadingHTTPServer(('127.0.0.1',8799),Handler).serve_forever()
    finally:temporary.cleanup()

if __name__=='__main__':
    import sys
    if '--serve' in sys.argv:serve()
    else:unittest.main()
