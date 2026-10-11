"""Small safe browser environment; the model chooses all workflow actions."""
import hashlib
import time
import lab_verification
from lab_agent import AgentStop
from lab_workspace import source_window


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


EDIT = obj({'path':{'const':'index.html'},'search':{'type':'string'},'replace':{'type':'string'}})
TEST = obj({k:v for k,v in lab_verification.CHECK['properties'].items() if k not in {'mode','passed','evidence'}})
INSTRUCTIONS = '''You are a coding agent building a complete runnable browser application from the user's request.
Use the original request throughout. Identify its functional and presentation requirements, implement them, run the application, inspect actual results, test requested interactions and success/failure states, and repair defects before finishing. Do not return a scaffold or invented test results.
Choose ONE action per response, as JSON matching the action schema. Begin with reason: a brief action rationale grounded in the latest observation, then action and its arguments. Tool results arrive as observations in this conversation. Decide what those facts mean before choosing the next operation. There are no separate planners or reviewers.
Environment: one owned offline index.html containing all HTML, CSS and JavaScript. No dependencies, network resources, imports, external fonts/images, shell commands, filesystem access or executable test scripts. Inline classic JavaScript, CSS and data images are supported in the sandbox.
Actions:
read: inspect current index.html and its revision. Use start_line/end_line for relevant ranges (1-based), search for literal text (empty for a range), and offset for continuing a long/minified range. Reads return at most 6000 source characters; search returns up to three nearby snippets extending into the matching code. A search snippet may be truncated: inspect the relevant complete function/body with ranges or next_offset before changing its behaviour. A function header alone does not establish what its body does. Read the relevant current source before patching. Source is external workspace state, not conversation memory. Prior code and browser receipts are compacted; use workspace revision, concise history including actual edit diffs, and latest diagnostics, and read again when needed.
write: create the initial project once, using project_name, summary, entrypoint index.html and files [{path:index.html,content:complete HTML}]. Create the complete first implementation, including all behaviour, in this action; do not deliberately leave placeholders for subsequent patches. After creation use targeted patches, not full-file regeneration.
patch: provide revision from read and edits [{path:index.html,search:exact unique current text,replace:new text}]. Each search must match exactly once; edits are atomic. Keep each edit small: change the few lines needed, or insert new functions at an exact short anchor. Do not echo an unchanged function or file in search/replace. Copy search verbatim from the read observation, including comment syntax and whitespace. If matching fails, use the reported actual lines and re-read as needed; do not repeat the same unmatched search. Re-read after any change before another patch.
run: compile source and load it in an isolated browser. Returns actual startup/runtime errors, visible text and controls. Run after edits.
test: execute checks against the current file. Each test action starts ONE fresh page. Its checks execute IN ORDER on that same page, preserving state between checks. Separate test actions start separate fresh pages. Include prerequisites before relying on a state. Each check has description, actions [{kind:click/fill/key,selector:native CSS,value:string}], selector, text, min_count, max_count, changed. All fields required. text="" means any text. changed=true requires a visible state change during actions. Reset tests must first change state. Check intended results, not merely the existence of a handler. A failing check may mean an application defect OR a wrong test: inspect the evidence and choose the correction.
When a test fails, compare its individual count/text/change assertions and before/after states. If the desired final value already existed before the action, that check did not test a transition. Test again with actions that establish the prerequisite state. Do not alter working application behaviour to satisfy an invalid test. Startup smoke actions are separate from the test script. Checks within one test action share state; separate test actions start fresh.
finish: give a concise summary only when the latest source has successfully run and your requested interaction tests passed. Failed/stale runs and tests cannot be overridden. You are responsible for checking completeness against the original request; startup alone does not prove requested functionality. Before finish, compare the visible presentation and controls with the original request; do not infer that an unexercised interaction works merely because startup passed.
Tool errors are observations: correct the action or inspect files, then continue. Keep code and observations concise. Do not claim to have tested something without executing it.'''


