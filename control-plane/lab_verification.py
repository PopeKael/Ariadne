"""Candidate checks. Untrusted JavaScript is compiled, never executed by Node."""
import json
import re
import shutil
import subprocess
import threading
import uuid
from html.parser import HTMLParser
from pathlib import Path

_PENDING = {}
_LOCK = threading.Lock()


class SourceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.styles = []
        self.active = None
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type', '').lower() not in {'application/json', 'application/ld+json'}:
            if attrs.get('type') == 'module':
                self.errors.append('index.html:%s: module scripts are unsupported in the offline V1 sandbox' % self.getpos()[0])
            self.active = {'code': '', 'line': self.getpos()[0]}
            self.scripts.append(self.active)
        if tag == 'style':
            self.active = {'code':'','line':self.getpos()[0]}
            self.styles.append(self.active)
        for key, value in attrs.items():
            if key.startswith('on') and value:
                self.scripts.append({'code': 'function handler(event){' + value + '\n}', 'line': self.getpos()[0]})
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in {'script','style'}:
            self.active = None
        if tag in self.stack:
            # HTML permits omitted closing tags. A browser remains the parser of record.
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
        else:
            self.errors.append('index.html:%s: unmatched closing </%s>' % (self.getpos()[0], tag))

    def handle_data(self, data):
        if self.active is not None:
            self.active['code'] += data


