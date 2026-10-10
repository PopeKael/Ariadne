"""Native bounded controller. No provider, browser, or filesystem policy here."""
import json
import os
import time
from dataclasses import dataclass
from typing import Protocol


class ModelAdapter(Protocol):
    capabilities: dict
    def query(self, messages, schema, watchdog_seconds): ...


class Environment(Protocol):
    instructions: str
    action_schema: dict
    def execute(self, action, watchdog_seconds): ...


class AgentStop(RuntimeError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RunLimits:
    steps: int = 24
    repairs: int = 2
    watchdog_seconds: float = 900

    @classmethod
    def configured(cls):
        def number(name, default, low, high):
            value = float(os.environ.get('ARIADNE_LAB_' + name, default))
            if not low <= value <= high:
                raise ValueError('Invalid Lab limit: ' + name)
            return value
        return cls(int(number('MAX_STEPS',24,1,64)), int(number('MAX_REPAIRS',2,0,5)),
                   number('WATCHDOG_SECONDS',900,1,3600))


class RunRecord:
    def __init__(self, data, save, emit, limits, clock=time.monotonic):
        self.data, self.save, self.emit = data, save, emit
        self.limits, self.clock = limits, clock
        self.started = clock()
        data.update(steps=[], limits=vars(limits), stop_reason=None, repairs=0)

    def step(self, action, role, reason, operation):
        started = self.clock()
        step = {'number':len(self.data['steps'])+1, 'action':action, 'role':role,
                'reason':reason, 'status':'running', 'started_ms':round((started-self.started)*1000,1),
                'model_call_number':None,'input_tokens':None,'output_tokens':None,
                'generation_duration_ms':0,'tool_duration_ms':0,'context_bytes':0}
        self.data['steps'].append(step)
        self.data['state'] = action
        self.save()
        self.emit({'type':'state','state':action})
        try:
            result = operation()
            step['status'] = 'complete'
            return result
        except Exception as exc:
            step.update(status='failed', error=str(exc)[:1000])
            raise
        finally:
            step['duration_ms'] = round((self.clock()-started)*1000,1)
            if role == 'tool': step['tool_duration_ms'] = step['duration_ms']
            self.save()

    def totals(self):
        calls = self.data.get('model_requests', [])
        return {'model_calls':len(calls), 'input_tokens':sum(c.get('telemetry',{}).get('prompt_eval_count') or 0 for c in calls) if all(c.get('telemetry',{}).get('prompt_eval_count') is not None for c in calls) else None,
                'output_tokens':sum(c.get('telemetry',{}).get('eval_count') or 0 for c in calls) if all(c.get('telemetry',{}).get('eval_count') is not None for c in calls) else None,
                'token_counts_complete':all(c.get('telemetry',{}).get('prompt_eval_count') is not None and c.get('telemetry',{}).get('eval_count') is not None for c in calls),
                'model_duration_ms':sum(c.get('duration_ms',0) for c in calls),
                'tool_duration_ms':sum(s.get('tool_duration_ms',0) for s in self.data['steps']),
                'wall_duration_ms':round((self.clock()-self.started)*1000,1)}


class LabAgent:
    """Query -> execute -> observation. No semantic roles or task-specific stages."""
    def __init__(self, model, environment, workspace, run):
        self.model, self.environment, self.workspace, self.run = model, environment, workspace, run

    def run_task(self, request):
        r, env = self.run, self.environment
        messages = [{'role':'system','content':env.instructions}, {'role':'user','content':request}]
        # The durable audit is not the model's working context. Source lives in
        # the workspace; old code and tool output remain available in the audit.
        trajectory = list(messages)
        r.data['trajectory'] = trajectory
        history, retained = [], {}
        errors = 0
        repeated = {}
        try:
            for _ in range(r.limits.steps):
                try:
                    action = r.step('GENERATING','model','Choose next environment action',
                        lambda:self.model.query(messages, env.action_schema,
                            r.limits.watchdog_seconds))
                except (ValueError, TypeError) as exc:
                    action = None
                    observation = {'ok':False,'error':'Invalid action response: '+str(exc)[:1000]}
                else:
                    trajectory.append({'role':'assistant','content':json.dumps(action,ensure_ascii=False)})
                    try:
                        observation = r.step('EXECUTE','tool','Execute model-selected action',
                                            lambda:env.execute(action,r.limits.watchdog_seconds))
                    except (ValueError,TypeError,KeyError) as exc:
                        observation = {'ok':False,'error':str(exc)[:1000],**getattr(exc,'details',{})}
                latest = {'role':'user','content':json.dumps({'observation':observation},ensure_ascii=False)}
                trajectory.append(latest)
                name = action.get('action') if isinstance(action,dict) else 'invalid'
                revision = observation.get('revision', retained.get('revision'))
                if name in {'write','patch'} and observation.get('ok'):
                    retained.clear()  # Every previous source/browser receipt is stale.
                retained['revision'] = revision
                if name == 'read' and observation.get('ok'):
                    retained['read'] = latest
                if name in {'run','test'} and 'passed' in observation:
                    retained[name] = latest  # Replace older receipts of this kind.
                history.append({'step':len(history)+1,'action':name,
                                'reason':str(action.get('reason',''))[:240] if isinstance(action,dict) else '',
                                'ok':observation.get('ok'), 'revision':revision,
                                'error':str(observation.get('error',''))[:240]})
                state = {'workspace':{'path':'index.html','revision':revision},'action_history':history}
                messages = trajectory[:2] + [{'role':'user','content':json.dumps({'workspace_state':state},ensure_ascii=False)}]
                messages.extend(value for key,value in retained.items() if key != 'revision' and value is not latest)
                if isinstance(action,dict):
                    # Action bodies are external artifacts too. Keep the action
                    # identity/rationale without echoing generated code or tests.
                    brief = {k:v for k,v in action.items() if k not in {'project','edits','checks'}}
                    messages.append({'role':'assistant','content':json.dumps(brief,ensure_ascii=False)})
                messages.append(latest)
                r.data['model_context'] = messages
                r.save()
                if observation.get('finished'):
                    r.data.update(success=True,verified=True,state='READY',stop_reason='VERIFIED')
                    break
                errors = errors+1 if observation.get('error') else 0
                if errors >= 3: raise AgentStop('FORMAT_ERROR_LIMIT','Three consecutive invalid actions; see trajectory.')
                executed = {k:v for k,v in action.items() if k!='reason'} if isinstance(action,dict) else action
                signature = json.dumps([executed,observation],sort_keys=True)
                repeated[signature] = repeated.get(signature,0)+1
                if repeated[signature] >= 3: raise AgentStop('NO_PROGRESS','The same action and observation repeated three times.')
            else:
                raise AgentStop('STEP_LIMIT','Lab model-action step limit reached.')
        except AgentStop as exc:
            r.data.update(state='NEEDS ATTENTION',stop_reason=exc.reason,error=str(exc))
        except (OSError,RuntimeError) as exc:
            r.data.update(state='NEEDS ATTENTION',stop_reason='ENVIRONMENT_ERROR',error=str(exc)[:2000])
        finally:
            r.data.update(pending=False,totals=r.totals(),wall_duration_ms=r.totals()['wall_duration_ms'])
            r.data.pop('pending_test',None)
            r.save()
            r.emit({'type':'state','state':r.data['state']})
        return self.workspace.code
