/* Chat's review and verification surface for Qwen Code workspaces. */
(() => {
  const root = document.querySelector("#workbench-content");
  if (!root) return;
  const tabs = ["Files", "Output", "Tests", "Preview", "Verify", "Details"];
  let job = null, selectedTab = "Output", treePath = "", selectedFile = "";
  let outputText = "", liveTests = [], toolRows = new Map(), previewToken = "", previewFrame = null;
  let verification = null, previewMessage = "Preview not loaded yet.";
  const make = (tag, text = "", className = "") => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const setStatus = text => {
    const status = document.querySelector("#ask-status");
    if (status) status.textContent = text;
  };
  const runnerReady = result => result?.status === "SUCCESS"
    && result.verification?.passed === true
    && !(result.tests || []).some(item => item?.result === "failed");
  const request = async url => {
    const response = await fetch(url, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
    return payload;
  };
  const workspaceUrl = (action, path = "") => `/api/coding-agent/workspace?${new URLSearchParams({
    run_id: job.run_id, action, path,
  })}`;
  function openPanel() {
    if (typeof window.setWorkbenchOpen === "function") window.setWorkbenchOpen(true);
  }
  function appendOutput(line) {
    outputText += String(line || "");
    if (outputText.length > 350_000) outputText = outputText.slice(-350_000);
    const output = root.querySelector("[data-coding-output]");
    if (output) { output.textContent = outputText || "Waiting for Qwen Code output…"; output.scrollTop = output.scrollHeight; }
  }
  function shellCommand(event) {
    const message = event?.message || {};
    const blocks = Array.isArray(message.content) ? message.content : [];
    return blocks.filter(block => block?.type === "tool_use").map(block => ({
      id: block.id, name: block.name, input: block.input || {},
    }));
  }
  function registerLiveTests(event) {
    for (const call of shellCommand(event)) {
      const name = String(call.name || "").toLowerCase();
      const command = String(call.input?.command || call.input?.cmd || "");
      if (!["run_shell_command", "shell", "execute"].includes(name)
        || !/\b(test|pytest|unittest|cargo\s+test|npm\s+(?:run\s+)?test|vitest|jest|go\s+test|mvn\s+test)\b/i.test(command)) continue;
      const row = {id: call.id, command, result: "running", output: ""};
      liveTests.push(row);
      if (call.id) toolRows.set(call.id, row);
      renderTests();
    }
  }
  function resolveToolResults(event) {
    const blocks = Array.isArray(event?.message?.content) ? event.message.content : [];
    for (const block of blocks) {
      if (block?.type !== "tool_result") continue;
      const row = toolRows.get(block.tool_use_id);
      if (!row) continue;
      row.output = String(block.content || "").slice(-5000);
      row.result = block.is_error || /\b(failed|error|exit code [1-9])\b/i.test(row.output) ? "failed" : "finished";
      renderTests();
    }
  }
  function renderTests() {
    const panel = root.querySelector("[data-coding-tests]");
    if (!panel) return;
    panel.replaceChildren();
    const resultTests = Array.isArray(job?.result?.tests) ? job.result.tests : [];
    const rows = resultTests.length ? resultTests : liveTests;
    if (!rows.length) panel.append(make("p", "Qwen Code has not reported a test command yet."));
    for (const item of rows) {
      const card = make("section", "", "coding-job-test");
      card.append(make("strong", item.result || "pending"), make("code", item.command || ""));
      if (item.output) card.append(make("pre", item.output));
      panel.append(card);
    }
  }
  async function loadFiles(path = "") {
    treePath = path;
    const target = root.querySelector("[data-coding-files]");
    if (!target) return;
    target.replaceChildren(make("p", "Reading workspace…"));
    try {
      const listing = await request(workspaceUrl("list", path));
      target.replaceChildren();
      const heading = make("div", `${listing.workspace}${listing.path ? ` · ${listing.path}` : ""}`, "coding-job-workspace-path");
      const refresh = make("button", "Refresh files", "coding-job-button");
      refresh.type = "button"; refresh.addEventListener("click", () => void loadFiles(path));
      target.append(heading, refresh);
      if (path) {
        const parent = path.split("/").slice(0, -1).join("/");
        const up = make("button", "↑ Parent", "coding-job-button");
        up.type = "button"; up.addEventListener("click", () => void loadFiles(parent)); target.append(up);
      }
      const columns = make("div", "", "coding-job-files");
      const list = make("nav", "", "coding-job-file-list"); list.setAttribute("aria-label", "Workspace files");
      const content = make("pre", "Select a file to inspect it.", "coding-job-source");
      for (const entry of listing.entries || []) {
        const button = make("button", `${entry.kind === "directory" ? "▸ " : ""}${entry.name}`, "coding-job-file");
        button.type = "button"; button.title = entry.path;
        button.addEventListener("click", () => {
          if (entry.kind === "directory") { void loadFiles(entry.path); return; }
          selectedFile = entry.path;
          content.textContent = "Loading file…";
          void request(workspaceUrl("read", entry.path)).then(value => {
            content.textContent = value.content + (value.truncated ? "\n\n[Viewer clipped this file after 1 MB.]" : "");
          }).catch(error => { content.textContent = error.message; });
        });
        list.append(button);
      }
      columns.append(list, content); target.append(columns);
      if (selectedFile && (listing.entries || []).some(entry => entry.path === selectedFile)) {
        const current = (listing.entries || []).find(entry => entry.path === selectedFile);
        if (current?.kind === "file") {
          const value = await request(workspaceUrl("read", selectedFile));
          content.textContent = value.content + (value.truncated ? "\n\n[Viewer clipped this file after 1 MB.]" : "");
        }
      }
    } catch (error) { target.replaceChildren(make("p", error.message, "coding-job-error")); }
  }
  function showOutput() {
    const panel = root.querySelector("[data-coding-output-panel]");
    if (!panel) return;
    panel.replaceChildren();
    const output = make("pre", outputText || "Waiting for Qwen Code output…", "coding-job-output");
    output.dataset.codingOutput = "";
    panel.append(output);
    output.scrollTop = output.scrollHeight;
  }
  async function showPreview() {
    const panel = root.querySelector("[data-coding-preview]");
    if (!panel || !job) return;
    panel.replaceChildren();
    const toolbar = make("div", "", "coding-job-toolbar");
    const refresh = make("button", "Reload preview", "coding-job-button");
    const verify = make("button", "Run interactive checks", "coding-job-button");
    const launch = make("button", "Open full-screen Runner", "coding-job-button");
    refresh.type = verify.type = launch.type = "button";
    verify.disabled = !job.result?.status; launch.disabled = !runnerReady(job.result);
    launch.title = runnerReady(job.result) ? "Open this verified workspace in the Rust Host-owned full-screen Runner." : "Finish the job, pass its tests, then pass interactive preview checks to enable Runner.";
    const state = make("span", previewMessage, "coding-job-preview-state");
    toolbar.append(refresh, verify, launch, state); panel.append(toolbar);
    const frame = document.createElement("iframe");
    frame.title = "Coding workspace preview";
    frame.className = "coding-job-preview-frame";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("allow", "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; usb 'none'; serial 'none'; display-capture 'none'");
    frame.referrerPolicy = "no-referrer";
    if (!("csp" in frame)) {
      state.textContent = "This browser cannot enforce the preview sandbox. Use current Chrome or Edge.";
      return;
    }
    const token = crypto.randomUUID().replaceAll("-", "");
    previewToken = token; previewFrame = frame;
    frame.csp = "default-src 'none'; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline' 'self'; img-src data: blob: 'self'; media-src data: blob: 'self'; font-src data: 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    const load = () => {
      frame.src = `/coding-preview/${encodeURIComponent(job.run_id)}/?token=${encodeURIComponent(token)}&reload=${Date.now()}`;
      state.textContent = "Loading project preview…";
    };
    refresh.addEventListener("click", load);
    verify.addEventListener("click", () => {
      if (frame.contentWindow) {
        state.textContent = "Exercising visible controls in the isolated preview…";
        frame.contentWindow.postMessage({type: "ariadne-coding-verify", token}, "*");
      }
    });
    launch.addEventListener("click", () => void launchRunner(launch, state));
    frame.addEventListener("load", () => {
      state.textContent = "Preview response loaded; waiting for its sandbox checks to report.";
    });
    panel.append(frame);
    const evidence = make("section", "", "coding-job-verification");
    if (verification) renderVerification(evidence, verification);
    else evidence.append(make("p", "Interact with the preview, then run the isolated control checks."));
    panel.append(evidence);
    load();
  }
  function renderVerification(container, report) {
    container.replaceChildren();
    if (!report) {
      container.append(make("p", "Use Preview to interact with the app, then run isolated checks that exercise visible buttons and selectors."));
      const preview = make("button", "Open Preview", "coding-job-button");
      preview.type = "button"; preview.addEventListener("click", () => { selectedTab = "Preview"; render(); });
      container.append(preview);
      return;
    }
    container.append(make("h4", report.passed ? "Interactive checks passed" : "Interactive checks need review"));
    container.append(make("p", `${report.actions?.length || 0} control action(s) · ${report.controls?.length || 0} visible control(s) · ${report.errors?.length || 0} runtime error(s)`));
    for (const error of report.errors || []) container.append(make("pre", error, "coding-job-error"));
    for (const action of report.actions || []) container.append(make("p", `${action.changed ? "Changed" : "No visible change"}: ${action.label || action.kind}`));
    for (const warning of report.warnings || []) container.append(make("p", warning, "coding-job-warning"));
  }
  async function launchRunner(button, status) {
    button.disabled = true; status.textContent = "Requesting full-screen Runner from the Rust Host…";
    try {
      const response = await fetch("/api/coding-agent/runner", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({run_id: job.run_id})});
      let current = await response.json();
      if (!response.ok) throw new Error(current.message || `HTTP ${response.status}`);
      for (let attempt = 0; attempt < 60; attempt += 1) {
        if (current.state === "READY" && typeof current.foreground === "boolean") {
          status.textContent = current.foreground ? "Runner is full screen and has keyboard focus; Alt+F4 closes it." : "Runner opened; use Alt+Tab to focus its Edge window.";
          return;
        }
        if (["FAILED", "CLOSED"].includes(current.state)) throw new Error(current.message || "Runner closed before it was ready.");
        await new Promise(resolve => setTimeout(resolve, 500));
        current = await request(`/api/coding-agent/runner/status?launch_id=${encodeURIComponent(current.launch_id)}`);
      }
      throw new Error("The Rust Host has not confirmed Runner readiness.");
    } catch (error) { status.textContent = `Runner: ${error.message}`; }
    finally { button.disabled = !runnerReady(job?.result); }
  }
  async function saveVerification(report) {
    try {
      const response = await fetch("/api/coding-agent/verification", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({run_id: job.run_id, report})});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      if (job.result) job.result = {...job.result, verification: payload.verification};
      const runner = root.querySelector(".coding-job-preview-state");
      if (runner) runner.textContent = payload.verification.passed
        ? "Interactive checks passed and saved as evidence. Runner is available."
        : "Interactive check report saved with issues; Runner stays disabled.";
      const launch = [...root.querySelectorAll(".coding-job-button")].find(button => button.textContent === "Open full-screen Runner");
      if (launch) launch.disabled = !runnerReady(job.result);
      const verifyPanel = root.querySelector("[data-coding-verification]");
      if (verifyPanel) renderVerification(verifyPanel, payload.verification);
      setStatus(payload.verification.passed ? "Coding preview checks passed." : "Coding preview checks found issues.");
    } catch (error) {
      const state = root.querySelector(".coding-job-preview-state");
      if (state) state.textContent = `Verification evidence could not be saved: ${error.message}`;
    }
  }
  function renderDetails(panel) {
    panel.replaceChildren();
    const result = job?.result || {};
    panel.append(make("p", `Run: ${job?.run_id || "starting"}`));
    panel.append(make("p", `Workspace: ${job?.workspace || "resolving"}`));
    panel.append(make("p", `State: ${result.status || job?.state || "RUNNING"}`));
    panel.append(make("p", `Model/context: ${result.model || job?.model || "—"} · ${result.context_tokens || job?.context_tokens || "—"} tokens`));
    panel.append(make("p", `Elapsed: ${result.elapsed_time_seconds ?? "running"}s · model calls: ${result.model_calls ?? "not reported"} · tool calls: ${result.tool_call_count ?? "not reported"}`));
    panel.append(make("p", `Changed files: ${(result.changed_files || []).join(", ") || "none reported yet"}`));
    for (const error of result.errors || []) panel.append(make("pre", error, "coding-job-error"));
    if (result.summary) panel.append(make("p", result.summary));
  }
  function render() {
    if (!job) return;
    const wrapper = make("section", "", "coding-job");
    wrapper.append(make("h3", "Coding job · Qwen Code"));
    wrapper.append(make("p", `${job.result?.status || job.state || "RUNNING"} · ${job.workspace || "workspace resolving"}`, "coding-job-summary"));
    const toolbar = make("div", "", "coding-job-tabs");
    const panels = {};
    for (const tab of tabs) {
      const button = make("button", tab, "coding-job-tab"); button.type = "button";
      button.setAttribute("aria-pressed", String(tab === selectedTab));
      button.addEventListener("click", () => { selectedTab = tab; render(); });
      toolbar.append(button);
      const panel = make("div", "", "coding-job-panel"); panel.hidden = tab !== selectedTab;
      panels[tab] = panel;
    }
    panels.Files.dataset.codingFiles = "";
    panels.Output.dataset.codingOutputPanel = "";
    panels.Tests.dataset.codingTests = "";
    panels.Preview.dataset.codingPreview = "";
    panels.Verify.dataset.codingVerification = "";
    wrapper.append(toolbar, ...tabs.map(tab => panels[tab]));
    root.replaceChildren(wrapper);
    showOutput(); renderTests(); renderDetails(panels.Details); renderVerification(panels.Verify, verification);
    if (selectedTab === "Files") void loadFiles(treePath);
    if (selectedTab === "Preview") void showPreview();
    openPanel();
  }
  function begin(started) {
    window.setCodingWorkbenchAvailable?.(true);
    const data = started?.job || started || {};
    job = {...data, state: "RUNNING", result: null};
    selectedTab = "Output"; outputText = "Qwen Code started. Waiting for tool and model output…\n";
    liveTests = []; toolRows = new Map(); verification = null; previewMessage = "Preview not loaded yet.";
    render();
    void loadFiles("");
  }
  function output(event) {
    if (!job) return;
    const line = String(event?.line || "");
    appendOutput(`[${event?.stream || "output"}] ${line}`);
    try {
      const parsed = JSON.parse(line);
      if (parsed.type === "assistant") registerLiveTests(parsed);
      if (parsed.type === "user") resolveToolResults(parsed);
    } catch (_) { /* Qwen Code may emit human-readable diagnostics between JSON events. */ }
  }
  function transcriptFor(result) {
    const parts = [];
    for (const call of result?.tool_calls || []) {
      parts.push(`TOOL ${call.name || "unknown"}\n${JSON.stringify(call.input || {}, null, 2)}`);
      if (call.result !== undefined) parts.push(String(call.result).slice(-12_000));
      if (call.is_error) parts.push("Tool reported an error.");
    }
    if (result?.summary) parts.push(`AGENT SUMMARY\n${result.summary}`);
    return parts.join("\n\n").slice(-350_000);
  }
  async function complete(result) {
    window.setCodingWorkbenchAvailable?.(true);
    if (!job && result?.run_id) job = {run_id: result.run_id, workspace: result.workspace};
    if (!job) return;
    job = {...job, ...result, result, state: result.status || "FAILED"};
    previewMessage = result.status === "SUCCESS" ? "Job complete. Preview is available." : "Job finished; preview is available for diagnosis.";
    appendOutput(`\n\n${result.status || "FINISHED"}: ${result.summary || "No summary supplied."}\n`);
    render();
    selectedTab = "Tests"; render();
    try {
      const detail = await request(workspaceUrlFor(result.run_id));
      if (detail.result) {
        job = {...job, ...detail, ...detail.result, result: detail.result};
        outputText = transcriptFor(detail.result) || outputText;
        renderTests();
        if (selectedTab === "Tests") render();
      }
    } catch (_) { /* Final status and streamed output remain visible if evidence cannot be reloaded. */ }
  }
  async function openSaved(result) {
    if (!result?.run_id) return;
    window.setCodingWorkbenchAvailable?.(true);
    const metadata = await request(workspaceUrlFor(result.run_id));
    const fullResult = metadata.result || result;
    job = {...metadata, ...fullResult, result: fullResult};
    selectedTab = "Files"; outputText = transcriptFor(fullResult) || fullResult.summary || "Completed Qwen Code job.";
    liveTests = Array.isArray(fullResult.tests) ? fullResult.tests : [];
    toolRows = new Map(); verification = null;
    render();
  }
  function workspaceUrlFor(runId) {
    return `/api/coding-agent/workspace?${new URLSearchParams({run_id: runId, action: "meta"})}`;
  }
  addEventListener("message", event => {
    if (!previewFrame || event.source !== previewFrame.contentWindow || event.origin !== "null") return;
    const message = event.data;
    if (message?.type !== "ariadne-coding-preview" || message.run_id !== job?.run_id || message.token !== previewToken) return;
    const payload = message.payload || {};
    if (message.kind === "loaded") {
      previewMessage = payload.usable ? `Loaded ${payload.title || "application"}; ${payload.controls?.length || 0} visible controls.` : "Page loaded, but no usable UI was detected.";
      const state = root.querySelector(".coding-job-preview-state"); if (state) state.textContent = previewMessage;
      const verify = [...root.querySelectorAll(".coding-job-button")].find(button => button.textContent === "Run interactive checks");
      if (verify) verify.disabled = !job.result?.status;
    } else if (message.kind === "runtime_error") {
      previewMessage = `Runtime error: ${payload.message || "unknown error"}`;
      const state = root.querySelector(".coding-job-preview-state"); if (state) state.textContent = previewMessage;
    } else if (message.kind === "verification") {
      verification = payload;
      root.querySelectorAll("[data-coding-verification],.coding-job-verification").forEach(container => renderVerification(container, payload));
      void saveVerification(payload);
    } else if (message.kind === "blocked_resource") {
      appendOutput(`[preview sandbox] blocked ${payload.directive || "resource"}: ${payload.resource || "unknown"}\n`);
    }
  });
  window.ariadneCodingJob = {begin, output, complete, open: openSaved};
})();
