"""Short HTTP requests around a background render; ComfyUI supplies real progress."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import threading
import time
import uuid


class ImageJobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.latest = None

    def snapshot(self, job_id=None):
        with self.lock:
            job = self.jobs.get(job_id or self.latest)
            if job is None:
                return None
            result = copy.deepcopy(job)
            result['elapsed_seconds'] = round((job.get('finished_at') or time.time()) - job['started_at'], 1)
            return result

    def start(self, body, generate):
        with self.lock:
            request_id = str(body.get('request_id') or uuid.uuid4().hex)
            if request_id in self.jobs:
                return {'ok': True, 'job': self.snapshot(request_id)}, 202
            current = self.snapshot()
            if current and current['state'] in {'queued', 'running'}:
                return {'ok': False, 'message': 'An image is already rendering.', 'job': current}, 409
            job = {'id': request_id, 'state': 'queued', 'stage': 'Waiting for GPU',
                   'started_at': time.time(), 'step': None, 'total_steps': None}
            self.jobs[request_id] = job
            self.latest = request_id
            # Bound retained results without discarding the current job.
            while len(self.jobs) > 32:
                del self.jobs[next(iter(self.jobs))]

        def update(**values):
            with self.lock:
                job.update(values)

        def run():
            update(state='running', stage='Checking image engine')
            try:
                result, status = generate(body, on_progress=update)
            except Exception as exc:
                result, status = {'ok': False, 'message': f'Image generation failed: {exc}'}, 500
            update(state='completed' if result.get('ok') else 'error',
                   stage='Complete' if result.get('ok') else 'Failed',
                   result=result, http_status=status, finished_at=time.time())

        threading.Thread(target=run, name='image-generation', daemon=True).start()
        return {'ok': True, 'job': self.snapshot(request_id)}, 202


def event_progress(message, prompt_id):
    data = message.get('data') or {}
    if data.get('prompt_id') != prompt_id:
        return {}
    event = message.get('type')
    if event == 'progress':
        return {'stage': 'Sampling image', 'step': data.get('value'), 'total_steps': data.get('max')}
    if event == 'executing':
        stages = {'4': 'Loading SDXL checkpoint', '5': 'Preparing image canvas',
                  '6': 'Encoding prompt', '7': 'Encoding negative prompt',
                  '3': 'Loading diffusion model / sampling', '8': 'Decoding image', '9': 'Saving engine output'}
        return {'stage': stages.get(str(data.get('node')), 'Collecting rendered image'),
                'step': None, 'total_steps': None}
    return {}


@contextmanager
def comfy_progress(engine_url, client_id, on_progress):
    # Imported only for image generation; the existing desktop Python has websockets.
    from websockets.sync.client import connect
    url = engine_url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)
    stop = threading.Event()
    with connect(f'{url}/ws?clientId={client_id}', open_timeout=10, proxy=None) as ws:
        def listen():
            while not stop.is_set():
                try:
                    raw = ws.recv(timeout=1)
                    if isinstance(raw, str):
                        progress = event_progress(json.loads(raw), client_id)
                        if progress:
                            on_progress(**progress)
                except TimeoutError:
                    continue
                except Exception:
                    if not stop.is_set():
                        on_progress(stage='Rendering; live progress connection unavailable', step=None, total_steps=None)
                    break
        thread = threading.Thread(target=listen, name='image-progress', daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)
