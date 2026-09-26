"""Execute the shipped JS with an in-memory DOM; not a browser layout test."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_operator_memory_selection_race_and_status_timeout():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the operator script regression")
    html = Path("src/virtual_ai/operator_panel.html").read_text(encoding="utf-8")
    script = html.split('<script nonce="__TOKEN__">', 1)[1].split("</script>", 1)[0]
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},timers=new Map();let serial=0,respond;
const element=id=>nodes[id]??={value:'',checked:false,textContent:'',replaceChildren(){},append(){}};
const sandbox={
  document:{getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
  AbortController,
  setTimeout:(fn,ms)=>{timers.set(++serial,{fn,ms});return serial},
  clearTimeout:id=>timers.delete(id),
  fetch:(path,options)=>new Promise((resolve,reject)=>{
    if(path==='/command')respond=resolve;
    options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));
  })
};
vm.createContext(sandbox);vm.runInContext(SCRIPT,sandbox);
(async()=>{
  // A pending status request must become visibly disconnected, not hang forever.
  [...timers.values()].find(t=>t.ms===5000).fn();
  await new Promise(setImmediate);
  assert.match(nodes.banner.textContent,/연결 끊김/);
  nodes.user.value='viewer-a';element('private').textContent='old private information';
  for(const id of ['identity','consent','storage','retrieval','public'])element(id).checked=true;
  void sandbox.command({action:'memory_inspect',platform:'youtube',user:'viewer-a'});
  nodes.user.value='viewer-b';nodes.user.oninput();
  assert.equal(nodes.private.textContent,'');
  for(const id of ['identity','consent','storage','retrieval','public'])assert.equal(nodes[id].checked,false);
  respond({ok:true,json:async()=>({private_memory:{text:'viewer-a private data'}})});
  await new Promise(setImmediate);
  assert.equal(nodes.private.textContent,'');assert.match(nodes.notice.textContent,/이전 대상/);
  nodes.identity.checked=true;nodes.platform.onchange();assert.equal(nodes.identity.checked,false);
  nodes.private.textContent='visible';nodes.clearprivate.onclick();
  assert.equal(nodes.private.textContent,'');assert.equal(nodes.user.value,'');
  void sandbox.command({action:'pause'});
  [...timers.values()].find(t=>t.ms===15000).fn();
  await new Promise(setImmediate);
  assert.match(nodes.notice.textContent,/결과를 확인하지 못했습니다/);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    import json

    result = subprocess.run(
        [node, "-e", "const SCRIPT=" + json.dumps(script) + ";" + harness],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
