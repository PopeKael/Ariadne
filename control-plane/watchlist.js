(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const link = value => {try {const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? escape(url.href) : "#";} catch {return "#";}};
  const date = value => value ? new Date(value).toLocaleString([], {dateStyle:"medium", timeStyle:"short"}) : "Not yet";
  let watches = [], filter = "active", historyWatch = null, historyBefore = null, rendered = "";
  const editor = $("#watch-editor"), form = $("#watch-form");
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || `HTTP ${response.status}`);
    return result;
  }
  function evidence(items) {
    return `<ul class="evidence-list">${items.map(item => `<li><a href="${link(item.url)}" target="_blank" rel="noreferrer">${escape(item.title)}</a><p>${escape(item.snippet)}</p></li>`).join("")}</ul>`;
  }
  function render() {
    rendered = JSON.stringify(watches);
    const active = watches.filter(w => w.state === "active");
    $("#active-count").textContent = active.length || "";
    $("#attention-count").textContent = active.filter(w => w.unread || w.reminder_due).length || "";
    const visible = watches.filter(w => filter === "attention" ? w.state === "active" && (w.unread || w.reminder_due) : w.state === filter);
    $("#watch-list").innerHTML = visible.length ? visible.map(w => {
      const badge = w.checking ? "Checking…" : w.last_error ? "Check needs attention" : w.reminder_due ? "Time to do this" : w.unread ? "Ready for a look" : w.state === "active" ? "Keeping a thread on it" : w.state;
      const next = w.reminder_due ? "Waiting for you to mark this done" : `Next ${w.kind === "reminder" ? "reminder" : "check"}: ${date(w.next_check)}`;
      const button = (action, text, extra="") => `<button data-id="${w.id}" data-action="${action}" ${extra}>${text}</button>`;
      return `<article class="watch-card ${w.unread || w.reminder_due ? "attention" : ""} ${w.last_error ? "error" : ""}">
        <div class="watch-card-top"><span class="watch-kind">${escape(w.kind)} · every ${w.interval_days} days</span><span class="watch-badge">${escape(badge)}</span></div>
        <h2>${escape(w.title)}</h2>${w.purpose ? `<p class="watch-purpose">${escape(w.purpose)}</p>` : ""}
        <p class="watch-summary">${escape(w.summary)}</p>${w.last_error ? `<p class="watch-error">${escape(w.last_error)}</p>` : ""}
        ${w.evidence.length ? `<details><summary>What came back · ${w.evidence.length} sources</summary>${evidence(w.evidence)}</details>` : ""}
        <details><summary>What we’re following</summary>${w.query ? `<p>Search: ${escape(w.query)}</p>` : ""}${w.sources.map(url => `<p><a href="${link(url)}" target="_blank" rel="noreferrer">${escape(url)}</a></p>`).join("")}${w.kind === "reminder" ? "A reminder for you; no web searches or system changes." : ""}</details>
        <p class="watch-timing">${escape(next)} · Last check: ${date(w.last_checked)}${w.last_error && w.last_success ? ` · Last successful: ${date(w.last_success)}` : ""}</p>
        <div class="watch-actions">${w.state === "active" ? (w.kind === "reminder" ? button("done","Mark done") : button("check","Check now",w.checking ? "disabled" : "")) : ""}${w.unread && !w.reminder_due ? button("acknowledge","Reviewed") : ""}${button("edit","Refine")}${button("history","History")}${w.state === "active" ? button("snooze","Snooze a week") + button("pause","Pause") : button("resume","Resume")}${w.state !== "archived" ? button("archive","Archive") : ""}</div>
      </article>`;
    }).join("") : `<div class="watch-empty"><h2>${filter === "active" ? "Give your curiosity somewhere to live." : "Nothing here at the moment."}</h2><p>${filter === "active" ? "Keep an eye on a project, follow a question, or leave yourself a reminder. It stays here until you decide otherwise." : "Your saved watches are still available in the other views."}</p>${filter === "active" ? '<div class="watch-examples"><button data-example="project">Follow a project</button><button data-example="topic">Watch a topic</button><button data-example="reminder">Every-four-weeks reminder</button></div>' : ""}</div>`;
  }
  async function load() {
    try {
      const result = await api("/api/watchlist");
      watches = result.watches;
      $("#github-budget").textContent = result.github_budget?.message || "";
      // Do not replace an open details panel on every background poll.
      const open = [...document.querySelectorAll(".watch-card details[open]")].map(el => [el.closest("article").querySelector("button[data-id]").dataset.id, [...el.parentNode.querySelectorAll("details")].indexOf(el)]);
      if (rendered !== JSON.stringify(watches)) {
        const focused = document.activeElement?.dataset;
        const focusId = focused?.id, focusAction = focused?.action;
        render();
        open.forEach(([id,index]) => {const card = document.querySelector(`button[data-id="${id}"]`)?.closest("article"); if (card) card.querySelectorAll("details")[index].open = true;});
        if (focusId && focusAction) document.querySelector(`button[data-id="${focusId}"][data-action="${focusAction}"]`)?.focus();
      }
      $("#scheduler-note").textContent = result.scheduler.detail + (result.scheduler.running ? "" : " Background checking is not running in this instance.") + (result.scheduler.error ? ` ${result.scheduler.error}` : "");
      $("#watch-status").textContent = `${result.attention ? `${result.attention} ${result.attention === 1 ? "watch needs" : "watches need"} a look.` : "You’re up to date."} ${watches.length} saved ${watches.length === 1 ? "watch" : "watches"}.`;
      window.dispatchEvent(new CustomEvent("ariadne:watchlist", {detail: result.attention}));
    } catch(error) {$("#watch-status").textContent = `Could not refresh: ${error.message}`;}
  }
  function updateKind() {
    const reminder = form.elements.kind.value === "reminder";
    $("#evidence-fields").hidden = reminder;
    $("#reminder-help").hidden = !reminder;
    $("#frequency-label").textContent = reminder ? "Remind me every (days)" : "Check every (days)";
  }
  function edit(watch) {
    form.reset();
    form.elements.id.value = watch?.id || "";
    for (const name of ["title","kind","purpose","query","interval_days"]) if (watch?.[name] !== undefined) form.elements[name].value = watch[name];
    form.elements.sources.value = (watch?.sources || []).join("\n");
    form.elements.kind.disabled = Boolean(watch?.id);
    form.elements.next_check.value = "";
    $("#editor-title").textContent = watch?.id ? "Refine this watch" : "What shall we keep an eye on?";
    $("#editor-status").textContent = "";
    updateKind(); editor.showModal();
  }
  const projectEditor = $('#project-editor'), projectForm = $('#project-form');
  function addProject() {
    projectForm.reset();
    $('#project-options').open = false;
    $('#project-status').textContent = '';
    projectEditor.showModal();
    projectForm.elements.repository.focus();
  }
  function projectLink(value) {
    let url;
    try { url = new URL(value.trim()); } catch { throw new Error('Paste a GitHub repository link, such as https://github.com/owner/project.'); }
    const parts = url.pathname.replace(/^\/+|\/+$/g, '').split('/');
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.username || url.password || url.port || url.search || url.hash || parts.length !== 2) {
      throw new Error('Use the main https://github.com/owner/project link, without a branch, issue or extra parameters.');
    }
    const name = parts.join('/').replace(/\.git$/, '');
    if (!/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\/[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(name)) throw new Error('That link needs a GitHub owner and repository name.');
    return {name, url:'https://github.com/' + name};
  }
  $('#add-watch').addEventListener('click', addProject);
  $('#other-watch').addEventListener('click', () => {projectEditor.close(); edit();});
  projectForm.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = projectForm.querySelector('[type="submit"]');
    if (submit.disabled) return;
    submit.disabled = true;
    $('#project-status').textContent = 'Saving your project…';
    try {
      const repository = projectLink(projectForm.elements.repository.value);
      const existing = watches.find(w => w.identity === 'github:' + repository.url.toLowerCase());
      const result = await api('/api/watchlist', {
        title:projectForm.elements.title.value.trim() || repository.name, kind:'project',
        sources:[repository.url], query:'', interval_days:Number(projectForm.elements.interval_days.value),
        purpose:'Follow releases, README and issues for local Windows use, RX 7800 XT/gfx1101/RDNA3 AMD support, 16 GB VRAM, offline use, disk requirements and a practical Garage Alchemy test.'
      });
      projectEditor.close();
      filter = result.watch.state;
      document.querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === filter)));
      rendered = ''; await load();
      $('#watch-status').textContent = existing
        ? `${result.watch.title} is already saved (${result.watch.state}); its settings were kept.`
        : `Watching ${result.watch.title}. First check queued; then every ${result.watch.interval_days} days. Use Refine to change settings later.`;
    } catch(error) {$('#project-status').textContent = error.message;}
    finally {submit.disabled = false;}
  });
  $("#check-all").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api('/api/watchlist/check-all', {});
      await load();
      $('#watch-status').textContent = `${result.queued} ${result.queued === 1 ? 'watch queued' : 'watches queued'} for checking now.${result.checking ? ` ${result.checking} already checking.` : ''} Reminders keep their schedule.`;
    } catch (error) {$('#watch-status').textContent = `Could not queue checks: ${error.message}`;}
    finally {button.disabled = false;}
  });
  form.elements.kind.addEventListener("change", updateKind);
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));
  document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach(b => b.setAttribute("aria-pressed",String(b === button)));
    render();
  }));
  form.addEventListener("submit", async event => {
    event.preventDefault(); const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
    try {
      const body = Object.fromEntries(new FormData(form));
      body.kind = form.elements.kind.value;
      body.interval_days = Number(body.interval_days);
      body.sources = body.kind === "reminder" ? [] : body.sources.split("\n").map(s => s.trim()).filter(Boolean);
      if (body.kind === "reminder") body.query = "";
      if (body.next_check) body.next_check = new Date(body.next_check).toISOString(); else delete body.next_check;
      if (!body.id) delete body.id;
      await api("/api/watchlist", body); editor.close(); await load();
    } catch(error) {$("#editor-status").textContent = error.message;} finally {submit.disabled = false;}
  });
  async function history(older=false) {
    const result = await api(`/api/watchlist/${historyWatch}/history${older && historyBefore ? `?before=${historyBefore}` : ""}`);
    const html = result.checks.map(c => `<section class="history-entry"><time>${date(c.checked_at)} · ${escape(c.status)}</time><h3>${escape(c.summary)}</h3>${c.error ? `<p class="watch-error">${escape(c.error)}</p>` : ""}${evidence(c.evidence || [])}</section>`).join("") || "<p>No checks yet.</p>";
    if (older) $("#history-list").insertAdjacentHTML("beforeend",html); else $("#history-list").innerHTML = html;
    historyBefore = result.before; $("#older-checks").hidden = !historyBefore;
  }
  $("#older-checks").addEventListener("click", () => history(true).catch(error => $("#watch-status").textContent = error.message));
  $("#watch-list").addEventListener("click", async event => {
    const button = event.target.closest("button"); if (!button) return;
    if (button.dataset.example) {
      const kind = button.dataset.example;
      if (kind === 'project') {addProject(); return;}
      edit(kind === "reminder" ? {kind,title:"Review Windows update schedule",purpose:"Review and renew my chosen Windows update schedule.",interval_days:28} : {kind}); return;
    }
    const {id,action} = button.dataset; if (!id) return;
    if (action === "edit") {edit(watches.find(w => w.id === id)); return;}
    button.disabled = true;
    try {
      if (action === "history") {historyWatch = id; await history(); $("#watch-history").showModal();}
      else {await api(`/api/watchlist/${id}/action`,{action,days:7}); await load();}
    } catch(error) {$("#watch-status").textContent = error.message;} finally {button.disabled = false;}
  });
  $("#import-topics").addEventListener("click", async event => {
    event.target.disabled = true;
    try {const result = await api("/api/watchlist/import-news-topics",{}); await load(); $("#watch-status").textContent = `${result.imported} news topics are available for follow-up. Existing decisions were kept.`;}
    catch(error) {$("#watch-status").textContent = error.message;} finally {event.target.disabled = false;}
  });
  load(); window.setInterval(() => {if (!document.hidden && !document.querySelector("dialog[open]")) load();},15000);
})();
