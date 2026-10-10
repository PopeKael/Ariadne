const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function check() {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {textContent: '', innerHTML: '', dataset: {}, disabled: false, attributes: {}, handlers: {},
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(event, callback) { this.handlers[event] = callback; },
      scrollIntoView() { this.scrolled = true; }});
    return nodes.get(selector);
  }
  let nextPoll, state = 'running', runCount = 0, browseCount = 0, checkCount = 0;
  const oldPage = {ok: true, has_result: true, result: {completed_at: new Date().toISOString(), results: [], exhausted: false, page:1, pages:2, total:12}};
  const context = vm.createContext({Date, Number, String, Map, Boolean, encodeURIComponent, JSON,
    document: {querySelector: node},
    window: {addEventListener() {}, setInterval() { return 1; }, setTimeout(callback) { nextPoll = callback; return 1; }},
    fetch: async url => {
      let payload;
      if (url === '/api/watchlist') payload = {watches: []};
      else if (url === '/api/rabbit-hole/result') payload = oldPage;
      else if (url === '/api/session/start') payload = {session_id: 'test', heartbeat_seconds: 5};
      else if (url === '/api/plugins/rabbit-hole/run') { runCount++; payload = {job_id: 'job'}; }
      else if (url === '/api/rabbit-hole/library') { browseCount++; oldPage.result.page = 2; payload = {ok:true}; }
      else if (url === '/api/rabbit-hole/check') { checkCount++; payload = {job_id:'nomination'}; }
      else if (url.startsWith('/api/vault/jobs/')) payload = {job: {state, message: state === 'running' ? 'Reading sources for 16 unseen projects…' : state === 'error' ? 'Safety cap reached; your previous page was kept.' : '7 projects ready.'}};
      else throw new Error('Unexpected request: ' + url);
      return {ok: true, json: async () => payload};
    },
  });
  vm.runInContext(fs.readFileSync(__dirname + '/rabbit-hole.js', 'utf8'), context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  node('#rabbit-next').handlers.click();
  await settle();
  assert.equal(browseCount, 1);
  assert.equal(runCount, 0, 'browsing never starts discovery');
  assert.match(node('#rabbit-next').textContent, /Back to first/);
  node('#rabbit-explore').handlers.click();
  assert.equal(node('#rabbit-next').disabled, true);
  assert.match(node('#rabbit-next').textContent, /Working/);
  assert.match(node('#rabbit-page-status').textContent, /minute or two/);
  await settle();
  assert.match(node('#rabbit-page-status').textContent, /Reading sources/);
  node('#rabbit-explore').handlers.click();
  await settle();
  assert.equal(runCount, 1, 'duplicate clicks cannot start another job');
  state = 'complete';
  oldPage.result.warnings = ['3 source checks deferred by the safety cap.'];
  await nextPoll();
  assert.equal(node('#rabbit-next').disabled, false);
  assert.equal(node('#rabbit-next').attributes['aria-busy'], 'false');
  assert.match(node('#rabbit-page-status').textContent, /7 projects ready/);
  assert.match(node('#rabbit-page-status').textContent, /safety cap/);
  assert.equal(node('#shortlist-heading').scrolled, true);
  state = 'error';
  node('#rabbit-explore').handlers.click();
  await settle();
  assert.equal(node('#rabbit-next').disabled, false);
  assert.match(node('#rabbit-page-status').textContent, /previous page was kept/);
  state = 'running';
  node('#rabbit-project-url').value = 'https://github.com/example/suggested';
  node('#rabbit-check-form').handlers.submit({preventDefault(){}});
  await settle();
  assert.equal(checkCount, 1);
  assert.equal(node('#rabbit-check').disabled, true);
  console.log('Rabbit Hole paging UI: visible progress, duplicate-click guard, completion/scroll and quota-error feedback passed.');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
