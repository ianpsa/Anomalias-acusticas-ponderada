'use strict';
const $ = (id) => document.getElementById(id);
let session = crypto.randomUUID();
let accessToken = '', enabled = false, busy = false, speaking = false, activeUntil = 0;
let recognition, capture, recordingCapture, currentUtterance, generation = 0, eventId = 0, polled = false;
let detectBusy = false, hits = 0, lastDetect = 0, commandSamples = [], silenceSince = 0, speechStarted = false;
let trainingChoice = false, trainingPolling = false;
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const norm = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

async function api(path, body, signal) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', signal,
    headers: {'Content-Type': 'application/json', ...(accessToken ? {Authorization: `Bearer ${accessToken}`} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir.');
  return data;
}
function notice(text) {
  $('notice').textContent = text; $('notice').hidden = false;
  clearTimeout(notice.timer); notice.timer = setTimeout(() => $('notice').hidden = true, 7000);
}
function state(text) { $('state').textContent = text; }
function idleState() { state(enabled ? (Date.now() < activeUntil ? 'Pode fazer sua pergunta.' : ($('voice-mode').value === 'esp' ? 'Aguardando “Ferris” no ESP32…' : 'Ouvindo por “Ferris”…')) : 'Microfone desligado.'); }
function addMessage(text, who = 'assistant', sources = [], error = false) {
  $('empty')?.remove();
  const box = document.createElement('div'); box.className = `message ${who}${error ? ' error' : ''}`;
  const label = document.createElement('small'); label.textContent = who === 'user' ? 'Você' : 'Ferris';
  box.append(label, document.createTextNode(text));
  if (sources.length) {
    const links = document.createElement('div'); links.className = 'sources';
    for (const source of sources) {
      try {
        const u = new URL(source.url); if (!['http:', 'https:'].includes(u.protocol)) continue;
        const a = document.createElement('a'); a.href = u.href; a.textContent = source.title || u.hostname;
        a.target = '_blank'; a.rel = 'noopener noreferrer'; links.append(a);
      } catch (_) { /* Ignore malformed source URLs. */ }
    }
    box.append(links);
  }
  $('messages').append(box); $('messages').scrollTop = $('messages').scrollHeight;
}
function speak(text, resume = true) {
  if (!$('spoken').checked || !window.speechSynthesis) {
    activeUntil = resume ? Date.now() + 20000 : 0; idleState(); return Promise.resolve();
  }
  return new Promise((resolve) => {
    speaking = true; recognition?.abort(); state('Ferris está falando…'); $('creature').classList.add('active');
    speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text); currentUtterance = utter; utter.lang = 'pt-BR'; utter.rate = 1.05;
    const voices = speechSynthesis.getVoices();
    utter.voice = voices.find(v => v.lang === 'pt-BR' && v.localService) || voices.find(v => v.lang.startsWith('pt')) || null;
    let done = false;
    const finish = () => {
      if (done) return; done = true;
      if (currentUtterance === utter) {
        currentUtterance = null; speaking = false; activeUntil = resume ? Date.now() + 20000 : 0;
        $('creature').classList.remove('active'); resetCommand(); idleState(); startRecognition();
      }
      resolve();
    };
    utter.onend = finish; utter.onerror = finish;
    utter._finish = finish;
    speechSynthesis.speak(utter);
  });
}
function cancelSpeech() {
  const utter = currentUtterance; currentUtterance = null; speaking = false;
  window.speechSynthesis?.cancel(); utter?._finish(); $('creature').classList.remove('active');
}
async function ask(text, search = false) {
  if (busy || !text.trim()) return;
  const turn = generation; busy = true; recognition?.abort(); $('send').disabled = true;
  addMessage(text, 'user'); state(search ? 'Pesquisando…' : 'Pensando…');
  const controller = new AbortController(); ask.controller = controller;
  try {
    const result = await api('/api/chat', {text, session, search}, controller.signal);
    if (turn !== generation) return;
    addMessage(result.text, 'assistant', result.sources); $('latency').textContent = `Resposta em ${(result.latency_ms / 1000).toFixed(1)} s`;
    // Allow speech completion to restart recognition after this request.
    busy = false; await speak(result.text, !result.end);
  } catch (error) {
    if (error.name !== 'AbortError' && turn === generation) { addMessage(error.message, 'assistant', [], true); notice(error.message); }
  } finally {
    if (turn === generation) { busy = false; $('send').disabled = false; idleState(); startRecognition(); }
  }
}
async function wake(event) {
  if (busy || speaking) return;
  const turn = generation; busy = true;
  try {
    const result = event || await api('/api/wake', {device: 'desktop'});
    if (turn !== generation) return;
    eventId = Math.max(eventId, result.id); hits = 0; addMessage(result.text); busy = false; await speak(result.text);
  } catch (e) { notice(e.message); }
  finally { if (turn === generation) { busy = false; idleState(); } }
}
function startRecognition() {
  if (!enabled || busy || speaking || $('voice-mode').value !== 'browser' || !recognition) return;
  try { recognition.start(); } catch (_) { /* Already active. */ }
}
function setupRecognition() {
  recognition = new SR(); recognition.lang = 'pt-BR'; recognition.continuous = true; recognition.interimResults = false;
  recognition.onresult = (event) => {
    if (busy || speaking || !enabled) return;
    const text = event.results[event.resultIndex][0].transcript.trim();
    if (Date.now() < activeUntil) { ask(text); return; }
    const match = /\b(ferris|feris|ferrys)\b/i.exec(norm(text));
    if (match) {
      const question = text.slice(match.index + match[0].length).replace(/^[\s,.!?]+/, '');
      if (question) { activeUntil = Date.now() + 20000; ask(question); } else wake();
    }
  };
  recognition.onend = () => { if (enabled) setTimeout(startRecognition, 400); };
  recognition.onerror = (event) => {
    if (['not-allowed', 'service-not-allowed', 'audio-capture'].includes(event.error)) {
      stopListening(); notice('Microfone indisponível. Confira as permissões do navegador.');
    } else if (event.error === 'network') {
      stopListening(); notice('O serviço de voz do navegador está indisponível. Use texto ou configure o modo local.');
    }
  };
}
function wav(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buffer);
  const str = (off, s) => [...s].forEach((c, i) => v.setUint8(off + i, c.charCodeAt(0)));
  str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => v.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, s)) * 32767), true));
  return buffer;
}
function b64(buffer) {
  const bytes = new Uint8Array(buffer); let s = '';
  for (let i = 0; i < bytes.length; i += 8192) s += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(s);
}
class Capture {
  constructor() { this.stopped = false; }
  async start(callback) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: false}});
      if (this.stopped) { stream.getTracks().forEach(t => t.stop()); throw new Error('Captura cancelada.'); }
      this.stream = stream;
      const context = new AudioContext({sampleRate: 16000}); this.context = context;
      await context.resume(); await context.audioWorklet.addModule('/capture.js');
      if (this.stopped) throw new Error('Captura cancelada.');
      this.source = context.createMediaStreamSource(stream);
      this.node = new AudioWorkletNode(context, 'ferris-capture');
      this.node.port.onmessage = ({data}) => callback(data);
      this.source.connect(this.node); this.node.connect(context.destination);
    } catch (error) { this.stop(); throw error; }
  }
  stop() {
    this.stopped = true;
    this.stream?.getTracks().forEach(t => t.stop()); this.stream = null;
    if (this.node) { this.node.port.onmessage = null; this.node.disconnect(); this.node = null; }
    this.source?.disconnect(); this.source = null;
    const context = this.context; this.context = null;
    if (context && context.state !== 'closed') context.close().catch(() => {});
  }
}
function resetCommand() { commandSamples = []; speechStarted = false; silenceSince = 0; }
async function processLocal(chunk) {
  if (!enabled || busy || speaking) { resetCommand(); processLocal.ring = []; return; }
  if (Date.now() < activeUntil) {
    const rms = Math.sqrt(chunk.reduce((s, x) => s + x*x, 0) / chunk.length);
    if (rms > 0.015) { speechStarted = true; silenceSince = Date.now(); }
    commandSamples.push(...chunk);
    if (!speechStarted && commandSamples.length > 8000) commandSamples.splice(0, commandSamples.length - 8000);
    if (speechStarted && (Date.now() - silenceSince > 850 || commandSamples.length >= 16000 * 12)) {
      const raw = wav(commandSamples); resetCommand(); busy = true; state('Entendendo sua voz…');
      const turn = generation;
      try {
        const result = await api('/api/transcribe', {audio: b64(raw)});
        if (turn !== generation) return;
        busy = false;
        if (result.text) await ask(result.text); else { activeUntil = Date.now() + 10000; idleState(); }
      } catch (e) { if (turn === generation) notice(e.message); }
      finally { if (turn === generation) { busy = false; idleState(); } }
    }
    return;
  }
  resetCommand(); idleState();
  if ($('voice-mode').value === 'esp') return;
  processLocal.ring = (processLocal.ring || []).concat(Array.from(chunk)).slice(-16000);
  if (processLocal.ring.length < 16000 || detectBusy || Date.now() - lastDetect < 250) return;
  detectBusy = true; lastDetect = Date.now(); const turn = generation;
  try {
    const result = await api('/api/detect', {audio: b64(wav(processLocal.ring))});
    if (turn !== generation || !enabled) return;
    hits = result.detected ? hits + 1 : 0;
    if (hits >= 2) { hits = 0; await wake(); }
  } catch (e) { if (turn === generation) { stopListening(); notice(e.message); } }
  finally { detectBusy = false; }
}
async function toggleListen() {
  if (enabled) { stopListening(); return; }
  const turn = generation; $('listen').disabled = true;
  try {
    if ($('voice-mode').value === 'browser') {
      if (!SR) throw new Error('Este navegador não oferece reconhecimento de voz. Use o modo local ou escreva sua pergunta.');
      if (!recognition) setupRecognition(); enabled = true; startRecognition();
    } else {
      const info = await api('/api/status');
      if (turn !== generation) return;
      if ($('voice-mode').value === 'local' && (!info.wake_model || !info.local_voice)) throw new Error('O modo local precisa do detector ONNX treinado e de um modelo Whisper configurado. Você já pode gravar exemplos em Minha voz.');
      if (!info.local_voice) throw new Error('Configure o Whisper neste PC para transcrever suas perguntas.');
      const opened = new Capture(); capture = opened; await opened.start(processLocal);
      if (turn !== generation || capture !== opened) { opened.stop(); return; }
      enabled = true;
    }
    $('listen').textContent = 'Desativar microfone'; $('mic-dot').classList.add('on'); idleState();
  } catch (e) { if (turn === generation) { stopListening(); notice(e.message); } }
  finally { $('listen').disabled = false; }
}
function stopListening() {
  enabled = false; recognition?.abort(); capture?.stop(); capture = null; activeUntil = 0;
  resetCommand(); processLocal.ring = []; hits = 0; $('listen').textContent = 'Ativar microfone'; $('mic-dot').classList.remove('on'); idleState();
}
async function stopAll() {
  generation++; ask.controller?.abort(); cancelSpeech(); stopListening(); busy = false; $('send').disabled = false;
  recordingCapture?.stop(); recordingCapture = null;
  const previous = session; session = crypto.randomUUID();
  try { await api('/api/reset', {session: previous}); } catch (_) {}
}
async function refresh() {
  const info = await api('/api/status');
  for (const key of ['ferris', 'other', 'noise']) $('count-' + key).textContent = info.recordings[key];
  $('model-state').textContent = info.wake_model ? 'Detector ONNX disponível. Valide com áudios de uma nova sessão.' : 'Modelo pessoal ainda não treinado. Comece reunindo seus exemplos.';
  if (!trainingChoice) $('training-mode').value = info.recording_sessions >= 4 ? 'sessions' : 'recordings';
  renderTraining(info.training);
  $('whisper-state').textContent = info.local_voice ? 'Whisper local configurado: suas perguntas são transcritas neste PC.' : 'Whisper ainda não configurado neste PC.';
  $('device-state').textContent = info.device?.connected ? 'ESP32 conectado por USB. Eventos por Wi-Fi também são aceitos quando configurados.' : 'USB não conectado. O modo Wi-Fi continua disponível quando configurado.';
  $('flash-esp32').disabled = !info.device?.available || info.training?.state === 'running';
  return info;
}
function renderTraining(job) {
  if (!job) return;
  const running = job.state === 'running';
  $('train-model').disabled = running;
  $('training-mode').disabled = running;
  if (running) $('flash-esp32').disabled = true;
  $('train-model').textContent = running ? 'Treinando…' : 'Treinar e usar modelo';
  $('training-state').textContent = job.message;
  const model = job.model, metrics = model?.test;
  $('training-metrics').textContent = metrics ? `${model.split_mode === 'recordings' ? 'Experimental. ' : ''}Teste reservado: ${metrics.tp} de ${metrics.tp + metrics.fn} exemplos Ferris reconhecidos; ${metrics.fp} de ${metrics.fp + metrics.tn} janelas negativas acionadas. Valide também com áudio contínuo novo.` : '';
  if (model) $('model-state').textContent = 'Detector ativo para testes no PC. ' + (model.split_mode === 'recordings' ? 'Versão experimental. ' : '') + 'Pesos exportados para o ESP32.';
  if (running && !trainingPolling) pollTraining();
}
async function pollTraining() {
  trainingPolling = true;
  try {
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const job = await api('/api/training'); renderTraining(job);
      if (job.state !== 'running') { await refresh(); break; }
    }
  } catch (e) {
    $('training-state').textContent = 'Não foi possível acompanhar o treino. Recarregue o painel: ' + e.message;
  } finally { trainingPolling = false; }
}
$('training-mode').onchange = () => { trainingChoice = true; };
$('train-model').onclick = async () => {
  $('train-model').disabled = true;
  try {
    renderTraining(await api('/api/train', {split_mode: $('training-mode').value, flash: $('flash-esp32').checked}));
  } catch (e) { $('training-state').textContent = e.message; $('train-model').disabled = false; }
};
async function openSettings() {
  try {
    const {settings: s} = await refresh();
    $('name').value = s.name; $('base-url').value = s.base_url; $('model').value = s.model; $('timezone').value = s.timezone;
  } catch (e) { $('connection-result').textContent = e.message; }
  $('settings').showModal();
}
async function saveSettings() {
  accessToken = $('access-token').value.trim();
  const data = Object.fromEntries(new FormData($('settings-form')));
  for (const [id, key] of [['api-key', 'api_key'], ['search-key', 'search_key']]) {
    if ($(id).value || $('clear-keys').checked) data[key] = $('clear-keys').checked ? '' : $(id).value;
  }
  await api('/api/settings', data); $('api-key').value = ''; $('search-key').value = ''; $('clear-keys').checked = false;
  return data;
}
$('open-settings').onclick = openSettings;
$('close-settings').onclick = () => $('settings').close();
$('settings-form').onsubmit = async (e) => {
  e.preventDefault(); try { await saveSettings(); $('settings').close(); notice('Conexão salva.'); await refresh(); } catch (e) { $('connection-result').textContent = e.message; }
};
$('test-connection').onclick = async () => {
  $('test-connection').disabled = true; $('connection-result').textContent = 'Conectando…';
  try {
    await saveSettings(); const {models} = await api('/api/models'); $('model-list').replaceChildren();
    models.forEach(id => { const o = document.createElement('option'); o.value = id; $('model-list').append(o); });
    if (!$('model').value && models.length) $('model').value = models.find(id => id === 'ferris-gemma') || models.find(id => /gemma/i.test(id)) || models[0];
    $('connection-result').textContent = models.length ? `${models.length} modelo(s) disponível(is). Escolha e salve.` : 'Servidor conectado, mas sem modelos disponíveis.';
    $('connection-dot').classList.toggle('on', models.length > 0);
  } catch (e) { $('connection-result').textContent = e.message; $('connection-dot').classList.remove('on'); }
  finally { $('test-connection').disabled = false; }
};
$('listen').onclick = toggleListen; $('wake').onclick = () => wake(); $('stop').onclick = stopAll;
$('voice-mode').onchange = () => {
  stopListening();
  $('voice-note').textContent = {esp: 'O ESP32 detecta “Ferris”. O microfone deste PC capta a pergunta; Whisper transcreve e Gemma responde aqui.', local: 'Teste do detector ONNX e transcrição Whisper neste PC. Nenhum áudio vai ao LM Studio.', browser: 'No modo navegador, o serviço de voz pode processar áudio online. A ativação por “Ferris” aqui é provisória, por transcrição.'}[$('voice-mode').value];
};
$('chat-form').onsubmit = (e) => { e.preventDefault(); if (busy) return; const text = $('message').value.trim(); if (text) { $('message').value = ''; ask(text, $('search').checked); } };
$('message').onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('chat-form').requestSubmit(); } };
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => ask(b.dataset.prompt));
$('clear').onclick = async () => { await stopAll(); $('messages').replaceChildren(); $('latency').textContent = ''; };
for (const tab of ['chat', 'voice']) $('tab-' + tab).onclick = () => {
  for (const t of ['chat', 'voice']) { $('tab-' + t).classList.toggle('selected', tab === t); $('tab-' + t).setAttribute('aria-selected', String(t === tab)); $(t + '-pane').hidden = tab !== t; }
};
async function saveRecording(buffer, label, group) {
  await api('/api/recordings', {audio: b64(buffer), label, group}); await refresh();
}
$('record').onclick = async () => {
  if (!$('group').checkValidity()) { $('group').reportValidity(); return; }
  const label = $('label').value, group = $('group').value, recording = new Capture(), samples = [];
  await stopAll(); const turn = generation; recordingCapture = recording; $('record').disabled = true; $('listen').disabled = true;
  $('record-level').value = -60; $('record-level-text').textContent = 'Aguardando áudio…';
  try {
    await recording.start(chunk => {
      samples.push(...chunk);
      const rms = Math.sqrt(chunk.reduce((sum, x) => sum + x*x, 0) / chunk.length);
      const db = rms > 0 ? 20 * Math.log10(rms) : -96;
      $('record-level').value = Math.max(-60, db);
      $('record-level-text').textContent = rms > 0 ? `${db.toFixed(0)} dBFS` : 'Sem sinal — confira o mute e o microfone.';
    });
    $('record-input').textContent = 'Microfone: ' + (recording.stream.getAudioTracks()[0]?.label || 'padrão do navegador');
    $('record-state').textContent = label === 'noise' ? 'Gravando o ambiente…' : 'Gravando… diga a palavra agora.';
    await new Promise(resolve => setTimeout(resolve, 2000)); recording.stop();
    if (turn !== generation) { $('record-state').textContent = 'Gravação cancelada.'; return; }
    await saveRecording(wav(samples), label, group); $('record-state').textContent = 'Exemplo salvo. Pode gravar o próximo.';
  } catch (e) { $('record-state').textContent = e.message; }
  finally { recording.stop(); if (recordingCapture === recording) recordingCapture = null; $('record').disabled = false; $('listen').disabled = false; }
};
$('upload').onchange = async () => {
  $('upload').disabled = true;
  try {
    for (const file of $('upload').files) {
      if (file.size > 200000) throw new Error('Use arquivos WAV de até 5 segundos (160 kB de áudio).');
      await saveRecording(await file.arrayBuffer(), $('label').value, $('group').value);
    }
    $('record-state').textContent = 'Arquivos importados.';
  } catch (e) { $('record-state').textContent = e.message; }
  finally { $('upload').value = ''; $('upload').disabled = false; }
};
$('date').textContent = new Intl.DateTimeFormat('pt-BR', {weekday: 'long', day: 'numeric', month: 'long'}).format(new Date());
refresh().catch(e => notice(e.message));
let pollBusy = false;
setInterval(async () => {
  if (pollBusy) return; pollBusy = true;
  try {
    const data = await api(`/api/events?since=${eventId}`);
    if (!polled) { eventId = data.latest; polled = true; return; }
    for (const event of data.events) {
      eventId = Math.max(eventId, event.id);
      if (event.device !== 'desktop' && Date.now()/1000 - event.timestamp < 10 && !busy && !speaking) await wake(event);
    }
  } catch (_) { /* Connection errors are presented by user actions. */ }
  finally { pollBusy = false; }
}, 1500);
window.addEventListener('pagehide', () => { stopListening(); cancelSpeech(); });
