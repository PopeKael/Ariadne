// Run with Node: node control-plane/test_discovery_card_order.cjs
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync(path.join(__dirname, 'home.js'), 'utf8');
const render = source.slice(source.indexOf('function renderToday(items)'), source.indexOf('function renderAdaptive'));
const node = () => ({children: [], dataset: {}, append(child) { this.children.push(child); }});
let root, count;
const context = {
  document: {querySelector: id => id === '#today-list' ? root : count},
  el: node,
  renderSignalCard: item => ({dataset: {}, item}),
};
vm.createContext(context);
vm.runInContext(render, context);
function freshPage() {
  root = node();
  root.scrollTop = 80;
  root.scrollHeight = 1000;
  root.clientHeight = 300;
  root.querySelector = () => root.children[0];
  count = {};
}
const article = id => ({article_id: id, content_ready: 1});
freshPage();
context.renderToday(['a', 'b', 'c'].map(article));
const original = [...root.children[0].children];
context.renderToday(['d', 'c', 'b', 'a', 'd'].map(article));
assert.deepEqual(root.children[0].children.map(c => c.item.article_id), ['a', 'b', 'c', 'd']);
original.forEach((card, i) => assert.equal(root.children[0].children[i], card));
assert.equal(root.scrollTop, 80);
context.renderToday([article('d')]);
assert.equal(root.children[0].children.length, 4);
assert.equal(count.textContent, '4 cached articles');
freshPage();
context.renderToday(['d', 'c', 'b', 'a'].map(article));
assert.deepEqual(root.children[0].children.map(c => c.item.article_id), ['d', 'c', 'b', 'a']);
console.log('PASS: polling preserves cards and scroll, appends new articles once, and reload applies ranking.');
