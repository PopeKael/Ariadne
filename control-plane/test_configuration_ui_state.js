const test = require("node:test");
const assert = require("node:assert/strict");
const CleanupState = require("./configuration-state.js");

function source(path) { return {path, enabled: true}; }
function filing(name) { return {name, extensions: [`.${name.toLowerCase()}`], destination: `D:/Filed/${name}`, enabled: true}; }

test("source rows stay synchronized through add/remove, save, and reload", () => {
  let config = {sources: [source("D:/Downloads")], filing_classes: []};
  config = CleanupState.add(config, "sources", source(""));
  assert.equal(config.sources.length, 2);
  config = CleanupState.remove(config, "sources", 1);
  assert.equal(config.sources.length, 1);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    config = CleanupState.add(config, "sources", source(""));
    config = CleanupState.remove(config, "sources", 1);
    assert.equal(config.sources.length, 1);
  }
  config = CleanupState.add(config, "sources", source("D:/Drop-A"));
  config = CleanupState.add(config, "sources", source("D:/Drop-B"));
  config = CleanupState.add(config, "sources", source("D:/Drop-C"));
  assert.equal(config.sources.length, 4);
  config = CleanupState.remove(config, "sources", 2);
  assert.deepEqual(config.sources.map(item => item.path), ["D:/Downloads", "D:/Drop-A", "D:/Drop-C"]);
  assert.deepEqual(config.sources.map((_, index) => CleanupState.rowLabel("Folder", index)), ["Folder 1", "Folder 2", "Folder 3"]);
  const saved = JSON.stringify({plugins: {cleanup: config}});
  assert.equal(JSON.parse(saved).plugins.cleanup.sources.length, 3);
  const reloaded = CleanupState.snapshot({}, JSON.parse(saved).plugins.cleanup.sources, []);
  assert.deepEqual(reloaded.sources.map(item => item.path), ["D:/Downloads", "D:/Drop-A", "D:/Drop-C"]);
});

test("filing class rows use the same compact state operations", () => {
  let config = {sources: [], filing_classes: [filing("Markdown")]};
  for (let attempt = 0; attempt < 5; attempt += 1) {
    config = CleanupState.add(config, "filing_classes", filing("Temporary"));
    config = CleanupState.remove(config, "filing_classes", 1);
    assert.equal(config.filing_classes.length, 1);
  }
  config = CleanupState.add(config, "filing_classes", filing("Images"));
  config = CleanupState.add(config, "filing_classes", filing("Video"));
  config = CleanupState.add(config, "filing_classes", filing("Audio"));
  assert.equal(config.filing_classes.length, 4);
  config = CleanupState.remove(config, "filing_classes", 2);
  assert.deepEqual(config.filing_classes.map(item => item.name), ["Markdown", "Images", "Audio"]);
  assert.deepEqual(config.filing_classes.map((_, index) => CleanupState.rowLabel("Filing class", index)), ["Filing class 1", "Filing class 2", "Filing class 3"]);
});
