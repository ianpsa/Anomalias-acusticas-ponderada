// End-to-end UI smoke test. The LM Studio endpoint is a deterministic fixture.
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const temporary = await mkdtemp(path.join(tmpdir(), 'ferris-browser-'));
const python = process.env.PYTHON_BIN || 'python3';
const children = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));
function start(args) {
  const child = spawn(python, args, {stdio: ['ignore','pipe','pipe']}); children.push(child); return child;
}
async function address(child) {
  let output = '';
  return await new Promise((resolve,reject) => {
    const timer = setTimeout(() => reject(Error('Server did not start: '+output)), 10000);
    child.stdout.on('data', data => { output += data; const m = output.match(/http:\/\/127.0.0.1:\d+/); if (m) { clearTimeout(timer); resolve(m[0]); } });
    child.stderr.on('data', data => output += data);
    child.on('exit', () => { clearTimeout(timer); reject(Error(output)); });
  });
}
let chrome;
try {
  const app = await address(start(['-m','ferris.server','--port','0','--data',path.join(temporary,'data')]));
  const fixture = await address(start(['-u','-c',`
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self): self.send({'data':[{'id':'text-embedding-model'},{'id':'ferris-gemma'}]})
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert data['model']=='ferris-gemma'
  self.send({'choices':[{'message':{'content':'Olá Ian, esta é uma resposta de teste do servidor remoto.'}}]})
 def send(self,value):
  raw=json.dumps(value).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
s=HTTPServer(('127.0.0.1',0),Handler)
print('http://127.0.0.1:'+str(s.server_port),flush=True)
s.serve_forever()
`]));
  chrome = spawn(process.env.CHROMIUM || 'chromium', ['--headless','--no-sandbox','--disable-gpu','--no-first-run',
    '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',
    '--disable-dev-shm-usage', '--remote-debugging-pipe', '--user-data-dir='+path.join(temporary,'chrome')], {stdio:['ignore','ignore','pipe','pipe','pipe']});
  children.push(chrome);
  let id=0, buffer=Buffer.alloc(0); const pending=new Map(), errors=[];
  chrome.stdio[4].on('data', chunk => {
    buffer=Buffer.concat([buffer,chunk]); let end;
    while ((end=buffer.indexOf(0))>=0) {
      const value=JSON.parse(buffer.subarray(0,end).toString()); buffer=buffer.subarray(end+1);
      if (value.id && pending.has(value.id)) { const p=pending.get(value.id); pending.delete(value.id); clearTimeout(p.timer); value.error?p.reject(Error(JSON.stringify(value.error))):p.resolve(value.result); }
      if (value.method==='Runtime.exceptionThrown') errors.push(value.params.exceptionDetails);
    }
  });
  function cdp(method, params={}, sessionId) {
    return new Promise((resolve,reject) => {
      const n=++id, timer=setTimeout(()=>{pending.delete(n);reject(Error('CDP timeout: '+method));},15000);
      pending.set(n,{resolve,reject,timer}); chrome.stdio[3].write(JSON.stringify({id:n,method,params,...(sessionId?{sessionId}:{})})+'\0');
    });
  }
  const {targetId}=await cdp('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await cdp('Target.attachToTarget',{targetId,flatten:true});
  const call=(method,params)=>cdp(method,params,sessionId);
  await call('Runtime.enable'); await call('Page.enable');
  const evaluate=async expression => {
    const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails)); return result.result.value;
  };
  async function until(expression) {
    for (let i=0;i<50;i++) { if (await evaluate(expression)) return; await sleep(100); }
    throw Error('Condition not reached: '+expression);
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:app}); await until('document.readyState === "complete"');
  await evaluate('document.getElementById("spoken").checked=false; document.getElementById("open-settings").click()');
  await until('document.getElementById("settings").open');
  await evaluate(`document.getElementById('base-url').value=${JSON.stringify(fixture)}; document.getElementById('test-connection').click()`);
  await until('document.getElementById("model").value === "ferris-gemma"');
  await evaluate('document.getElementById("settings-form").requestSubmit()');
  await until('!document.getElementById("settings").open');
  await evaluate('document.getElementById("message").value="Olá, Ferris"; document.getElementById("chat-form").requestSubmit()');
  await until('document.getElementById("messages").textContent.includes("resposta de teste do servidor remoto")');
  await evaluate('document.getElementById("message").value="Crie um código Python"; document.getElementById("chat-form").requestSubmit()');
  await until('document.getElementById("messages").textContent.includes("não criar nem executar")');
  await evaluate('document.getElementById("message").value="Pesquise café"; document.getElementById("chat-form").requestSubmit()');
  await until('document.querySelector(".sources a") !== null');
  assert.match(await evaluate('document.querySelector(".sources a").href'),/^https:\/\/www.google.com\/search/);
  await evaluate('document.getElementById("tab-voice").click()');
  assert.equal(await evaluate('document.getElementById("voice-pane").hidden'),false);
  await evaluate('document.getElementById("label").value="noise"; document.getElementById("record").click()');
  await until('document.getElementById("count-noise").textContent === "1"');
  await evaluate('document.getElementById("voice-mode").value="local"; document.getElementById("listen").click()');
  await until('document.getElementById("notice").textContent.includes("detector ONNX treinado")');
  await evaluate('document.getElementById("tab-chat").click()');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
  const shot=await call('Page.captureScreenshot',{format:'png'});
  await writeFile(path.join(temporary,'desktop.png'),Buffer.from(shot.data,'base64'));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
  const mobile=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await writeFile(path.join(temporary,'mobile.png'),Buffer.from(mobile.data,'base64'));
  assert.deepEqual(errors,[]);
  console.log('PASS: LM Studio connection, Gemma selection, chat, code refusal, Google fallback, microphone recording with fake audio, untrained-model notice, tabs, desktop/mobile overflow, no JS errors.');
  console.log('Screenshots: '+temporary);
} finally { for (const child of children) child.kill('SIGTERM'); }
