const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('selection stays within its response, activates an anchor, and can be cleared', () => {
  const nodes = new Map();
  const node = () => ({listeners: {}, children: [], textContent: '',
    append(...children) { this.children.push(...children); },
    prepend(child) { this.children.unshift(child); nodes.set('#' + child.id, child); },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    remove() { nodes.delete('#' + this.id); },
    focus() { this.focused = true; }
  });
  nodes.set('#ask-form', node()); nodes.set('#ask-input', node()); nodes.set('#ask-status', node());
  const inside = {};
  const body = node();
  body.contains = target => target === inside;
  body.after = button => { body.button = button; };
  let selected = 'A selected passage';
  let endContainer = inside;
  const context = vm.createContext({CHAT_PAGE: true,
    document: {querySelector: selector => nodes.get(selector)},
    window: {getSelection: () => ({rangeCount: 1, getRangeAt: () => ({startContainer: inside, endContainer}), toString: () => selected})},
    el: (tag, className, text) => Object.assign(node(), {textContent: text || ''})
  });
  const source = fs.readFileSync(require.resolve('./home.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('let currentFocus ='), source.indexOf('function addMessage(')), context);
  context.body = body;
  vm.runInContext("wireSelectionFocus(body, {turn_id: 'turn-one'})", context);
  assert.equal(body.button.hidden, true);
  body.listeners.mouseup();
  assert.equal(body.button.hidden, false);
  body.button.listeners.click();
  assert.equal(vm.runInContext('currentFocus.source_turn_id', context), 'turn-one');
  assert.equal(vm.runInContext('currentFocus.text', context), selected);
  assert.equal(nodes.get('#ask-input').focused, true);
  nodes.get('#selection-focus').children[1].listeners.click();
  assert.equal(vm.runInContext('currentFocus', context), null);
  assert.equal(nodes.get('#ask-status').textContent, 'Focus cleared.');
  endContainer = {};
  body.listeners.mouseup();
  assert.equal(body.button.hidden, true);
  endContainer = inside; selected = 'x'.repeat(8001);
  body.listeners.mouseup();
  assert.equal(body.button.hidden, true);
});
