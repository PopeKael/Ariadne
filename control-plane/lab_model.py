"""Provider adapter and idle-watchdog Ollama transport, independent of agent roles."""
import json
import socket
import threading
import time
import urllib.request
from lab_agent import AgentStop


def ollama_stream(endpoint, model, payload, watchdog_seconds=900):
    """Only silence is timed: valid streamed output refreshes the watchdog."""
    route = '/api/chat' if 'messages' in payload else '/api/generate'
    request = urllib.request.Request(endpoint.rstrip('/')+route,data=json.dumps(payload).encode(),
                                    headers={'Content-Type':'application/json'},method='POST')
    try:
        response = urllib.request.urlopen(request,timeout=watchdog_seconds)
    except (TimeoutError, socket.timeout) as exc:
        raise AgentStop('CONNECTION_WATCHDOG','Model connection produced no response within the emergency watchdog.') from exc
    with response:
        sock = response.fp.raw._sock
        last_progress = time.monotonic()
        stopped = threading.Event()
        hung = threading.Event()
        def watch():
            while not stopped.wait(min(1,watchdog_seconds/4)):
                if time.monotonic()-last_progress >= watchdog_seconds:
                    hung.set()
                    try: sock.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    return
        monitor = threading.Thread(target=watch,daemon=True)
        monitor.start()
        try:
            for line in response:
                if not line.strip(): continue
                chunk = json.loads(line)
                message = chunk.get('message') or {}
                if chunk.get('response') or message.get('content') or message.get('thinking') or chunk.get('done'):
                    last_progress = time.monotonic()
                if hung.is_set(): break
                yield chunk
            if hung.is_set():
                raise AgentStop('CONNECTION_WATCHDOG','Model stopped producing valid output; emergency idle watchdog closed the connection.')
        except (OSError,ValueError) as exc:
            if hung.is_set() or isinstance(exc,TimeoutError):
                raise AgentStop('CONNECTION_WATCHDOG','Model stopped producing valid output; emergency idle watchdog closed the connection.') from exc
            raise
        finally:
            stopped.set()
            monitor.join(timeout=2)


class OllamaModelAdapter:
    def __init__(self, endpoint, model, context, stream, admission, activity, run, metrics, write, hardware):
        self.endpoint,self.model,self.context,self.stream = endpoint,model,context,stream
        self.admission,self.activity,self.run,self.metrics,self.write,self.hardware = admission,activity,run,metrics,write,hardware
        self.capabilities = {'generation':True,'chat':True,'structured_output':True,'streaming':True,
                             'context_tokens':context,'thinking':False,'metrics':True}

    def call(self, role, system, prompt, schema, watchdog_seconds, messages=None):
        size = len((system+prompt+json.dumps(schema)).encode('utf-8'))
        output = min(8192,self.context//2)
        # Bytes are an initial upper bound, not tokens. Once the provider has
        # measured an identical conversation prefix, retain that measured count
        # and charge every new/changed UTF-8 byte plus a framing safety margin.
        # Full requests/trajectory remain untouched; no inferred chars/token ratio.
        input_bound = size
        basis = 'utf8_upper_bound'
        serialized = json.dumps(messages,ensure_ascii=False) if messages is not None else None
        calls = self.run.data.get('model_requests',[])
        if serialized is not None and calls:
            previous = calls[-1]
            prior = previous.get('request',{})
            measured = previous.get('input_tokens')
            if (previous.get('status') == 'complete' and isinstance(measured,int) and measured > 0
                    and 'messages' in prior):
                old = json.dumps(prior['messages'],ensure_ascii=False)
                prefix = 0
                for before,after in zip(old,serialized):
                    if before != after: break
                    prefix += 1
                schema_charge = 0 if prior.get('format') == schema else len(json.dumps(schema).encode('utf-8'))
                input_bound = min(input_bound, measured + len(serialized[prefix:].encode('utf-8')) + schema_charge + 1024)
                basis = 'provider_prefix_plus_utf8_margin'
        if input_bound > self.context-output:
            raise AgentStop('CONTEXT_BUDGET',f'Model input budget reached (bytes={size}, token upper bound={input_bound}, available={self.context-output}); source was not silently truncated.')
        payload = {'model':self.model,'system':system,'prompt':prompt,'format':schema,'stream':True,'keep_alive':-1,
                   'options':{'num_ctx':self.context,'num_predict':output,'temperature':0,'seed':42}}
        if messages is not None:
            payload.pop('system');payload.pop('prompt')
            payload['messages']=json.loads(json.dumps(messages))
        calls = self.run.data.setdefault('model_requests',[])
        call = {'number':len(calls)+1,'role':role,'reason':self.run.data['steps'][-1]['reason'],
                'request':payload,'context_bytes':size,'input_token_bound':input_bound,'context_budget_basis':basis,'input_tokens':None,'output_tokens':None,'status':'running'}
        calls.append(call)
        self.run.data.setdefault('request',payload)
        self.run.save()
        chunks=[]; response={}; started=time.monotonic(); last_save=started
        try:
            with self.admission(),self.activity(self.model):
                stream = self.stream(self.endpoint,self.model,payload,watchdog_seconds)
                try:
                    for chunk in stream:
                        response.update(chunk)
                        text = str(chunk.get('message',{}).get('content') or chunk.get('response') or '')
                        chunks.append(text)
                        self.run.data['output_characters'] += len(text)
                        self.run.emit({'type':'output','characters':self.run.data['output_characters'],'chunk_characters':len(text)})
                        if time.monotonic()-last_save >= 1:
                            self.run.save();last_save=time.monotonic()
                finally:
                    close=getattr(stream,'close',None)
                    if close: close()
            if not response.get('done') or response.get('done_reason') in {'length','context','context_length'}:
                raise AgentStop('OUTPUT_LIMIT','Model response incomplete or output/context limit reached.')
            value=json.loads(''.join(chunks))
            call['status']='complete'
            return value
        except Exception as exc:
            call.update(status='failed',error=str(exc)[:1000])
            raise
        finally:
            self.write(f'model-{call["number"]}-{role}.json',''.join(chunks))
            call.update(duration_ms=round((time.monotonic()-started)*1000,1),telemetry=self.metrics(response),
                        input_tokens=response.get('prompt_eval_count'),output_tokens=response.get('eval_count'))
            self.run.data['steps'][-1].update(model_call_number=call['number'],input_tokens=call['input_tokens'],
                output_tokens=call['output_tokens'],generation_duration_ms=call['duration_ms'],context_bytes=size)
            if call['number']==1:self.run.data['telemetry']=call['telemetry']
            self.run.data['latest_model_telemetry']=call['telemetry']
            self.run.save()

    def query(self, messages, schema, watchdog_seconds):
        # Native chat and structured actions use the selected provider/model.
        return self.call('action',messages[0]['content'],
                         json.dumps(messages[1:],ensure_ascii=False),schema,watchdog_seconds,messages)
