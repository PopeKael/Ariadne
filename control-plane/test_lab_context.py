"""Source is external state; deterministic repair fixtures need no model calls."""
import copy
import hashlib
import json
import unittest

from lab_agent import RunLimits
import test_lab_agent as fixtures
from test_lab_verification import GOOD, project


def revision(source):
    return hashlib.sha256(source.encode()).hexdigest()


def message_bytes(messages):
    return len(json.dumps(messages,ensure_ascii=False).encode())


def repair_fixture():
    source = GOOD.replace('Counter test','Revision 0').replace('</body>',
        '\n'+'\n'.join('<!-- unrelated source section %03d %s -->' % (n,'x'*55) for n in range(300))+'\n</body>')
    sources = {revision(source):source}
    actions = [{'action':'write','project':project(source)}]
    for n in range(3):
        search = 'Revision '+str(n)
        read = {'action':'read','search':search}
        actions.extend([read,
            lambda e:{'action':'patch','revision':e.read_revision,'edits':[
                {'path':'index.html','search':'nonexistent exact anchor','replace':'ignored'}]},
            read,
            lambda e,n=n,search=search:{'action':'patch','revision':e.read_revision,'edits':[
                {'path':'index.html','search':search,'replace':'Revision '+str(n+1)}]},
            {'action':'run'}])
        source = source.replace(search,'Revision '+str(n+1))
        sources[revision(source)] = source
    actions.append({'action':'finish','summary':'Complete'})
    runtime = [{'passed':True,'errors':[],'checks':[],
                'startup':{'initial':{'text':'Current browser receipt '+str(n)+' '+('z'*3500)}}} for n in range(3)]
    data,calls = fixtures.AgentTests().run_actions(actions,RunLimits(repairs=3),runtime=runtime)
    # Replay the former append-only policy with the SAME actions/observations,
    # replacing targeted reads with the former complete-file observation.
    legacy = copy.deepcopy(data['trajectory'])
    for message in legacy[2:]:
        payload = json.loads(message['content'])
        observation = payload.get('observation',{})
        if observation.get('path')=='index.html' and ('content' in observation or 'snippets' in observation):
            message['content'] = json.dumps({'observation':{
                'ok':True,'path':'index.html','revision':observation['revision'],
                'content':sources[observation['revision']]}},ensure_ascii=False)
    before = [message_bytes(legacy[:2+2*n]) for n in range(len(calls))]
    after = [message_bytes(messages) for messages in calls]
    receipt = {'fixture':'Generic 300-section page, three successful repairs and three failed exact matches',
               'measurement':'UTF-8 bytes of serialized model messages; excludes identical action schema; not token counts',
               'model_calls':'Scripted actions only; no Ollama or paid API requests',
               'stop_reason':data['stop_reason'],'repairs':data['repairs'],
               'requests':len(calls),'before_bytes':before,'after_bytes':after,
               'before_peak_bytes':max(before),'after_peak_bytes':max(after),
               'before_final_bytes':before[-1],'after_final_bytes':after[-1]}
    return receipt,data,calls


