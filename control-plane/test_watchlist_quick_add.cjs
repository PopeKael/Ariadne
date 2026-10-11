const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  const nodes = new Map();
  function node(key) {
    if (!nodes.has(key)) nodes.set(key, {value:'', textContent:'', dataset:{}, handlers:{}, disabled:false,
      addEventListener(event, fn) {this.handlers[event] = fn;}, setAttribute(){}, focus(){this.focused=true;},
      showModal(){this.open=true;}, close(){this.open=false;}, querySelector(){return node(key + ':submit');},
      reset(){for (const field of Object.values(this.elements || {})) field.value=''; if(this.elements?.interval_days) this.elements.interval_days.value='7';}
    });
    return nodes.get(key);
  }
  for (const key of ['#watch-form','#project-form']) {
    node(key).elements = Object.fromEntries(['id','title','kind','purpose','query','sources','interval_days','next_check','repository'].map(name => [name,node(key+':'+name)]));
  }
  let saved=[], fail=false;
  const context = vm.createContext({URL, JSON, Map, String, Number, Boolean, Date, Object, CustomEvent:class{},
    document:{querySelector:node, querySelectorAll:()=>[], hidden:false},
    window:{setInterval(){},dispatchEvent(){}},
    fetch:async (path,options) => {
      if (options?.method === 'POST') {
        const body = JSON.parse(options.body); saved.push(body);
        if (fail) return {ok:false,json:async()=>({ok:false,message:'Could not save'})};
        return {ok:true,json:async()=>({ok:true,watch:{...body,state:'active'}})};
      }
      return {ok:true,json:async()=>({ok:true,watches:[],attention:0,scheduler:{detail:'Checks run locally.',running:true}})};
    }
  });
  vm.runInContext(fs.readFileSync(__dirname+'/watchlist.js','utf8'),context);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  await settle();
  node('#add-watch').handlers.click();
  assert.equal(node('#project-editor').open,true);
  assert.equal(node('#project-options').open,false);
  assert.equal(node('#project-form').elements.repository.focused,true);
  const form=node('#project-form');
  form.elements.repository.value='https://github.com/Friend/Project.git/';
  await form.handlers.submit({preventDefault(){}});
  assert.equal(saved[0].title,'Friend/Project');
  assert.equal(saved[0].kind,'project');
  assert.equal(saved[0].interval_days,7);
  assert.deepEqual(saved[0].sources,['https://github.com/Friend/Project']);
  assert.equal(saved[0].query,'');
  assert.match(node('#watch-status').textContent,/First check queued/);
  assert.equal(node('#project-editor').open,false);
  for (const url of ['https://evil.test/a/b','https://github.com/a/b/issues/1','https://user:password@github.com/a/b','https://github.com/a/b?q=x']) {
    node('#add-watch').handlers.click(); form.elements.repository.value=url;
    const before=saved.length;
    await form.handlers.submit({preventDefault(){}});
    assert.equal(saved.length,before,'invalid links never reach save');
    assert.equal(node('#project-editor').open,true);
  }
  form.elements.repository.value='https://github.com/Friend/Project';
  form.elements.title.value='My project'; form.elements.interval_days.value='14';
  fail=true;
  await form.handlers.submit({preventDefault(){}});
  assert.equal(saved.at(-1).title,'My project'); assert.equal(saved.at(-1).interval_days,14);
  assert.equal(form.elements.repository.value,'https://github.com/Friend/Project');
  assert.equal(node('#project-editor').open,true);
  assert.match(node('#project-status').textContent,/Could not save/);
  fail=false;
  await form.handlers.submit({preventDefault(){}});
  assert.equal(node('#project-editor').open,false);
  node('#add-watch').handlers.click(); node('#other-watch').handlers.click();
  assert.equal(node('#project-editor').open,false);
  assert.equal(node('#watch-editor').open,true);
  console.log('Watchlist quick add: default payload, link validation, optional settings, failure recovery and general-editor access passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