class BrowserEnvironment:
    instructions = INSTRUCTIONS

    def __init__(self, project_schema, workspace, run, connected):
        self.workspace,self.run,self.connected = workspace,run,connected
        self.read_revision = None
        self.last_run = None
        self.last_test = None
        variants = {
            'read':{'start_line':{'type':'integer','minimum':1},'end_line':{'type':'integer','minimum':1},
                    'search':{'type':'string'},'offset':{'type':'integer','minimum':0}}, 'write':{'project':project_schema},
            'patch':{'revision':{'type':'string'},'edits':{'type':'array','items':EDIT,'minItems':1,'maxItems':12}},
            'run':{}, 'test':{'checks':{'type':'array','items':TEST,'minItems':1,'maxItems':30}},
            'finish':{'summary':{'type':'string','minLength':1,'maxLength':2000}}}
        self._action_schema = {'oneOf':[obj({'reason':{'type':'string','minLength':1,'maxLength':800},'action':{'const':name},**args}) for name,args in variants.items()]}
        self.fields = {name:{'reason','action',*args} for name,args in variants.items()}

    @property
    def action_schema(self):
        # Expose only executable edit permissions. A model must inspect the
        # actual current file before it can spend output on a patch.
        current_read = self.read_revision is not None and self.read_revision == self.revision()
        available = {'write'} if self.workspace.code is None else {'read','run','test','finish'}
        if current_read: available.add('patch')
        return {'oneOf':[variant for variant in self._action_schema['oneOf']
                         if variant['properties']['action']['const'] in available]}

    def revision(self):
        return hashlib.sha256(self.workspace.code.encode()).hexdigest() if self.workspace.code is not None else None

    def execute(self, action, watchdog_seconds):
        if not isinstance(action,dict) or action.get('action') not in self.fields:
            raise ValueError('Choose read, write, patch, run, test or finish.')
        name = action['action']
        if name == 'read':
            if not {'reason','action'} <= set(action) <= self.fields[name]: raise ValueError('Invalid fields for read')
        elif set(action) != self.fields[name]: raise ValueError('Invalid fields for '+name)
        if not isinstance(action['reason'],str) or not 1<=len(action['reason'])<=800:
            raise ValueError('Provide a brief action rationale in reason.')
        w, r = self.workspace, self.run
        if name == 'write':
            if w.code is not None: raise ValueError('File already exists. Read it, then use a targeted patch.')
            w.candidate(action['project'])
            return {'ok':True,'path':'index.html','revision':self.revision(),'bytes':len(w.code.encode())}
        if w.code is None: raise ValueError('Workspace is empty. Create index.html with write first.')
        if name == 'read':
            actual = (w.directory/'index.html').read_bytes().decode('utf-8')
            if actual != w.code: raise ValueError('Workspace changed outside this run; refusing stale edits.')
            start,end,offset = action.get('start_line',1),action.get('end_line'),action.get('offset',0)
            search = action.get('search','')
            if (type(start) is not int or start<1 or type(offset) is not int or offset<0
                or (end is not None and (type(end) is not int or end<start)) or not isinstance(search,str)):
                raise ValueError('read requires a valid line range, nonnegative offset and literal search string.')
            self.read_revision = self.revision()
            result = {'ok':True,'path':'index.html','revision':self.read_revision}
            if search:
                snippets, count, pos = [], 0, actual.find(search)
                while pos>=0:
                    count += 1
                    if len(snippets)<3:
                        line = actual[:pos].count('\n')+1
                        first = max(1,line-2)
                        block_start = sum(len(s) for s in actual.splitlines(keepends=True)[:first-1])
                        snippets.append(source_window(actual,first,line+77,
                            max(0,pos-block_start-200)+offset,2000))
                    pos = actual.find(search,pos+len(search))
                return {**result,'search':search,'matches':count,'snippets':snippets}
            return {**result,**source_window(actual,start,end,offset)}
        if name == 'patch':
            if self.read_revision != self.revision() or action['revision'] != self.read_revision:
                raise ValueError('Read the current index.html before patching; revision must match.')
            if (w.directory/'index.html').read_bytes().decode('utf-8') != w.code:
                raise ValueError('Workspace changed since read; no patch applied.')
            if r.data['repairs'] >= r.limits.repairs: raise AgentStop('REPAIR_LIMIT','Maximum application patches reached.')
            w.patch({'edits':action['edits']})
            self.read_revision = None
            r.data['repairs'] += 1
            r.data['repair_attempts'] = r.data['repairs']
            return {'ok':True,'revision':self.revision(),'bytes':len(w.code.encode())}
        if name in {'run','test'}:
            checks = []
            if name == 'test':
                if not isinstance(action['checks'],list) or not 1<=len(action['checks'])<=30:
                    raise ValueError('test requires 1–30 checks.')
                if any(not isinstance(c,dict) or set(c)!=set(TEST['required']) for c in action['checks']):
                    raise ValueError('Invalid browser test fields.')
                checks = lab_verification.validate_checks({'checks':[
                    {**c,'mode':'browser','passed':False,'evidence':''} for c in action['checks']]},w.code)['checks']
            observation = self.run_browser(checks,watchdog_seconds)
            receipt = (self.revision(),observation)
            if name == 'run': self.last_run = receipt
            else: self.last_test = receipt
            return {'revision':self.revision(),**self.digest(observation)}
        if not isinstance(action['summary'],str) or not action['summary'].strip() or len(action['summary'])>2000:
            raise ValueError('finish requires a concise summary.')
        if (w.directory/'index.html').read_bytes().decode('utf-8') != w.code:
            raise ValueError('Workspace changed outside the agent; finish refused.')
        if not self.last_run or self.last_run[0] != self.revision() or not self.last_run[1]['passed']:
            raise ValueError('Run the current source successfully before finish.')
        if self.last_test and (self.last_test[0] != self.revision() or not self.last_test[1]['passed']):
            raise ValueError('Current tests failed or are stale. Correct and rerun them before finish.')
        if not self.last_test and any(c.get('visible') and not c.get('disabled') for c in self.last_run[1].get('controls',[])):
            raise ValueError('The page has interactive controls. Execute tests of their requested behaviour before finish.')
        observation = self.last_test[1] if self.last_test else self.last_run[1]
        r.data.update(summary=action['summary'],verification=observation)
        return {'ok':True,'finished':True,'summary':action['summary']}

    def run_browser(self, checks, watchdog_seconds):
        source = lab_verification.check_source(self.workspace.code)
        observation = {'source':source,'passed':False,'errors':list(source['errors']),'checks':[]}
        if any('compiler unavailable' in e.lower() or 'compiler failed' in e.lower() for e in source['errors']):
            raise AgentStop('ENVIRONMENT_UNAVAILABLE','; '.join(source['errors']))
        if source['passed']:
            if not self.connected:
                raise AgentStop('ENVIRONMENT_UNAVAILABLE','Browser execution requires the connected Workbench streaming workflow.')
            observation.update(lab_verification.request_browser(self.run.data['run_id'],{'checks':checks},self.run.emit,
                timeout=watchdog_seconds))
            if observation.get('infrastructure_failure'):
                raise AgentStop('ENVIRONMENT_UNAVAILABLE','; '.join(observation.get('errors',[])))
        observation['requirements'] = checks
        self.run.data.pop('pending_test',None)
        self.run.data['attempts'][-1].update(source=source,browser=observation,requirements=checks,failures=observation['errors'])
        self.run.data.setdefault('observations',[]).append(observation)
        return observation

    @staticmethod
    def digest(observation):
        checks = [{'description':str(c.get('description',''))[:250],'passed':c.get('passed'),'assertions':c.get('assertions'),
                   'matches':[str(m)[:160] for m in c.get('matches',[])[:3]],
                   'errors':[str(e)[:250] for e in c.get('errors',[])[:3]],
                   'actions':[{**{k:str(a.get(k,''))[:180] for k in ('selector','before','after')},'changed':a.get('changed')}
                              for a in c.get('actions',[])[:6]]} for c in observation.get('checks',[])[:30]]
        return {'ok':observation.get('passed') is True,'passed':observation.get('passed'),
                'source':observation['source'],'usable':observation.get('usable'),
                'errors':list(dict.fromkeys(str(e)[:1000] for e in observation.get('errors',[])))[:12],
                'checks':checks,'controls':observation.get('controls',[])[:30],
                'visible_text':str(observation.get('startup',{}).get('initial',{}).get('text',''))[:4000],
                'smoke_actions':[{**{k:str(a.get(k,''))[:180] for k in ('label','before','after')},'changed':a.get('changed')}
                                 for a in observation.get('startup',{}).get('actions',[])[:12]],
                'warnings':observation.get('warnings',[])[:12]}
