const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('Chat renders arriving chunks and replaces the draft with the checked final answer', async () => {
  const source = fs.readFileSync(require.resolve('./home.js'), 'utf8');
  for (const corrected of [false, true]) {
    const pieces = ['A provisional ', 'claim. [Live Source 1]'];
    const draft = pieces.join('');
    const checked = corrected ? 'No current live sources were supplied for this turn.' : draft;
    const nodes = {
      '#ask-input': {value: 'Research this article.', focus() {}},
      '#ask-submit': {}, '#ask-status': {}, '#knowledge-mode': {value: 'all'}
    };
    const displayed = [];
    const state = {sessionId: 'session', chatId: 'chat', messages: [],
      signalArticleBusy: new Set(), selectedToolIds: new Set(['external-research']),
      articleTldrPending: false, contextMutationInFlight: false};
    let body;
    const context = vm.createContext({state, CHAT_PAGE: true, currentFocus: null,
      ARTICLE_FETCH_WATCHDOG_MS: 240000, AbortController,
      document: {querySelector: selector => nodes[selector],
        body: {classList: {add() {}}}, documentElement: {scrollHeight: 1000}},
      window: {innerHeight: 1000, scrollY: 0, scrollTo() {}, setTimeout() {return 1;}, clearTimeout() {}},
      loadingSourceArticles: () => [], scrollChatToLatest() {}, showChatActivity() {},
      beginRequestStatus() {}, endRequestStatus() {}, finishChatActivity() {},
      pushChatActivity() {}, showFocus() {}, loadHome() {}, loadRecentChats() {},
      setContextMutationState: value => {state.contextMutationInFlight = value;},
      renderMarkdown: text => text, formatTiming: () => '',
      addMessage(role, text) {
        const message = {role, text, removed: false, remove() {this.removed = true;}};
        displayed.push(message);
        if (role === 'assistant' && !text) {
          body = {innerHTML: ''};
          message.querySelector = () => body;
        }
        return message;
      },
      async streamHomeChat(payload, onEvent) {
        assert.equal(payload.message, 'Research this article.');
        for (let index = 0; index < pieces.length; index++) {
          onEvent({type: 'delta', text: pieces[index]});
          assert.equal(body.innerHTML, pieces.slice(0, index + 1).join(''));
          assert.equal(displayed[1].removed, false);
        }
        onEvent({type: 'final', answer: checked});
        return {answer: checked, timing: {context_recipe: {generation: {source_guard_rejected: corrected}}}};
      }
    });
    vm.runInContext(source.slice(source.indexOf('async function ask(event)'),
      source.indexOf('async function loadChatHealth()')), context);
    await vm.runInContext('ask({preventDefault() {}})', context);
    assert.equal(displayed[1].removed, true);
    assert.equal(displayed[2].text, checked);
    assert.equal(state.messages[1].content, checked);
    assert.equal(nodes['#ask-status'].textContent, 'Complete.');
    assert.equal(nodes['#ask-submit'].disabled, false);
  }
});

test('Closed coding workbench reopens from the edge tab and on hover', () => {
  const source = fs.readFileSync(require.resolve('./home.js'), 'utf8');
  const start = source.indexOf('const workbenchToggle =');
  const end = source.indexOf('const webSearchToggle =', start);
  assert.notEqual(start, -1); assert.notEqual(end, -1);
  class Button {
    constructor() { this.hidden = false; this.attributes = {}; this.handlers = {}; }
    getAttribute(name) { return this.attributes[name] ?? null; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    focus() { this.focused = true; }
    fire(name) { this.handlers[name]?.({}); }
  }
  const elements = Object.fromEntries([
    '#workbench-toggle', '#workbench-close', '#workbench-reopen',
    '#diagnostics-toggle', '#diagnostics-close', '#workbench', '#diagnostics-drawer'
  ].map(selector => [selector, new Button()]));
  elements['#workbench-toggle'].attributes['aria-expanded'] = 'false';
  elements['#diagnostics-toggle'].attributes['aria-expanded'] = 'false';
  const classes = new Set();
  const context = vm.createContext({
    document: {body: {classList: {
      toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); }
    }}, querySelector: selector => elements[selector]},
    window: {addEventListener() {}},
    addEventListener() {}
  });
  vm.runInContext(source.slice(start, end), context);
  vm.runInContext('window.setCodingWorkbenchAvailable(true)', context);
  assert.equal(elements['#workbench-reopen'].hidden, false);
  elements['#workbench-reopen'].fire('mouseenter');
  assert.equal(elements['#workbench'].hidden, false);
  assert.equal(elements['#workbench-reopen'].hidden, true);
  elements['#workbench-close'].fire('click');
  assert.equal(elements['#workbench'].hidden, true);
  assert.equal(elements['#workbench-reopen'].hidden, false);
});
