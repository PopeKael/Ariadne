(() => {
  const token = new URLSearchParams(location.search).get('launch_id');
  const frame = document.querySelector('iframe'), failure = document.querySelector('#failure');
  const report = (state, message = '') => fetch('/api/code-lab/runner/status', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({launch_id: token, state, message, viewport: {width: innerWidth, height: innerHeight, screen_width: screen.width, screen_height: screen.height}})}).catch(() => {});
  let failed = false;
  const fail = message => { failed = true; frame.hidden = true; failure.hidden = false; failure.textContent = `${message} Close Lab Runner with Alt+F4.`; void report('FAILED', message); };
  if (!('csp' in frame)) { fail('This browser cannot enforce the build sandbox.'); return; }
  frame.csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; media-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; webrtc 'block'";
  let previewToken = crypto.randomUUID().replaceAll('-', '');
  const timeout = setTimeout(() => fail('The build did not report a successful load.'), 15000);
  addEventListener('message', event => {
    const data = event.data;
    if (event.source !== frame.contentWindow || event.origin !== 'null' || data?.type !== 'code-lab-preview' || data.token !== previewToken) return;
    if (data.kind === 'loaded' && !failed) { clearTimeout(timeout); frame.focus(); void report('READY'); }
    else if (['error', 'rejection'].includes(data.kind)) { clearTimeout(timeout); fail(data.message || 'Build runtime error.'); }
  });
  fetch(`/api/code-lab/runner/status?launch_id=${encodeURIComponent(token)}`, {cache: 'no-store'}).then(r => r.json()).then(job => {
    if (!job.run_id) throw new Error(job.message || 'Launch not found.');
    frame.src = `/api/code-lab/preview?run_id=${encodeURIComponent(job.run_id)}&token=${previewToken}`;
  }).catch(error => fail(error.message));
})();