def check_source(source):
    parser = SourceParser()
    parser.feed(source)
    errors = list(parser.errors)
    for style in parser.styles:
        css=style['code']; stack=[]; quote=None; comment=False; escaped=False; line=style['line']; i=0
        while i<len(css):
            char=css[i]; pair=css[i:i+2]
            if char=='\n':line+=1
            if comment:
                if pair=='*/':comment=False;i+=1
            elif quote:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char==quote:quote=None
            elif pair=='/*':comment=True;i+=1
            elif char in '\"\'':quote=char
            elif char in '{([':stack.append((char,line))
            elif char in '})]':
                if not stack or stack[-1][0]!='{(['['})]'.index(char)]:
                    errors.append(f'index.html:{line}: unmatched CSS {char}');break
                stack.pop()
            i+=1
        if stack or quote or comment:
            errors.append(f'index.html:{stack[-1][1] if stack else line}: unclosed CSS block, string or comment')
    node = shutil.which('node')
    if not node:
        errors.append('JavaScript compiler unavailable: Node.js is required for source verification.')
    else:
        checker = """const vm=require('node:vm');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{let errors=[];for(const x of JSON.parse(s)){try{new vm.Script(x.code,{filename:'index.html',lineOffset:x.line-1})}catch(e){errors.push(String(e.stack).split('\\n').slice(0,5).join('\\n'))}}process.stdout.write(JSON.stringify(errors))});"""
        try:
            result = subprocess.run([node, '-e', checker], input=json.dumps(parser.scripts), text=True,
                                    capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                errors.append('JavaScript compiler failed: ' + result.stderr[:1000])
            else:
                errors.extend(json.loads(result.stdout))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append('JavaScript compiler failed: ' + str(exc))
    return {'passed': not errors, 'errors': errors}


ACTION = {'type':'object','properties': {'kind':{'type':'string','enum':['click','fill','key']},
          'selector':{'type':'string'},'value':{'type':'string'}},'required':['kind','selector','value'],'additionalProperties':False}
CHECK = {'type':'object','properties': {
    'description':{'type':'string'}, 'mode':{'type':'string','enum':['source','browser']},
    'passed':{'type':'boolean'}, 'evidence':{'type':'string'},
    'actions':{'type':'array','items':ACTION,'maxItems':40},
    'selector':{'type':'string'},'text':{'type':'string'},'min_count':{'type':'integer','minimum':0,'maximum':1000},
    'max_count':{'type':'integer','minimum':0,'maximum':1000},'changed':{'type':'boolean'}},
    'required':['description','mode','passed','evidence','actions','selector','text','min_count','max_count','changed'], 'additionalProperties':False}
def validate_checks(plan, source):
    checks = plan.get('checks')
    if not isinstance(checks, list) or not 1 <= len(checks) <= 30:
        raise ValueError('test requires 1–30 bounded checks.')
    for check in checks:
        if set(check) != set(CHECK['required']) or check['mode'] not in {'source','browser'}:
            raise ValueError('Invalid acceptance check.')
        if not isinstance(check['actions'], list) or len(check['actions']) > 40:
            raise ValueError('Invalid acceptance actions.')
        for action in check['actions']:
            if set(action) != set(ACTION['required']) or action['kind'] not in {'click','fill','key'} or any(not isinstance(action[k], str) or len(action[k]) > 1000 for k in ('selector','value')):
                raise ValueError('Invalid acceptance action.')
        if any(not isinstance(check[k], str) or len(check[k]) > 4000 for k in ('description','selector','text','evidence')) or not isinstance(check['changed'], bool):
            raise ValueError('Invalid acceptance expectation.')
        if any(type(check[k]) is not int or not 0 <= check[k] <= 1000 for k in ('min_count','max_count')) or check['min_count'] > check['max_count']:
            raise ValueError('Invalid acceptance count.')
        if check['mode'] == 'source':
            check['passed'] = check['passed'] is True and bool(check['evidence']) and check['evidence'] in source
        elif not check['selector']:
            raise ValueError('Browser acceptance check requires a selector.')
    return plan


def request_browser(run_id, plan, emit, timeout=180):
    nonce = uuid.uuid4().hex
    job = {'run_id':run_id,'plan':plan,'event':threading.Event(),'report':None}
    with _LOCK:
        _PENDING[nonce] = job
    try:
        emit({'type':'test_candidate','run_id':run_id,'token':nonce,'plan':plan})
        if not job['event'].wait(timeout):
            return {'passed':False,'infrastructure_failure':True,'errors':['Browser verification timed out. Keep the Workbench open and connected.'],'checks':[]}
        return job['report']
    finally:
        with _LOCK:
            _PENDING.pop(nonce, None)


def test_configuration(run_id, token, index):
    with _LOCK:
        job = _PENDING.get(token)
        if not job or job['run_id'] != run_id:
            raise ValueError('Candidate verification token expired.')
        checks = [c for c in job['plan']['checks'] if c['mode'] == 'browser']
        if index not in {-1,0} or index==0 and not checks:
            raise ValueError('Invalid browser check index.')
        return {'index':index,'checks':checks if index==0 else []}


def submit_report(body):
    with _LOCK:
        job = _PENDING.get(body.get('token'))
        if not job or body.get('run_id') != job['run_id'] or job['event'].is_set():
            raise ValueError('Verification report is stale or unknown.')
        report = body.get('report')
        if not isinstance(report, dict) or len(json.dumps(report)) > 500000:
            raise ValueError('Invalid browser evidence.')
        expected = [c for c in job['plan']['checks'] if c['mode'] == 'browser']
        checks = report.get('checks', [])
        if not isinstance(checks, list) or len(checks) != len(expected):
            raise ValueError('Incomplete browser checklist.')
        if any(not isinstance(check,dict) or not isinstance(check.get('passed'),bool) for check in checks):
            raise ValueError('Invalid browser check evidence.')
        errors = report.get('errors', [])
        if not isinstance(errors, list) or len(errors) > 100 or any(not isinstance(e,str) for e in errors):
            raise ValueError('Invalid browser errors.')
        report['passed'] = report.get('usable') is True and report.get('smoke_passed') is True and not errors and all(c.get('passed') is True for c in checks)
        job['report'] = report
        job['event'].set()
    return {'ok':True}


def bootstrap(run_id, token, index):
    config = test_configuration(run_id, token, index)
    script = (Path(__file__).parent / 'lab-verification.js').read_text(encoding='utf-8')
    return '<script>window.__labTest=' + json.dumps({'token':token,'source_offset':0,**config}).replace('<','\\u003c') + ';' + script + '</script>'