class ContextTests(unittest.TestCase):
    def test_multi_repair_context_growth_and_audit(self):
        receipt,data,calls = repair_fixture()
        self.assertEqual(data['stop_reason'],'VERIFIED')
        self.assertEqual(len(data['attempts']),4)
        self.assertLess(receipt['after_peak_bytes'],receipt['before_peak_bytes']*.25)
        self.assertLess(receipt['after_final_bytes'],receipt['before_final_bytes']*.15)
        for messages in calls:
            self.assertEqual(messages[0],calls[0][0])
            self.assertEqual(messages[1]['content'],'A generic request')
            self.assertNotIn('unrelated source section 299',json.dumps(messages))
        self.assertIn('unrelated source section 299',data['trajectory'][2]['content'])
        self.assertTrue(all(a['revision'] for a in json.loads(calls[-1][2]['content'])['workspace_state']['action_history']))
        latest = json.dumps(calls[-1])
        self.assertIn('Current browser receipt 2',latest)
        self.assertNotIn('Current browser receipt 0',latest)
        # Successful edits retire the previous revision's read and browser state.
        for call in (calls[5],calls[10],calls[15]):
            self.assertNotIn('"snippets"',str(call))
            self.assertNotIn('Current browser receipt',str(call))

    def test_ranges_search_and_long_line_continuation(self):
        source = GOOD.replace('<body>','<body>\r\n<!-- '+('x'*8500)+'TARGET -->\r\n')
        data,calls = fixtures.AgentTests().run_actions([
            {'action':'write','project':project(source)},
            {'action':'read','start_line':1,'end_line':1},
            {'action':'read','start_line':2,'end_line':2,'offset':6000},
            {'action':'read','search':'TARGET'},
            {'action':'run'},{'action':'finish','summary':'Complete'}])
        self.assertEqual(data['stop_reason'],'VERIFIED')
        first = json.loads(calls[2][-1]['content'])['observation']
        self.assertTrue(first['content'].endswith('\r\n'))
        self.assertNotIn('TARGET',first['content'])
        continuation = json.loads(calls[3][-1]['content'])['observation']
        self.assertIn('TARGET',continuation['content'])
        result = json.loads(calls[4][-1]['content'])['observation']
        self.assertEqual(result['matches'],1)
        self.assertIn('TARGET',result['snippets'][0]['content'])
        self.assertLessEqual(sum(len(s['content']) for s in result['snippets']),6000)

    def test_default_read_is_bounded_and_can_continue(self):
        source = GOOD.replace('</body>','<!-- '+('q'*9000)+' -->\n</body>')
        data,calls = fixtures.AgentTests().run_actions([{'action':'write','project':project(source)},
            {'action':'read'},{'action':'read','offset':6000},
            {'action':'run'},{'action':'finish','summary':'Complete'}])
        first = json.loads(calls[2][-1]['content'])['observation']
        second = json.loads(calls[3][-1]['content'])['observation']
        self.assertEqual(first['next_offset'],6000)
        self.assertEqual(first['content']+second['content'],source)
        self.assertEqual(data['stop_reason'],'VERIFIED')

    def test_duplicate_patch_failure_reports_actual_match_locations(self):
        source = GOOD.replace('</body>','\n<script>\nfunction duplicate() {}\n\nfunction duplicate() {}\n</script>\n</body>')
        def failed(env):
            return {'action':'patch','revision':env.read_revision,'edits':[
                {'path':'index.html','search':'function duplicate() {','replace':'function fixed() {'}]}
        data,calls = fixtures.AgentTests().run_actions([{'action':'write','project':project(source)},
            {'action':'read','search':'duplicate'},failed,{'action':'run'},
            {'action':'finish','summary':'Complete'}])
        observation = json.loads(calls[3][-1]['content'])['observation']
        self.assertIn('"matches": 2',observation['error'])
        self.assertEqual(len(observation['snippets']),2)
        self.assertNotEqual(observation['snippets'][0]['start_line'],observation['snippets'][1]['start_line'])
        self.assertTrue(all('function duplicate()' in s['content'] for s in observation['snippets']))
        self.assertEqual(data['repairs'],0)

    def test_failed_atomic_batch_returns_committed_source(self):
        def failed(env):
            return {'action':'patch','revision':env.read_revision,'edits':[
                {'path':'index.html','search':'Counter test','replace':'temporary name'},
                {'path':'index.html','search':'<!-- Counter test -->','replace':'ignored'}]}
        data,calls = fixtures.AgentTests().run_actions([{'action':'write','project':project(GOOD)},
            {'action':'read'},failed,{'action':'run'},{'action':'finish','summary':'Complete'}])
        observation = json.loads(calls[3][-1]['content'])['observation']
        self.assertIn('Counter test',str(observation['snippets']))
        self.assertNotIn('temporary name',str(observation['snippets']))
        self.assertEqual(len(data['attempts']),1)


if __name__=='__main__':
    unittest.main()
