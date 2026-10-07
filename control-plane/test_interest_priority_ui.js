const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function harness(request) {
  const source = fs.readFileSync(`${__dirname}/configuration.js`, "utf8");
  const listeners = {};
  const root = {innerHTML: "", addEventListener: (name, handler) => { listeners[name] = handler; }};
  const context = vm.createContext({
    document: {querySelector: selector => selector === "#interest-list" ? root : null},
    configurationJson: request,
    htmlEscape: value => String(value ?? ""),
    setStatus: () => {},
  });
  vm.runInContext(`let managedInterests = new Map(); const interestPriorityEdits = new Map();\n` +
    source.slice(source.indexOf("function renderAdaptiveManagement("), source.indexOf("async function loadAdaptiveManagement(")) +
    source.slice(source.indexOf("function interestMarkup("), source.indexOf("function ensureInterestVisible(")) +
    source.slice(source.indexOf('document.querySelector("#interest-list")?.addEventListener("input"'), source.indexOf("async function updateSource(")), context);
  const interest = {interest_id: "thailand", name: "Thailand", priority: 1, description: "Thai news", aliases: ["Bangkok"], enabled: true, semantic_enabled: true};
  context.interest = interest;
  vm.runInContext("renderAdaptiveManagement({interests: [interest]})", context);
  function slide(value) {
    const output = {};
    const button = {};
    const status = {};
    listeners.input({target: {value: String(value), dataset: {interestPriority: "thailand"}, matches: () => true,
      parentElement: {querySelector: () => output},
      closest: () => ({querySelector: selector => selector === "[data-interest-save]" ? button : status})}});
    return {output, button, status};
  }
  return {context, root, slide, run: code => vm.runInContext(code, context)};
}

test("slider keeps an unsaved priority through a stale management refresh and exposes Save", () => {
  let requests = 0;
  const h = harness(() => { requests++; });
  const controls = h.slide(5);
  assert.equal(controls.button.hidden, false);
  assert.equal(controls.output.textContent, "5");
  assert.equal(requests, 0);
  h.run("renderAdaptiveManagement({interests: [interest]})");
  assert.match(h.root.innerHTML, /value="5"/);
  assert.match(h.root.innerHTML, /Unsaved/);
});

test("Save preserves interest metadata and prevents duplicate pending submissions", async () => {
  let resolve;
  const sent = [];
  const h = harness((url, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise(done => { resolve = done; });
  });
  h.slide(5);
  const saving = h.run('saveInterestPriority("thailand")');
  await h.run('saveInterestPriority("thailand")');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].priority, 5);
  assert.deepEqual(sent[0].aliases, ["Bangkok"]);
  assert.equal(sent[0].description, "Thai news");
  assert.match(h.root.innerHTML, /Saving…/);
  resolve({interest: {...sent[0]}});
  await saving;
  assert.equal(h.run("interestPriorityEdits.size"), 0);
  h.run("renderAdaptiveManagement({interests: [...managedInterests.values()]})");
  assert.match(h.root.innerHTML, /value="5"/);
});

test("a timeout after a committed write is reconciled with the authoritative registry", async () => {
  const h = harness(async (url, options) => {
    if (options) throw new Error("Signal Service unavailable: timed out");
    return {interests: [{interest_id: "thailand", name: "Thailand", priority: 5}]};
  });
  h.slide(5);
  await h.run('saveInterestPriority("thailand")');
  assert.equal(h.run("interestPriorityEdits.size"), 0);
  assert.equal(h.run('managedInterests.get("thailand").priority'), 5);
});

test("an unconfirmed save retains the draft and makes retry available", async () => {
  const h = harness(async (url, options) => {
    if (options) throw new Error("Signal Service unavailable: timed out");
    return {interests: [{interest_id: "thailand", priority: 1}]};
  });
  h.slide(5);
  await h.run('saveInterestPriority("thailand")');
  assert.equal(h.run('interestPriorityEdits.get("thailand").priority'), 5);
  assert.equal(h.run('interestPriorityEdits.get("thailand").saving'), false);
  assert.match(h.root.innerHTML, /value="5"/);
  assert.match(h.root.innerHTML, /Save not confirmed/);
});
