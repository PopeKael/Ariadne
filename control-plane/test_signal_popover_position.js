const test = require("node:test");
const assert = require("node:assert/strict");
const positionPopover = require("./signal-popover-position.js");

const viewport = {width: 1200, height: 800};
const popover = {width: 320, height: 220};

function assertInsideViewport(position) {
  assert.ok(position.left >= 12);
  assert.ok(position.left + position.width <= viewport.width - 12);
  assert.ok(position.top >= 12);
  assert.ok(position.top + popover.height <= viewport.height - 12);
}

test("top-row card never falls back to page top-left coordinates", () => {
  const position = positionPopover({left: 600, top: 140, width: 20, bottom: 160}, popover, viewport);
  assert.equal(position.placement, "below-left");
  assert.equal(position.top, 168);
  assert.notEqual(position.left, 0);
  assert.notEqual(position.top, 0);
  assertInsideViewport(position);
});

test("bottom-row card stays adjacent and flips above-left", () => {
  const position = positionPopover({left: 600, top: 680, width: 20, bottom: 700}, popover, viewport);
  assert.equal(position.placement, "above-left");
  assert.equal(position.top, 452);
  assert.equal(position.left + position.width, 612);
  assertInsideViewport(position);
});

test("right-edge card shifts left inside the viewport", () => {
  const position = positionPopover({left: 1170, top: 140, width: 20, bottom: 160}, popover, viewport);
  assert.equal(position.left, 862);
  assert.equal(position.placement, "below-left");
  assertInsideViewport(position);
});

test("position is clamped when neither side has full natural space", () => {
  const position = positionPopover({left: 600, top: 390, width: 20, bottom: 410}, {width: 500, height: 780}, viewport);
  assert.equal(position.placement, "clamped-left");
  assert.equal(position.top, 12);
  assert.equal(position.left, 112);
  assert.equal(position.maxHeight, 776);
});

test("left-edge card flips horizontally without losing the anchor", () => {
  const position = positionPopover({left: 16, top: 500, width: 20, right: 36, bottom: 520}, popover, viewport);
  assert.equal(position.placement, "above-right");
  assert.equal(position.left, 44);
  assert.equal(position.top + popover.height, 492);
  assertInsideViewport(position);
});
