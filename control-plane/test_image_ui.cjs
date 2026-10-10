const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
function element(key) {
  if (!elements.has(key)) elements.set(key, {
    hidden: false, value: '', textContent: '',
    removeAttribute() {},
    querySelector(child) { return element(`${key} ${child}`); },
  });
  return elements.get(key);
}
const storage = new Map();
const source = fs.readFileSync(__dirname + '/image.js', 'utf8');
const context = {
  document: { querySelector: element },
  localStorage: { setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
  Date,
};
// Exercise the real rendering code with responses arriving out of order.
vm.runInNewContext(source.slice(0, source.indexOf('  window.addEventListener')) +
  '\nglobalThis.renderJob = renderJob;\n})();', context);
const completed = { id: 'job', state: 'completed', stage: 'Complete', result: {
  ok: true, message: 'Saved', model: 'SDXL', seed: 42,
  image: { url: '/api/sequence/projects/test/assets/image/content', width: 1280, height: 720 },
} };
context.renderJob(completed);
assert.equal(element('#image-preview-image').hidden, false);
assert.match(element('#image-preview-image').src, /content\?t=/);
context.renderJob({ id: 'job', state: 'running', stage: 'Decoding image' });
context.renderJob(completed);
assert.equal(element('#image-preview-image').hidden, false, 'stale runtime status must not hide the finished image');
assert.equal(element('#image-preview-empty').hidden, true);
assert.equal(element('#image-feedback').textContent, 'Saved');
console.log('Image completion survives stale status; project preview URL is valid.');

// Run normal page initialization twice with the same browser storage.
function page() {
  const fields = new Map();
  function field(key) {
    if (!fields.has(key)) fields.set(key, {
      value: '', listeners: {},
      addEventListener(event, fn) { this.listeners[event] = fn; },
    });
    return fields.get(key);
  }
  vm.runInNewContext(source, {
    document: { querySelector: field },
    localStorage: {
      getItem: k => storage.get(k) || null,
      setItem: (k, v) => storage.set(k, v),
      removeItem: k => storage.delete(k),
    },
    window: { addEventListener() {}, setInterval() {} },
    fetch: () => new Promise(() => {}),
  });
  return field;
}
const beforeRefresh = page();
for (const [id, value] of Object.entries({
  'image-prompt': 'Keep this cavern prompt', 'image-negative': 'blurry',
  'image-size': '1280x720', 'image-seed': '42',
})) beforeRefresh('#' + id).value = value;
beforeRefresh('#image-prompt').listeners.input();
const afterRefresh = page();
assert.equal(afterRefresh('#image-prompt').value, 'Keep this cavern prompt');
assert.equal(afterRefresh('#image-negative').value, 'blurry');
assert.equal(afterRefresh('#image-size').value, '1280x720');
assert.equal(afterRefresh('#image-seed').value, '42');
console.log('Prompt, negative prompt, size and seed survive page initialization.');
