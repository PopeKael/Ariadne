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
      HOME_REQUEST_TIMEOUT_MS: 240000, AbortController,
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
