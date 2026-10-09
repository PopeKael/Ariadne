/* The Lab is an explicit Chat mode; V1's only workflow is browser generation. */
(() => {
  const toggle = document.querySelector('#lab-toggle');
  if (!toggle) return;
  let enabled = false, busy = false, current = null, frame = null, token = '', timer = null;
  let previewState = 'NOT RUN', errors = [], generation = 0, persistence = Promise.resolve();
  let normalModel = '', normalContext = '';
  let workingSelection = null, buildContext = null;
  let activityTimer = null, activity = null, testFrame = null;
  const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; media-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; webrtc 'block'";
  const root = document.querySelector('#workbench-content');
  const status = document.querySelector('#ask-status');
  const make = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
  function open() { setWorkbenchOpen(true); }
  function startActivity() {
    clearInterval(activityTimer);
    activity={started:Date.now(),lastEvent:Date.now(),lastOutput:0,characters:0,stage:'LOADING MODEL'};
    root.replaceChildren(make('h3','The Lab'));
    const indicator=make('div','','lab-load-spinner'); indicator.setAttribute('aria-hidden','true');
    const heading=make('p','LOADING MODEL'), detail=make('pre','','lab-runtime');
    heading.setAttribute('role','status'); heading.setAttribute('aria-live','polite');
    root.append(indicator,heading,detail);
    const update=()=>{
      const now=Date.now(), age=Math.floor((now-activity.lastEvent)/1000), quiet=activity.lastOutput ? Math.floor((now-activity.lastOutput)/1000) : null;
      const generating=/GENERATING|REPAIRING|CHECKING REQUIREMENTS|UNDERSTAND|REVIEW|REPAIR$/.test(activity.stage);
      heading.textContent=generating && (quiet===null || quiet>=5) ? `${activity.stage} · WAITING FOR MODEL OUTPUT` : activity.stage;
      const elapsed=Math.floor((now-activity.started)/1000);
      detail.textContent=`Elapsed: ${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}\nOutput received: ${activity.characters.toLocaleString()} characters\nLatest output: ${quiet===null?'None yet':quiet+' seconds ago'}\nConnection: ${age<=15?'Active':'No recent heartbeat'} · last event ${age}s ago`;
    };
    update(); activityTimer=setInterval(update,1000);
  }
  async function testCandidate(event) {
    const checks=event.plan.checks.filter(c=>c.mode==='browser');
    const report={usable:false,smoke_passed:false,checks:[],errors:[]};
    // One fresh startup page and one fresh test script; assertions within that
    // script share state, like a conventional browser test.
    for(const index of (checks.length ? [-1,0] : [-1])) {
      const evidence=await new Promise(resolve=>{
        const test=document.createElement('iframe'); testFrame=test;
        test.title='Candidate verification'; test.setAttribute('sandbox','allow-scripts'); test.referrerPolicy='no-referrer';
        test.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; usb 'none'; serial 'none'; display-capture 'none'");
        test.className='lab-test-frame';
        let deadline;
        const finish=value=>{clearTimeout(deadline);removeEventListener('message',receive);test.remove();testFrame=null;resolve(value);};
        const receive=e=>{
          const d=e.data;
          if(e.source!==test.contentWindow || e.origin!=='null' || d?.type!=='code-lab-test' || d.token!==event.token || d.index!==index) return;
          if(d.kind==='running' && activity) activity.stage=index===-1?'RUNNING APPLICATION':'TESTING APPLICATION';
          if(d.kind==='result') finish(d.evidence);
        };
        addEventListener('message',receive);
        if(!('csp' in test)) {finish({passed:false,errors:['Browser cannot enforce sandbox CSP. Use current Chrome or Edge.']});return;}
        test.csp=csp;
        test.src=`/api/code-lab/preview?run_id=${event.run_id}&token=${event.token}&test=${index}`;
        root.append(test);
        deadline=setTimeout(()=>finish({passed:false,errors:['Test build did not initialise within 15 seconds.']}),15000);
      });
      report.errors.push(...(evidence.errors||[]));
      if(index===-1) Object.assign(report,{usable:evidence.usable===true,smoke_passed:evidence.smoke_passed===true,startup:evidence,controls:evidence.controls||[],warnings:evidence.warnings||[]});
      else report.checks.push(...(evidence.checks||[]));
      if((evidence.errors||[]).some(message=>/did not initialise|cannot enforce sandbox/.test(message))) {
        while(report.checks.length<checks.length) report.checks.push({passed:false,description:checks[report.checks.length].description,errors:['Not run: browser initialisation failed.']});
        break;
      }
    }
    const proved=new Set(report.checks.filter(c=>c.passed).flatMap(c=>(c.actions||[]).filter(a=>a.changed).map(a=>a.label)));
    report.errors=report.errors.filter(message=>!message.startsWith('Control produced no observable change: ') || !proved.has(message.slice('Control produced no observable change: '.length)));
    if(report.startup) report.smoke_passed=report.usable && !(report.startup.errors||[]).some(message=>!message.startsWith('Control produced no observable change: ') || !proved.has(message.slice('Control produced no observable change: '.length)));
    report.errors=report.errors.slice(0,100);
    const response=await fetch('/api/code-lab/verification',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:event.run_id,token:event.token,report})});
    if(!response.ok) throw Error((await response.json()).message||'Verification evidence could not be saved.');
  }
  function dispose() {
    clearTimeout(timer); frame?.remove(); frame = null; token = '';
  }
  async function persist() {
    if (!current?.code) return;
    const payload = {run_id: current.run.run_id, result: previewState, errors: [...errors]};
    const runId = payload.run_id;
    persistence = persistence.catch(() => {}).then(async () => {
      const response = await fetch('/api/code-lab/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      const value = await response.json();
      if (!response.ok) throw new Error(value.message || 'Preview record could not be saved.');
      if (current?.run.run_id === runId) { current.run = value.run; refreshMetrics(); }
    });
    try { await persistence; } catch (error) { status.textContent = error.message; }
  }
  function refreshMetrics() {
    const target = root.querySelector('[data-lab-metrics]');
    const runtime = root.querySelector('[data-lab-runtime]');
    if (runtime) runtime.textContent = `Preview: ${previewState} · ${errors.length} runtime error(s)${errors.length ? '\n' + errors.join('\n') : ''}`;
    if (!target || !current) return;
    target.replaceChildren();
    const r = current.run, t = r.telemetry || {}, h = r.hardware || {};
    const number = (v, suffix = '') => typeof v === 'number' ? `${v.toLocaleString(undefined, {maximumFractionDigits: 2})}${suffix}` : 'Unavailable';
    const seconds = v => number(typeof v === 'number' ? v / 1e9 : null, ' s');
    const rows = [
      ['Model', r.model], ['Context', number(r.context_tokens, ' tokens')],
      ['Input', number(t.prompt_eval_count, ' tokens')], ['Output', number(t.eval_count, ' tokens')],
      ['Model load', seconds(t.load_duration)], ['Prefill', seconds(t.prompt_eval_duration)],
      ['Prefill rate', number(t.prefill_tokens_per_second, ' tok/s')], ['Generation', seconds(t.eval_duration)],
      ['Generation rate', number(t.eval_tokens_per_second, ' tok/s')], ['Ollama total', seconds(t.total_duration)],
      ['Ariadne total', number(typeof r.wall_duration_ms === 'number' ? r.wall_duration_ms / 1000 : null, ' s')],
      ['Completion', t.done_reason || 'Unavailable'], ['GPU', h.gpu?.name || 'Unavailable'],
      ['VRAM after generation', number(h.gpu?.used_gb, ' GB')], ['VRAM peak', number(h.vram_peak_gb, ' GB')],
      ['GPU utilisation', number(h.gpu_utilisation, '%')], ['System RAM used', number(h.memory?.used_gb, ' GB')],
      ['Resident models', h.resident_models?.map(x => x.name).join(', ') || 'Unavailable'],
      ['Generation state', r.success ? 'Saved' : 'Failed'], ['Preview', `${previewState} / ${errors.length} runtime error(s)`],
      ['Project', r.project_path || 'No project saved'], ['Run ID', r.run_id], ['Agent model calls', r.totals?.model_calls ?? 'Unavailable'],
      ['Agent input/output tokens', r.totals?.token_counts_complete ? `${r.totals.input_tokens} / ${r.totals.output_tokens}` : 'Unavailable'],
      ['Agent model/tool time', r.totals ? `${number(r.totals.model_duration_ms/1000,' s')} / ${number(r.totals.tool_duration_ms/1000,' s')}` : 'Unavailable'],
      ['Agent stop reason', r.stop_reason || 'Unavailable'],
    ];
    const list = make('dl', '', 'lab-metrics');
    for (const [label, value] of rows) list.append(make('dt', label), make('dd', String(value || 'Unavailable')));
    target.append(list);
    if (r.error) target.append(make('p', r.error));
  }
  function render() {
    dispose(); root.replaceChildren();
    root.append(make('h3', current.run.project_name || 'The Lab'));
    root.append(make('p', current.message || 'Browser build saved.'));
    const verification=make('p',current.run.verified?'BUILD VERIFIED · READY':current.run.pipeline_version>=2?'NEEDS ATTENTION':'Legacy build · verification not performed');
    root.append(verification);
    if(current.run.verification) {
      const v=current.run.verification, requirements=v.requirements||[];
      root.append(make('p',current.run.pipeline_version>=4
        ? `Syntax: Passed · Runtime: Passed · Smoke tests: Passed · Browser checks passed: ${v.checks?.length||0}`
        : `Syntax: Passed · Runtime: Passed · Smoke tests: Passed · Requirements: ${requirements.length}/${requirements.length} (${requirements.filter(c=>c.mode==='source').length} source review, ${requirements.filter(c=>c.mode==='browser').length} browser checks)`));
    }
    if(current.run.attempts?.length) {
      const diagnostics=make('details'), summary=make('summary','View diagnostics / candidate history');
      diagnostics.append(summary,make('pre',JSON.stringify(current.run.attempts,null,2),'lab-code'));root.append(diagnostics);
      for(const attempt of current.run.attempts) {
        const button=make('button',`Inspect candidate ${attempt.attempt+1}`,'chat-utility-button'); button.type='button';
        button.addEventListener('click',async()=>{
          button.disabled=true;
          try {
            const response=await fetch(`/api/code-lab/candidate?run_id=${current.run.run_id}&attempt=${attempt.attempt}`);
            const value=await response.json();if(!response.ok) throw Error(value.message||'No valid candidate source saved.');
            diagnostics.append(make('pre',value.code,'lab-code'));
          } catch(error) { diagnostics.append(make('p',error.message)); }
          finally {button.disabled=false;}
        });diagnostics.append(button);
      }
    }
    const tabs = make('div', '', 'lab-toolbar'), controls = make('div', '', 'lab-toolbar');
    const preview = make('section'), code = make('pre', current.code || 'No source saved.', 'lab-code'), metrics = make('section');
    metrics.dataset.labMetrics = '';
    const runtime = make('pre', '', 'lab-runtime'); runtime.dataset.labRuntime = '';
    const holder = make('div', '', 'lab-preview'); preview.append(controls, holder, runtime);
    for (const [label, panel] of [['Preview', preview], ['Code', code], ['Metrics', metrics]]) {
      const button = make('button', label, 'chat-utility-button'); button.type = 'button';
      button.addEventListener('click', () => {
        for (const p of [preview, code, metrics]) p.hidden = p !== panel;
        for (const b of tabs.children) b.setAttribute('aria-pressed', String(b === button));
      });
      button.setAttribute('aria-pressed', String(panel === code)); tabs.append(button);
      panel.hidden = panel !== code;
    }
    const launch = make('button', 'Launch Lab Runner', 'chat-utility-button'); launch.type = 'button';
    const runnerStatus = make('p', '', 'lab-runtime');
    runnerStatus.setAttribute('role', 'status'); runnerStatus.setAttribute('aria-live', 'polite');
    launch.disabled = !current.run.success || (current.run.pipeline_version>=2 && !current.run.verified); launch.title = 'Separate full-screen session. Alt+F4 closes the Runner.';
    launch.addEventListener('click', async () => {
      launch.disabled = true;
      runnerStatus.textContent = 'Opening full-screen Lab Runner...';
      try {
        let response = await fetch('/api/code-lab/runner', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run_id: current.run.run_id})});
        let job = await response.json();
        if (!response.ok) throw new Error(job.message || 'Runner launch failed.');
        for (let attempt = 0; attempt < 60; attempt++) {
          if (job.state === 'READY' && typeof job.foreground === 'boolean') {
            runnerStatus.textContent = job.foreground
              ? 'Lab Runner is running full screen with keyboard focus. Alt+F4 closes its session.'
              : 'Lab Runner loaded, but Windows did not grant foreground focus. Select its Edge window with Alt+Tab.';
            return;
          }
          if (['FAILED', 'CLOSED'].includes(job.state)) throw new Error(job.message || 'Runner closed before readiness.');
          await new Promise(resolve => setTimeout(resolve, 500));
          response = await fetch(`/api/code-lab/runner/status?launch_id=${encodeURIComponent(job.launch_id)}`, {cache: 'no-store'});
          job = await response.json();
          if (!response.ok) throw new Error(job.message || 'Runner status unavailable.');
        }
        throw new Error('The Windows host did not confirm Runner readiness.');
      } catch (error) { runnerStatus.textContent = `Lab Runner: ${error.message}`; }
      finally { launch.disabled = !current?.run.success; }
    });
    tabs.append(launch);
    const run = make('button', 'Run', 'chat-utility-button'), reset = make('button', 'Reset / reload preview', 'chat-utility-button');
    run.type = reset.type = 'button'; run.disabled = reset.disabled = !current.code;
    async function start() {
      dispose(); errors = []; previewState = 'RUNNING';
      token = crypto.randomUUID().replaceAll('-', '');
      frame = document.createElement('iframe');
      frame.title = 'The Lab browser preview'; frame.setAttribute('sandbox', 'allow-scripts');
      frame.setAttribute('allow', "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; usb 'none'; serial 'none'; display-capture 'none'");
      frame.referrerPolicy = 'no-referrer';
      // Chromium's required CSP also prevents a navigation to a server that does
      // not agree to this policy. Fail closed on browsers without this boundary.
      if (!('csp' in frame)) {
        previewState = 'FAILED'; errors.push('This browser cannot enforce preview CSP. Use current Chrome or Edge.');
        frame = null; refreshMetrics(); await persist(); return;
      }
      frame.csp = csp;
      frame.addEventListener('error', () => report('FAILED', 'Preview load failed.'));
      frame.src = `/api/code-lab/preview?run_id=${encodeURIComponent(current.run.run_id)}&token=${token}`;
      holder.append(frame);
      preview.hidden = false; code.hidden = metrics.hidden = true;
      for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.textContent === 'Preview'));
      refreshMetrics(); await persist();
      if (previewState === 'RUNNING') timer = setTimeout(() => report('FAILED', 'Preview did not report a successful load within 15 seconds.'), 15000);
    }
    run.addEventListener('click', start); reset.addEventListener('click', start);
    controls.append(run, reset); const save = make('button', 'Save to Inbox', 'chat-utility-button'); save.type = 'button';
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        const result = await window.ariadneLabChat.save(current.run.run_id, buildContext);
        status.textContent = `Saved to Inbox: ${result.inbox_path}`;
      } catch (error) { status.textContent = `Save to Inbox failed: ${error.message}`; }
      finally { save.disabled = false; }
    });
    root.append(tabs, runnerStatus, save, preview, code, metrics); refreshMetrics(); open();
  }
  async function acceptResult(result) {
    if (!result.run) throw new Error(result.message || 'No saved build was returned.');
    current = result; previewState = result.run.preview?.result || 'NOT RUN'; errors = result.run.preview?.errors || [];
    clearInterval(activityTimer); activity=null;
    render();
    try {
      await window.ariadneLabChat.record(result.run.run_id, buildContext);
      status.textContent = !result.ok ? `NEEDS ATTENTION: ${result.message} · conversation recorded` : result.run.verified ? 'BUILD VERIFIED · READY · conversation recorded' : 'Saved build · conversation recorded';
    } catch (error) { status.textContent = `Build saved, but conversation recording failed: ${error.message}. Use Save to Inbox to retry.`; }
  }
  function recoveryButton(runId = '') {
    const button = make('button', 'Recover saved build', 'chat-utility-button'); button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const response = await fetch('/api/code-lab/result' + (runId ? `?run_id=${encodeURIComponent(runId)}` : ''), {cache: 'no-store'});
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || 'No saved result is available yet.');
        buildContext = window.ariadneLabChat.context();
        if(result.pending) {await recoverRun(result.run.run_id);return;}
        await acceptResult(result);
      } catch (error) { status.textContent = error.message; button.disabled = false; }
    });
    return button;
  }
  root.append(recoveryButton());
  async function recoverRun(runId) {
    status.textContent = 'Connection interrupted. Waiting for the saved build...';
    root.replaceChildren(make('h3', 'The Lab'), make('p', status.textContent));
    const tested=new Set();
    for (let attempt = 0; attempt < 120; attempt++) {
      const response = await fetch(`/api/code-lab/result?run_id=${encodeURIComponent(runId)}`, {cache: 'no-store'});
      const result = await response.json();
      if (response.ok && !result.pending) { await acceptResult(result); return; }
      if(result.run?.pending_test && !tested.has(result.run.pending_test.token)) {
        tested.add(result.run.pending_test.token);await testCandidate(result.run.pending_test);
      }
      if (!result.pending) throw new Error(result.message || 'Saved build could not be recovered.');
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    throw new Error('The build is still unconfirmed. Recover its saved result before starting another build.');
  }
  function report(result, message = '') {
    if (message && errors.length < 100) errors.push(String(message).slice(0, 2000));
    previewState = result; clearTimeout(timer); refreshMetrics(); void persist();
  }
  addEventListener('message', event => {
    const data = event.data;
    if (!frame || event.source !== frame.contentWindow || event.origin !== 'null' || data?.type !== 'code-lab-preview' || data.token !== token) return;
    if (data.kind === 'loaded') report(errors.length ? 'ERROR' : 'LOADED');
    else if (['error', 'rejection'].includes(data.kind)) report('ERROR', data.message);
  });
  toggle.addEventListener('click', async () => {
    if (busy || document.querySelector('#ask-submit').disabled) return;
    const desired = !enabled;
    const model = document.querySelector('#model-name'), context = document.querySelector('#model-context');
    const submit = document.querySelector('#ask-submit');
    if (desired) { normalModel = model.textContent; normalContext = context.textContent; }
    busy = true; toggle.disabled = submit.disabled = true;
    const dialog = make('dialog', '', 'lab-load-dialog');
    dialog.setAttribute('aria-labelledby', 'lab-load-heading'); dialog.setAttribute('aria-busy', 'true');
    const spinner = make('div', '', 'lab-load-spinner'); spinner.setAttribute('aria-hidden', 'true');
    const heading = make('h2', desired ? 'Preparing The Lab' : 'Returning to Chat'); heading.id = 'lab-load-heading';
    const explanation = make('p', desired ? 'Checking the coding model…' : `Loading ${normalModel}…`);
    explanation.setAttribute('role', 'status');
    const details = make('dl', '', 'lab-metrics');
    dialog.append(spinner, heading, explanation, details); document.body.append(dialog);
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.showModal();
    const announce = loading => window.dispatchEvent(new CustomEvent('ariadne:working-model', {detail: {loading, selection: workingSelection}}));
    announce(true);
    const version = ++generation;
    try {
      if (desired) {
        const response = await fetch('/api/code-lab'); const config = await response.json();
        if (!response.ok || !config.installed) throw new Error(config.message || 'The coding model is unavailable.');
        heading.textContent = 'Loading model'; explanation.textContent = `Loading ${config.model} for The Lab. Releasing the previous model from memory…`;
        const info = config.model_details || {};
        const rows = [['Model', config.model], ['Parameters', info.parameter_size || 'Unavailable'],
          ['Quantisation', info.quantization_level || 'Unavailable'],
          ['Model size', typeof config.size_bytes === 'number' ? `${(config.size_bytes / 1024 ** 3).toFixed(2)} GB` : 'Unavailable'],
          ['Context', `${(config.context_tokens / 1024).toLocaleString()}K · ${config.context_tokens.toLocaleString()} tokens`]];
        for (const [label, value] of rows) details.append(make('dt', label), make('dd', value));
      }
      status.textContent = 'LOADING MODEL';
      const response = await fetch('/api/code-lab/prepare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: desired})});
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || 'Model loading failed.');
      if (version !== generation) return;
      enabled = desired; toggle.setAttribute('aria-pressed', String(enabled));
      workingSelection = enabled ? {model: result.model, provider_id: result.provider_id, mode: 'The Lab', location: 'desktop'} : null;
      model.textContent = enabled ? result.model : normalModel;
      context.textContent = enabled ? `${result.context_tokens / 1024}K context · The Lab · loaded` : normalContext;
      submit.innerHTML = enabled ? 'Build <span>↗</span>' : 'Ask <span>↗</span>';
      document.querySelector('#ask-input').placeholder = enabled ? 'Describe the browser application to build…' : 'Ask Ariadne anything…';
      if (!enabled) dispose();
      status.textContent = enabled ? 'The Lab is ready. Describe an application and click Build.' : 'Conversation mode · model loaded.';
      heading.textContent = enabled ? 'The Lab is ready' : 'Chat is ready';
      explanation.textContent = result.message;
      spinner.classList.add('complete'); dialog.setAttribute('aria-busy', 'false');
      announce(false);
      setTimeout(() => { dialog.close(); dialog.remove(); toggle.focus(); }, 900);
    } catch (error) {
      heading.textContent = 'Model loading failed'; explanation.textContent = error.message;
      spinner.hidden = true; dialog.setAttribute('aria-busy', 'false'); status.textContent = error.message;
      const close = make('button', 'Close', 'chat-utility-button'); close.type = 'button';
      close.addEventListener('click', () => { dialog.close(); dialog.remove(); toggle.focus(); }); dialog.append(close); close.focus();
      announce(false);
    } finally { busy = false; toggle.disabled = submit.disabled = false; }
  });
  window.ariadneLab = {
    openSaved: async runId => {
      const response = await fetch(`/api/code-lab/result?run_id=${encodeURIComponent(runId)}`, {cache: 'no-store'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || 'Saved build could not be opened.');
      if(result.pending) throw Error('This build is still being generated or verified.');
      buildContext = window.ariadneLabChat.context();
      await acceptResult(result);
    },
    active: () => enabled,
    switching: () => busy,
    rememberConversation: (model, context) => { normalModel = model; normalContext = context; },
    build: async (prompt, context) => {
      if (busy) return;
      if (!prompt.trim()) { status.textContent = 'Describe an application first.'; return; }
      buildContext = context;
      busy = true; toggle.disabled = true; document.querySelector('#ask-submit').disabled = true;
      dispose(); startActivity(); open();
      let runId = '', complete = false;
      try {
        const response = await fetch('/api/code-lab/stream', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt})});
        if (!response.ok || !response.body) throw new Error('The Lab request failed.');
        const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = '';
        while (true) {
          const {value, done} = await reader.read(); pending += decoder.decode(value || new Uint8Array(), {stream: !done});
          const lines = pending.split('\n'); pending = lines.pop();
          for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);
            if(activity) activity.lastEvent=Date.now();
            if (event.run_id) runId = event.run_id;
            if (event.type === 'state') { status.textContent = event.state; if(activity) activity.stage=event.state; }
            if (event.type === 'output' && activity) { activity.characters=event.characters;activity.lastOutput=Date.now(); }
            if (event.type === 'test_candidate') void testCandidate(event).catch(error=>{status.textContent=`Verification: ${error.message}`;});
            if (event.type === 'complete') {
              complete = true;
              if (!event.run) throw new Error(event.message || 'Generation failed.');
              await acceptResult(event);
            }
          }
          if (done) break;
        }
        if (!complete) throw new Error('Generation connection ended before a result was received.');
      } catch (error) {
        if (runId && !complete) {
          try { await recoverRun(runId); return; } catch (recoveryError) { error = recoveryError; }
        }
        status.textContent = `BUILD RESULT UNCONFIRMED: ${error.message}`;
        root.replaceChildren(make('h3', 'The Lab'), make('p', status.textContent), recoveryButton(runId));
      }
      finally { clearInterval(activityTimer);testFrame?.remove(); busy = false; toggle.disabled = false; document.querySelector('#ask-submit').disabled = false; }
    },
  };
})();
