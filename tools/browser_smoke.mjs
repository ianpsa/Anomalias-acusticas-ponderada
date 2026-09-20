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
  const app = await address(start(['-m','ferris.server','--port','0','--data',path.join(temporary,'data'),'--models',path.join(temporary,'models'),'--serial-port','']));
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
  async function until(expression, attempts=50) {
    for (let i=0;i<attempts;i++) { if (await evaluate(expression)) return; await sleep(100); }
    throw Error('Condition not reached: '+expression);
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:app}); await until('document.readyState === "complete"');
  assert.equal(await evaluate('document.getElementById("voice-choice").value'), 'ferris');
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
  // Every wake grants one question, with or without spoken responses.
  const oneQuestionPerWake = await evaluate(`(async () => {
    const originalApi = api, originalFetch = window.fetch, OriginalAudio = window.Audio;
    let transcriptions = 0, fail = false;
    api = async (path, ...args) => {
      if (path === '/api/transcribe') { transcriptions++; if (fail) throw Error('STT failure'); return {text:'Como está o dia?'}; }
      return originalApi(path, ...args);
    };
    window.fetch = (url, options) => url === '/api/speech' ? Promise.resolve(new Response(new Blob(['test'], {type:'audio/wav'}))) : originalFetch(url, options);
    window.Audio = class { play() { queueMicrotask(() => this.onended?.()); return Promise.resolve(); } pause() {} removeAttribute() {} load() {} };
    const speakQuestion = async () => { await processLocal(new Float32Array(800).fill(.1)); silenceSince = Date.now() - 1000; await processLocal(new Float32Array(800)); };
    try {
      $('voice-mode').value = 'esp'; enabled = true; hardwareMuted = false; closeQuestion();
      for (const spoken of [false, true]) {
        $('spoken').checked = spoken;
        await wake({id:eventId + 1, text:'Olá! Pode perguntar.'});
        if (activeUntil <= Date.now()) return false;
        const before = transcriptions;
        await speakQuestion();
        if (transcriptions !== before + 1 || activeUntil !== 0) return false;
        await speakQuestion();
        if (transcriptions !== before + 1) return false;
      }
      await wake({id:eventId + 1, text:'Olá!'}); fail = true;
      await speakQuestion();
      return activeUntil === 0;
    } finally {
      api = originalApi; window.fetch = originalFetch; window.Audio = OriginalAudio;
      enabled = false; closeQuestion(); $('spoken').checked = false;
    }
  })()`);
  assert.equal(oneQuestionPerWake, true);
  await evaluate('document.getElementById("tab-voice").click()');
  assert.equal(await evaluate('document.getElementById("voice-pane").hidden'),false);
  await evaluate('document.getElementById("record-source").value="pc"; document.getElementById("label").value="noise"; document.getElementById("record").click()');
  await until('document.getElementById("count-noise").textContent === "1"');
  assert.match(await evaluate('document.getElementById("record-input").textContent'),/^Microfone: /);
  // Simulate a muted capture at the worklet callback, preserving the real WAV/API path.
  await evaluate('window.originalCaptureStart = Capture.prototype.start; Capture.prototype.start = function(callback) { return window.originalCaptureStart.call(this, chunk => callback(new Float32Array(chunk.length))); }; document.getElementById("record").click()');
  await until('document.getElementById("record-state").textContent.includes("sem sinal")');
  assert.equal(await evaluate('document.getElementById("count-noise").textContent'),'1');
  assert.match(await evaluate('document.getElementById("record-level-text").textContent'),/Sem sinal/);
  await evaluate('Capture.prototype.start = window.originalCaptureStart');
  // ESP collection never requests getUserMedia, and gives a USB-specific error offline.
  await evaluate('window.pcStarts = 0; Capture.prototype.start = function(...args) { window.pcStarts++; return window.originalCaptureStart.apply(this,args); }; document.getElementById("record-source").value="esp"; document.getElementById("record").click()');
  await until('document.getElementById("record-state").textContent.includes("Conecte o ESP32")');
  assert.equal(await evaluate('window.pcStarts'),0);
  // Physical mute stops an existing track and invalidates a pending speech response.
  await evaluate('(async () => { window.muteCapture = new Capture(); await window.muteCapture.start(() => {}); window.muteTrack = window.muteCapture.stream.getTracks()[0]; capture = window.muteCapture; enabled = true; applyHardware({muted:true,revision:100}); })()');
  assert.equal(await evaluate('window.muteTrack.readyState'),'ended');
  assert.equal(await evaluate('enabled'),false);
  assert.equal(await evaluate('document.getElementById("record").disabled'),true);
  await evaluate('applyHardware({muted:false,revision:101});');
  assert.equal(await evaluate('enabled'),false);
  const voiceCancelled = await evaluate(`(async () => {
    const originalFetch = window.fetch; let signal, release, requestedVoice;
    $('voice-choice').value = 'ferris'; $('voice-choice').dispatchEvent(new Event('change'));
    window.fetch = (url, options) => url === '/api/speech' ? new Promise(resolve => { signal = options.signal; requestedVoice = JSON.parse(options.body).voice; release = resolve; }) : originalFetch(url, options);
    try {
      const completion = speak('Teste de voz', false, true);
      applyHardware({muted:true,revision:102});
      release(new Response(new Blob([new Uint8Array(44)], {type:'audio/wav'})));
      await completion; await new Promise(resolve => setTimeout(resolve, 20));
      return signal.aborted && currentSpeech === null && !speaking && requestedVoice === 'ferris';
    } finally { window.fetch = originalFetch; applyHardware({muted:false,revision:103}); }
  })()`);
  assert.equal(voiceCancelled,true);
  await evaluate('Capture.prototype.start = window.originalCaptureStart');
  await evaluate('document.getElementById("train-model").click()');
  await until('document.getElementById("training-state").textContent.includes("12 exemplos")');
  // Synthetic tones exercise the real training subprocess without private audio.
  const seed = start(['-c',`
import sys,wave
from pathlib import Path
import numpy as np
root=Path(sys.argv[1]); rng=np.random.default_rng(8)
for label,hz in [('ferris',900),('other',2400),('noise',3000)]:
 folder=root/'recordings'/label/'browser-test'; folder.mkdir(parents=True,exist_ok=True)
 for i in range(12):
  t=np.arange(32000)/16000
  pcm=(8000*np.sin(2*np.pi*(hz+i)*t)+rng.normal(0,100,32000)).astype('<i2').tobytes()
  with wave.open(str(folder/f'{i}.wav'),'wb') as w:
   w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
`,path.join(temporary,'data')]);
  await new Promise((resolve,reject) => seed.on('exit',code=>code===0?resolve():reject(Error('Training fixture failed'))));
  await evaluate('refresh()');
  await evaluate('document.getElementById("training-mode").value="recordings"; document.getElementById("train-model").click()');
  await until('document.getElementById("training-state").textContent.includes("Modelo treinado e ativo")',450);
  assert.match(await evaluate('document.getElementById("training-metrics").textContent'),/Experimental.*Teste reservado/);
  assert.equal(await evaluate('document.getElementById("train-model").disabled'),false);
  const trainingShot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await writeFile(path.join(temporary,'training-desktop.png'),Buffer.from(trainingShot.data,'base64'));
  await call('Page.reload'); await until('document.readyState === "complete"');
  assert.equal(await evaluate('document.getElementById("voice-choice").value'), 'ferris');
  await until('document.getElementById("model-state").textContent.includes("Detector ativo")');
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
  await evaluate('document.getElementById("tab-voice").click()');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
  const trainingMobile=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await writeFile(path.join(temporary,'training-mobile.png'),Buffer.from(trainingMobile.data,'base64'));
  assert.deepEqual(errors,[]);
  console.log('PASS: LM Studio, Gemma, chat, code refusal, Google fallback, PC/ESP recording selection, physical mute, late speech cancellation, training button, metrics, activation after reload, local voice prerequisites, tabs, desktop/mobile overflow, no JS errors.');
  console.log('Screenshots: '+temporary);
} finally { for (const child of children) child.kill('SIGTERM'); }
