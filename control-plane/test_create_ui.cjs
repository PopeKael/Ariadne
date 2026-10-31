const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent: '', value: '', disabled: false,
    replaceChildren(...children) { this.children = children; },
    addEventListener() {},
  });
  return elements.get(id);
}
const storage = new Map([['ariadne.music-provider', 'ariadne-local']]);
const source = fs.readFileSync(__dirname + '/create.js', 'utf8');
const createHtml = fs.readFileSync(__dirname + '/create.html', 'utf8');
const panelCount = (createHtml.match(/<article class="production-card\b/g) || []).length;
assert.equal(panelCount, 4, 'Create must present all four media panels');
for (const title of ['Music Engine', 'Image', 'Video', 'Speech Studio']) {
  assert.ok(createHtml.includes(`<h3>${title}</h3>`), `Create is missing its ${title} panel`);
}
assert.match(createHtml, /id="speech-open"[^>]*href="\/speech"/);
const context = {
  document: { querySelector: element, createElement: () => ({}) },
  window: {
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    addEventListener() {}, setInterval() {},
  },
  fetch: () => new Promise(() => {}),
};
vm.runInNewContext(source.slice(0, source.indexOf('  window.addEventListener')) +
  '\nglobalThis.loadMusicStatus = loadMusicStatus;\n})();', context);

(async () => {
  context.fetch = async () => { throw new Error('network unavailable'); };
  await context.loadMusicStatus();
  assert.equal(element('#music-state').textContent, 'unknown');
  assert.equal(element('#music-start').disabled, true);
  assert.match(element('#music-feedback').textContent, /network unavailable/);
  assert.equal(storage.get('ariadne.music-provider'), 'ariadne-local');
  context.fetch = async () => ({ ok: true, json: async () => ({ providers: [
    { id: 'ariadne-local', label: 'Local', state: 'ready', launchable: true },
  ] }) });
  await context.loadMusicStatus();
  assert.equal(element('#music-state').textContent, 'ready');
  assert.equal(element('#music-start').disabled, false);
  console.log('Create exposes all four media panels; Music status fails closed and recovers without losing the selected provider.');
})().catch(error => { console.error(error); process.exitCode = 1; });
