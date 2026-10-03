const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(__dirname + "/startup-telemetry.js", "utf8");

test("reports the active instance only after two paint opportunities", async () => {
  const calls = [], frames = [];
  const root = {performance: {now: () => 123}, requestAnimationFrame: fn => frames.push(fn),
    fetch: async (path, options) => {
      calls.push({path, options});
      return {ok: true, json: async () => ({enabled: true, instance_id: "instance"})};
    }};
  vm.runInNewContext(source, {window: root, Date});
  const done = root.AriadneStartup.report("home");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  frames.shift()();
  assert.equal(calls.length, 1);
  frames.shift()();
  await done;
  const body = JSON.parse(calls[1].options.body);
  assert.equal(body.instance_id, "instance");
  assert.equal(body.navigation_elapsed_ms, 123);
  assert.equal(body.surface, "home");
});

test("telemetry failure never rejects page initialization", async () => {
  const root = {fetch: async () => {throw new Error("offline");}};
  vm.runInNewContext(source, {window: root, Date});
  await root.AriadneStartup.report("chat");
});

test("both entry points load the observer before Home code", () => {
  for (const page of ["home.html", "chat.html"]) {
    const html = fs.readFileSync(__dirname + "/" + page, "utf8");
    assert.ok(html.indexOf('/startup-telemetry.js') < html.indexOf('/home.js'));
  }
});
