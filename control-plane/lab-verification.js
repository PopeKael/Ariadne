/* Trusted, bounded test harness inside the existing opaque-origin sandbox. */
(() => {
  const config = window.__labTest; delete window.__labTest;
  const failures = [], sleep = ms => new Promise(r => setTimeout(r, ms));
  const send = (kind, evidence) => parent.postMessage({type:'code-lab-test', token:config.token, index:config.index, kind, evidence}, '*');
  const error = message => { if (failures.length < 100) failures.push(String(message).slice(0,2000)); };
  addEventListener('error', e => error(`${e.message} (index.html:${Math.max(1,e.lineno-config.source_offset)}:${e.colno})`));
  addEventListener('securitypolicyviolation',e=>error('Sandbox blocked resource: '+e.violatedDirective+' '+e.blockedURI));
  addEventListener('unhandledrejection', e => error(String(e.reason)));
  const originalError = console.error.bind(console);
  console.error = (...args) => { error('console.error: ' + args.map(String).join(' ')); originalError(...args); };
  const visible = el => { const r=el.getBoundingClientRect(),s=getComputedStyle(el); return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  function query(selector) {
    // Some local models supply jQuery's text filter. Resolve it against the actual
    // DOM without executing model code, instead of rewriting a working application.
    const filter=selector.match(/^(.+):contains\((['"])(.*?)\2\)$/);
    return [...document.querySelectorAll(filter?filter[1]:selector)].filter(el=>!filter || el.textContent.includes(filter[3]));
  }
  function canvasState() {
    return [...document.querySelectorAll('canvas')].slice(0,4).map(c => {
      try { return c.toDataURL().slice(-20000); } catch { return 'unreadable'; }
    }).join('');
  }
  const snapshot = () => ({text:document.body?.innerText.slice(0,4000)||'', dom:document.body?.innerHTML.slice(0,4000)||'',canvas:canvasState().slice(-2000),
    visual:[...document.querySelectorAll('body *')].filter(visible).slice(0,100).map(el=>({tag:el.tagName,value:el.value||'',style:el.getAttribute('style')||'',class:(el.className?.baseVal??el.className)||''}))});
  const changed = (a,b) => a.text!==b.text || JSON.stringify(a.visual)!==JSON.stringify(b.visual) || a.canvas!==b.canvas;
  async function action(a) {
    const before=snapshot(); let label='';
    if (a.kind === 'key') {
      const target = a.selector ? query(a.selector)[0] : document;
      if (!target) throw Error('Missing keyboard target: '+a.selector);
      target.dispatchEvent(new KeyboardEvent('keydown',{key:a.value,code:a.value,bubbles:true}));
      target.dispatchEvent(new KeyboardEvent('keyup',{key:a.value,code:a.value,bubbles:true}));
    } else {
      const target=query(a.selector)[0];
      if (!target || !visible(target)) throw Error('Missing/hidden control: '+a.selector);
      if (target.disabled) throw Error('Disabled control: '+a.selector);
      label=target.textContent.trim()||target.value||'';
      if (a.kind==='click') target.click();
      else {
        const proto=target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto,'value').set.call(target,a.value);
        target.dispatchEvent(new Event('input',{bubbles:true})); target.dispatchEvent(new Event('change',{bubbles:true}));
      }
    }
    await sleep(100);
    const after=snapshot();return {...a,label,changed:changed(before,after),before:before.text.slice(0,1000),after:after.text.slice(0,1000)};
  }
  addEventListener('DOMContentLoaded', async () => {
    send('running', {});
    await sleep(350);
    const initial=snapshot(), evidence={initial,errors:failures,actions:[],warnings:[],controls:[...document.querySelectorAll('button,input,select,textarea,[role=button]')].slice(0,30).map(el=>({tag:el.tagName,id:el.id,label:(el.textContent||el.value||'').slice(0,120),visible:visible(el),disabled:!!el.disabled}))};
    try {
      if (config.index === -1) {
        evidence.usable=!!document.body && [...document.querySelectorAll('body *')].some(el => visible(el) && (el.textContent.trim() || ['CANVAS','SVG','INPUT'].includes(el.tagName)));
        if (!evidence.usable) error('No usable visible application rendered.');
        for (const sheet of document.styleSheets) {
          try {
            // Browser CSSOM catches wholesale stylesheet rejection; partial invalid declarations need source review.
            if (sheet.ownerNode?.textContent.trim() && !sheet.cssRules.length) error('Stylesheet produced no valid CSS rules.');
            const css=(sheet.ownerNode?.textContent||'').replace(/\/\*[\s\S]*?\*\//g,'');
            for(const block of css.matchAll(/[^{}]+\{([^{}]*)\}/g)) {
              for(const declaration of block[1].matchAll(/([\w-]+)\s*:\s*([^;{}]+)(?:;|$)/g)) {
                const property=declaration[1],value=declaration[2].replace(/\s*!important\s*$/,'').trim();
                if(!property.startsWith('--') && !property.startsWith('-') && !CSS.supports(property,value)) error('Invalid/unsupported CSS declaration: '+property+': '+value);
              }
            }
          } catch(e) { error('Cannot inspect stylesheet: '+e.message); }
        }
        const buttons=[...document.querySelectorAll('button,input[type=button],input[type=submit],[role=button]')].filter(el=>visible(el)&&!el.disabled).slice(0,25);
        const selects=[...document.querySelectorAll('select')].filter(visible).slice(0,10);
        for (const el of selects) {
          if (el.options.length>1) { el.selectedIndex=1; el.dispatchEvent(new Event('change',{bubbles:true})); await sleep(100); }
        }
        for (const el of buttons) {
          if (!el.isConnected || !visible(el) || el.disabled) continue;
          const before=snapshot(), label=el.textContent.trim()||el.value;
          el.click(); await sleep(150);
          const after=snapshot(), didChange=changed(before,after);
          evidence.actions.push({label,changed:didChange,before:before.text.slice(0,2000),after:after.text.slice(0,2000)});
          if (!didChange) evidence.warnings.push('Control produced no observable change in this state: '+label);
        }
        evidence.smoke_passed=!failures.length;
      } else {
        evidence.checks=[];
        for (const c of config.checks) {
          const started=failures.length, before=snapshot(), result={description:c.description,actions:[]};
          try {
            for (const a of c.actions) result.actions.push(await action(a));
            const matches=query(c.selector).filter(visible), final=snapshot();
            result.matches=matches.map(el=>el.textContent.slice(0,2000)).slice(0,20);
            result.assertions={count:matches.length>=c.min_count && matches.length<=c.max_count,
              text:!c.text || matches.some(el=>el.textContent.includes(c.text)),
              change:!c.changed || result.actions.some(a=>a.changed) || changed(before,final)};
            if (!result.assertions.count) error('Test failed: '+c.description+' expected '+c.min_count+'–'+c.max_count+' visible matches for '+c.selector+', observed '+matches.length+'.');
            if (!result.assertions.text) error('Test failed: '+c.description+' expected text '+JSON.stringify(c.text)+', observed '+JSON.stringify(result.matches)+'.');
            if (!result.assertions.change) error('Test failed: '+c.description+' requested a visible state change but none occurred; inspect before/after and establish the prerequisite state.');
          } catch(e) { error(e.message); }
          result.errors=failures.slice(started);result.passed=result.errors.length===0;
          evidence.checks.push(result);
        }
        evidence.passed=evidence.checks.every(c=>c.passed)&&!failures.length;
      }
    } catch(e) { error(e.message); evidence.passed=false; evidence.smoke_passed=false; }
    evidence.errors=[...failures]; send('result',evidence);
  });
})();
