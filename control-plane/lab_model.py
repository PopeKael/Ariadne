"""Provider adapter and deadline-aware Ollama transport, independent of agent roles."""
import json
import socket
import threading
import time
import urllib.request
from lab_agent import AgentStop


def ollama_stream(endpoint, model, payload, deadline):
    remaining = deadline-time.monotonic()
    if remaining <= 0: raise AgentStop('CALL_TIME_LIMIT','Model call deadline reached.')
    route = '/api/chat' if 'messages' in payload else '/api/generate'
    request = urllib.request.Request(endpoint.rstrip('/')+route,data=json.dumps(payload).encode(),
                                    headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=remaining) as response:
        # A socket inactivity timeout alone resets with every chunk. Shutdown the
        # owned socket at the absolute deadline, including a blocked readline.
        sock = response.fp.raw._sock
        def cancel():
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        timer = threading.Timer(max(.001,deadline-time.monotonic()),cancel)
        timer.daemon = True
        timer.start()
        try:
            for line in response:
                if time.monotonic() >= deadline: raise AgentStop('CALL_TIME_LIMIT','Model call deadline reached; owned connection closed.')
                if line.strip(): yield json.loads(line)
            if time.monotonic() >= deadline: raise AgentStop('CALL_TIME_LIMIT','Model call deadline reached; owned connection closed.')
        except (OSError,ValueError) as exc:
            if time.monotonic() >= deadline: raise AgentStop('CALL_TIME_LIMIT','Model call deadline reached; owned connection closed.') from exc
            raise
        finally:
            timer.cancel()


class OllamaModelAdapter:
    def __init__(self, endpoint, model, context, stream, admission, activity, run, metrics, write, hardware):
        self.endpoint,self.model,self.context,self.stream = endpoint,model,context,stream
        self.admission,self.activity,self.run,self.metrics,self.write,self.hardware = admission,activity,run,metrics,write,hardware
        self.capabilities = {'generation':True,'chat':True,'structured_output':True,'streaming':True,
                             'context_tokens':context,'thinking':False,'metrics':True}

    def call(self, role, system, prompt, schema, deadline, messages=None):
        size = len((system+prompt+json.dumps(schema)).encode('utf-8'))
        # UTF-8 bytes provide a conservative token upper bound, not a live token count.
        output = min(8192,self.context//2)
        if size > min(self.run.limits.prompt_bytes,self.context-output):
            raise AgentStop('CONTEXT_BUDGET','Model input exceeds the configured conservative byte budget; source was not silently truncated.')
        payload = {'model':self.model,'system':system,'prompt':prompt,'format':schema,'stream':True,'keep_alive':-1,
                   'options':{'num_ctx':self.context,'num_predict':output,'temperature':0,'seed':42}}
        if messages is not None:
            payload.pop('system');payload.pop('prompt')
            payload['messages']=json.loads(json.dumps(messages))
        calls = self.run.data.setdefault('model_requests',[])
        call = {'number':len(calls)+1,'role':role,'reason':self.run.data['steps'][-1]['reason'],
                'request':payload,'context_bytes':size,'input_tokens':None,'output_tokens':None,'status':'running'}
        calls.append(call)
        self.run.data.setdefault('request',payload)
        self.run.save()
        chunks=[]; response={}; started=time.monotonic(); last_save=started
        try:
            with self.admission(),self.activity(self.model):
                stream = self.stream(self.endpoint,self.model,payload,deadline)
                try:
                    for chunk in stream:
                        if time.monotonic() >= deadline: raise AgentStop('CALL_TIME_LIMIT','Model call deadline reached.')
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

    def query(self, messages, schema, deadline):
        # Native chat and structured actions use the selected provider/model.
        return self.call('action',messages[0]['content'],
                         json.dumps(messages[1:],ensure_ascii=False),schema,deadline,messages)
