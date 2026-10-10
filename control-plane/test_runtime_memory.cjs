const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const shell = fs.readFileSync(__dirname + '/page-shell.js', 'utf8');
const app = fs.readFileSync(__dirname + '/app.js', 'utf8');

async function check() {
  const events = [];
  const chips = {};
  let finish, requests = 0;
  const context = vm.createContext({
    Date, Number, AbortSignal, Error,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: {dispatchEvent(event) { events.push(event); }},
    profileChip: {}, gpuChip: {}, serviceChip: {},
    setChip(chip, value) { chips[chip === context.gpuChip ? 'gpu' : chip === context.serviceChip ? 'service' : 'profile'] = value; },
    fetch() { requests++; return new Promise(resolve => { finish = resolve; }); },
  });
  vm.runInContext(shell.slice(shell.indexOf('  let runtimePending'), shell.indexOf('  modelTrigger.addEventListener')), context);
  const refresh = context.window.ariadneRuntime.refresh;
  const first = refresh(), second = refresh();
  assert.equal(first, second, 'overlapping consumers share one request');
  assert.equal(requests, 1);
  const payload = {timestamp: new Date().toISOString(), gpu: {available: true, used_gb: 14, total_gb: 16, free_gb: 2}, memory: {available: true}, model_memory: {available: true}};
  finish({ok: true, json: async () => payload});
  await first;
  assert.equal(events.length, 1);
  assert.equal(events[0].detail, context.window.ariadneRuntime.snapshot);
  assert.equal(chips.gpu, 'NONE · 2 GB free');
  const stale = refresh();
  finish({ok: true, json: async () => ({...payload, timestamp: new Date(Date.now() - 60000).toISOString()})});
  await stale;
  assert.equal(context.window.ariadneRuntime.snapshot.gpu.available, false);
  assert.equal(context.window.ariadneRuntime.snapshot.model_memory.available, false);
  assert.match(chips.gpu, /Refreshing/);
  const failed = refresh();
  finish({ok: false, status: 503});
  await assert.rejects(failed, /503/);
  assert.equal(events.at(-1).type, 'ariadne:runtime-error');
  assert.equal(context.window.ariadneRuntime.snapshot, null, 'failed readings cannot be reused by new consumers');
  assert.equal(chips.gpu, 'Unavailable');
  const recovered = refresh();
  finish({ok: true, json: async () => payload});
  await recovered;
  assert.equal(context.window.ariadneRuntime.snapshot.gpu.available, true);

  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {textContent: '', style: {}, classList: {add() {}, remove() {}}, querySelector: child => node(selector + child)});
    return nodes.get(selector);
  };
  context.document = {querySelector: node};
  vm.runInContext(app.slice(app.indexOf('function renderGauge('), app.indexOf('function renderHostCapabilities(')), context);
  context.renderGauge('gpu', {...payload.gpu, used_percent: 87.5});
  context.renderModelMemory({available: true, loaded_vram_gb: 5.1, loaded: [{name: 'Qwen', size_vram: 5490081790}]});
  assert.equal(node('#gpu-used').textContent, '14 / 16 GB used');
  assert.equal(node('#gpu-model-memory').textContent, 'Model VRAM: 5.1 GB');
  context.renderModelMemory({available: true, loaded_vram_gb: 0, loaded: []});
  assert.equal(node('#gpu-model-memory').textContent, 'Model VRAM: 0 GB', 'unloaded models clear previous allocation');
  context.renderGauge('gpu', null);
  context.renderModelMemory(null);
  assert.equal(node('#gpu-used').textContent, 'Unavailable', 'failure clears old numeric reading');
  assert.equal(node('#gpu-model-memory').textContent, 'Model VRAM: unavailable');
  console.log('Runtime memory: shared requests, stale readings, failure/recovery and model allocation checks passed.');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
