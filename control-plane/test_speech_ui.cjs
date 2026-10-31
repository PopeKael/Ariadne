const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(__dirname + '/speech.js', 'utf8');
const savedVoiceId = '47de8170fb1b4027b262e50d983f432c';
const liveMode = process.argv.includes('--live');
const liveTranscribe = process.argv.includes('--live-transcribe');
const apiOrigin = 'http://127.0.0.1:8765';
const liveAudioPath = process.env.ARIADNE_SPEECH_TEST_AUDIO;

class FakeElement {
  constructor() {
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.files = [];
    this.children = [];
    this.listeners = {};
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  removeAttribute(name) { delete this[name]; }
  focus() {}
}

function response(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

async function makeWorkspace(status) {
  const elements = new Map();
  const requests = [];
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement());
    return elements.get(id);
  };
  const fetch = async (url, options = {}) => {
    const request = { url, options };
    requests.push(request);
    if (liveMode) {
      const result = await globalThis.fetch(new URL(url, apiOrigin), options);
      try { request.payload = await result.clone().json(); } catch { request.payload = null; }
      return result;
    }
    if (url === '/api/speech/status') return response(status);
    if (url === '/api/speech/voices') return response({ ok: true, voices: [
      { id: savedVoiceId, name: 'Saved test voice', available: true },
    ] });
    if (url === '/api/speech/jobs') return response({ ok: true, jobs: [] });
    if (url === '/api/speech/generate' || url === '/api/speech/transcribe') {
      return response({ ok: true, job: {
        id: 'b'.repeat(32), state: 'queued', operation: url.endsWith('generate') ? 'synthesis' : 'transcription',
        stage: 'Waiting for shared GPU admission', result: null,
      } });
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  const FormData = liveMode ? globalThis.FormData : class {
    constructor() { this.entries = []; }
    append(...args) { this.entries.push(args); }
  };
  element('speech-voice-mode').value = 'reference';
  element('speech-text').value = 'A short handoff test.';
  element('speech-voice-select').value = savedVoiceId;
  if (liveMode) {
    if (liveTranscribe && !liveAudioPath) throw new Error('Set ARIADNE_SPEECH_TEST_AUDIO to a local WAV before a live Transcribe run.');
    const audio = liveTranscribe
      ? new Blob([fs.readFileSync(liveAudioPath)], { type: 'audio/wav' })
      : new Blob([], { type: 'audio/wav' });
    Object.defineProperty(audio, 'name', { value: 'handoff-test.wav' });
    element('speech-file').files = [audio];
  } else {
    element('speech-file').files = [{ name: 'sample.wav' }];
  }
  vm.runInNewContext(source, {
    document: { getElementById: element, createElement: () => new FakeElement() },
    window: { setInterval() {}, confirm: () => false },
    navigator: { mediaDevices: { getUserMedia() {} }, clipboard: { writeText() {} } },
    fetch,
    FormData,
    console,
  });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const renderDeadline = Date.now() + 10000;
  while ((!elements.has('speech-runtime-state') || !element('speech-voice-select').children.length) && Date.now() < renderDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  if (!elements.has('speech-runtime-state') || !element('speech-voice-select').children.length) {
    throw new Error('Speech UI did not finish loading runtime status and saved voices.');
  }
  return { elements, requests };
}

(async () => {
  const readyAfterHandoff = liveMode
    ? await (await globalThis.fetch(`${apiOrigin}/api/speech/status`, { cache: 'no-store' })).json()
    : {
    state: 'ready_after_handoff',
    detail: 'Ready after GPU handoff. Rust Host will verify capacity.',
    capabilities: { asr: true, tts: true },
    gpu: { available: true, free_gb: 6.4, total_gb: 15.8 },
    gpu_owner: { current_gpu_owner: 'AI', transition_state: 'IDLE' },
    models: [],
    };
  const synthesis = await makeWorkspace(readyAfterHandoff);
  if (liveMode) {
    const initial = await (await globalThis.fetch(`${apiOrigin}/api/model-control`, { cache: 'no-store' })).json();
    assert.ok(initial.loaded?.includes('qwen3.5:9b-q4_K_M'), 'Home model must be resident before the Generate click');
    assert.equal(readyAfterHandoff.state, 'ready_after_handoff');
    if (liveTranscribe) {
      const transcription = await makeWorkspace(readyAfterHandoff);
      transcription.elements.get('speech-file').listeners.change();
      assert.equal(transcription.elements.get('speech-transcribe').disabled, false,
        JSON.stringify({ keys: [...transcription.elements.keys()], state: transcription.elements.get('speech-runtime-state')?.textContent, files: transcription.elements.get('speech-file')?.files.length }));
      await transcription.elements.get('speech-transcribe').listeners.click();
      const transcribeRequest = transcription.requests.find((request) => request.url === '/api/speech/transcribe');
      assert.ok(transcribeRequest?.payload?.ok, `Live Transcribe failed: ${JSON.stringify(transcribeRequest?.payload)}`);
      let job = transcribeRequest.payload.job;
      const observed = new Set();
      const deadline = Date.now() + 180000;
      while (Date.now() < deadline && ['queued', 'running', 'stopping'].includes(job.state)) {
        const [modelResponse, jobResponse] = await Promise.all([
          globalThis.fetch(`${apiOrigin}/api/model-control`, { cache: 'no-store' }),
          globalThis.fetch(`${apiOrigin}/api/speech/jobs/${job.id}`, { cache: 'no-store' }),
        ]);
        const [modelState, jobPayload] = await Promise.all([modelResponse.json(), jobResponse.json()]);
        observed.add(modelState.gpu_owner?.current_gpu_owner || 'UNKNOWN');
        if (!modelState.loaded?.includes('qwen3.5:9b-q4_K_M')) observed.add('HOME_UNLOADED');
        job = jobPayload.job;
        if (['queued', 'running', 'stopping'].includes(job.state)) await new Promise((resolve) => setTimeout(resolve, 150));
      }
      assert.equal(job.state, 'completed', `Live ASR job ended ${job.state}: ${job.message || ''}`);
      assert.ok(observed.has('SPEECH') || observed.has('HOME_UNLOADED'), `No Rust Host handoff observed: ${[...observed].join(', ')}`);
      assert.match(job.result.transcript.toLowerCase(), /handoff test/);
      assert.ok(fs.existsSync(job.result.text_path), 'Live Transcribe must save its TXT export');
      console.log(`Live Transcribe click completed job ${job.id}; Home was resident before submission; observed GPU states: ${[...observed].join(', ')}.`);
      return;
    }
  }
  assert.equal(synthesis.elements.get('speech-runtime-state').textContent, 'Ready after GPU handoff');
  assert.equal(synthesis.elements.get('speech-generate').disabled, false);
  await synthesis.elements.get('speech-generate').listeners.click();
  const generateRequest = synthesis.requests.find((request) => request.url === '/api/speech/generate');
  assert.ok(generateRequest, 'Generate click must submit the Speech job');
  assert.equal(JSON.parse(generateRequest.options.body).voice_id, savedVoiceId);
  if (liveMode) {
    assert.equal(generateRequest.payload?.ok, true, `Live Generate failed: ${JSON.stringify(generateRequest.payload)}`);
    let job = generateRequest.payload.job;
    const observed = new Set();
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline && ['queued', 'running', 'stopping'].includes(job.state)) {
      const [modelResponse, jobResponse] = await Promise.all([
        globalThis.fetch(`${apiOrigin}/api/model-control`, { cache: 'no-store' }),
        globalThis.fetch(`${apiOrigin}/api/speech/jobs/${job.id}`, { cache: 'no-store' }),
      ]);
      const [modelState, jobPayload] = await Promise.all([modelResponse.json(), jobResponse.json()]);
      observed.add(modelState.gpu_owner?.current_gpu_owner || 'UNKNOWN');
      if (!modelState.loaded?.includes('qwen3.5:9b-q4_K_M')) observed.add('HOME_UNLOADED');
      job = jobPayload.job;
      if (['queued', 'running', 'stopping'].includes(job.state)) await new Promise((resolve) => setTimeout(resolve, 150));
    }
    assert.equal(job.state, 'completed', `Live Speech job ended ${job.state}: ${job.message || ''}`);
    assert.ok(observed.has('SPEECH') || observed.has('HOME_UNLOADED'), `No Rust Host handoff observed: ${[...observed].join(', ')}`);
    assert.ok(fs.existsSync(job.result.audio_path), 'Live Generate must create a WAV output');
    assert.ok(fs.existsSync(job.result.mp3_path), 'Live Generate must create an MP3 output');
    console.log(`Live Generate click completed job ${job.id}; Home was resident before submission; observed GPU states: ${[...observed].join(', ')}.`);
    return;
  }

  const transcription = await makeWorkspace(readyAfterHandoff);
  transcription.elements.get('speech-file').listeners.change();
  assert.equal(transcription.elements.get('speech-transcribe').disabled, false);
  await transcription.elements.get('speech-transcribe').listeners.click();
  assert.ok(transcription.requests.find((request) => request.url === '/api/speech/transcribe'), 'Transcribe click must submit the Speech job');

  const blocked = await makeWorkspace({ ...readyAfterHandoff, state: 'blocked', detail: 'An active GPU lease prevents Speech admission.' });
  assert.equal(blocked.elements.get('speech-generate').disabled, true);
  assert.equal(blocked.elements.get('speech-transcribe').disabled, true);
  const busy = await makeWorkspace({ ...readyAfterHandoff, state: 'busy', detail: 'A Speech job is already running.' });
  assert.equal(busy.elements.get('speech-generate').disabled, true);
  assert.equal(busy.elements.get('speech-transcribe').disabled, true);
  console.log('Speech UI enables Generate and Transcribe after safe GPU handoff readiness, submits both controls, and keeps blocked or busy states disabled.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
