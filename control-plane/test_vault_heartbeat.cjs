const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const body = source.slice(source.indexOf('function activateVaultSession('), source.indexOf('async function startVaultSession('));

async function check() {
  let heartbeat;
  let failure;
  const lost = [];
  const closed = [];
  const context = vm.createContext({
    vaultHeartbeat: null, vaultSessionId: null, vaultHeartbeatInFlight: false,
    document: {querySelector: () => null},
    clearInterval() {}, setInterval(fn) { heartbeat = fn; return 1; },
    vaultSessionLabel() {}, setVaultControlsDisabled() {}, updateSessionButtons() {},
    postJson: async (url, data) => {
      if (url.endsWith('/close')) { closed.push(data.session_id); return {}; }
      if (failure) throw failure;
      return {ok: true};
    },
    markVaultSessionLost(message) { lost.push(message); context.vaultSessionId = null; },
  });
  vm.runInContext(body, context);
  context.activateVaultSession({session_id: 'owner', heartbeat_seconds: 5}, 'knowledge-vault');
  failure = new Error('temporary network failure');
  await heartbeat();
  assert.equal(context.vaultSessionId, 'owner');
  assert.equal(lost.length, 0);
  failure = null;
  await heartbeat();
  failure = new Error('persistent network failure');
  await heartbeat(); await heartbeat();
  assert.equal(lost.length, 0, 'successful heartbeat resets the failure count');
  await heartbeat();
  assert.equal(lost.length, 1);
  assert.deepEqual(closed, ['owner']);
  context.activateVaultSession({session_id: 'expired'}, 'knowledge-vault');
  failure = Object.assign(new Error('not active'), {status: 404});
  await heartbeat();
  assert.equal(lost.length, 2);
  assert.deepEqual(closed, ['owner', 'expired']);
  console.log('Vault heartbeat: transient recovery, persistent cleanup and expired-session checks passed.');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
