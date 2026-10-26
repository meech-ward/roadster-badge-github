import { createRoadsterControls } from './roadster-controls.js';

const canvas = document.querySelector('canvas');
const context = canvas.getContext('2d');
const status = document.querySelector('#status');
const output = document.querySelector('#output');
let worker;
let sequence = 0;
let pending = new Map();
let generation = 0;
let running = false;
let booted = false;
let lastFrame = null;
let inputs = { buttons: [], pad: [], imu: [0, 0, 16384, 0, 0, 0] };
let currentApp;
let appControls;
let roadsterControls;
let tickChain = Promise.resolve();
const testMode = new URLSearchParams(location.search).get('test') === '1';
function log(text) { output.textContent += text + '\n'; output.scrollTop = output.scrollHeight; }
function request(type, data) {
  if (!worker) return Promise.reject(new Error('Boot an app first'));
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('Interpreter exceeded 5-second deadline; boot to recover'));
      stopWorker(); status.textContent = 'Interpreter stopped by watchdog';
    }, 5000);
    pending.set(id, { resolve, reject, timer });
    worker.postMessage({ id, type, data });
  });
}
function stopWorker() {
  generation++; worker?.terminate(); worker = null; running = false; booted = false;
  roadsterControls?.clear(true);
  appControls = undefined;
  lastFrame = null; context.clearRect(0, 0, canvas.width, canvas.height);
  for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error('Interpreter stopped')); }
  pending.clear();
}
function paint(frame) {
  canvas.width = frame.width; canvas.height = frame.height;
  context.putImageData(new ImageData(new Uint8ClampedArray(frame.bytes), frame.width, frame.height), 0, 0);
  lastFrame = frame;
}
const api = {
  async boot(slug, options = {}) {
    stopWorker();
    const bootGeneration = generation;
    tickChain = Promise.resolve();
    output.textContent = ''; status.textContent = 'Loading MicroPython…';
    const index = await (await fetch('/generated/index.json', { cache: 'no-store' })).json();
    if (bootGeneration !== generation) throw new Error('Boot superseded');
    if (!index.apps.includes(slug)) throw new Error('Unknown app: ' + slug);
    worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => {
      if ('log' in data) { log(data.log); return; }
      const p = pending.get(data.id);
      if (!p) return;
      pending.delete(data.id); clearTimeout(p.timer);
      data.ok ? p.resolve(data.value) : p.reject(new Error(data.error));
    };
    worker.onerror = event => { log(event.message); status.textContent = 'Worker error'; stopWorker(); };
    inputs = { buttons: [], pad: [], imu: [0, 0, 16384, 0, 0, 0] };
    currentApp = slug;
    roadsterControls.activate(slug);
    const result = await request('init', { slug, index, state: options.state, seed: options.seed, inputs });
    if (bootGeneration !== generation) throw new Error('Boot superseded');
    booted = true;
    document.querySelector('#app').value = slug;
    status.textContent = slug === 'roadster_sandbox' ? 'Sandbox ready · keyboard + buttons · native 3D' : `Ready · synthetic Universe inputs · native ${index.capabilities.pico3d ? '2D + 3D' : '2D'} graphics`;
    await api.step(2);
    return result;
  },
  step(frames = 1, options = {}) {
    if (!Number.isInteger(frames) || frames < 1 || frames > 3600) return Promise.reject(new Error('frames must be 1..3600'));
    const dt = options.dt ?? 33;
    if (!Number.isInteger(dt) || dt < 0 || dt > 1000) return Promise.reject(new Error('dt must be 0..1000ms'));
    const job = tickChain.then(async () => {
      const stepGeneration = generation;
      const state = { ...inputs, ...(appControls === undefined ? {} : { appControls }), ...options, dt };
      delete state.frames;
      for (const source of ['buttons', 'pad']) {
        if (!state[source].every(x => ['LEFT', 'RIGHT', 'SELECT', 'BACK', 'MENU', 'HOME', 'UP', 'DOWN'].includes(x))) throw new Error('Unknown logical input');
      }
      if (state.imu.length !== 6 || !state.imu.every(Number.isFinite)) throw new Error('imu must contain six raw readings');
      let result;
      for (let i = 0; i < frames; i++) {
        if (stepGeneration !== generation) throw new Error('Boot changed during step');
        result = await request('step', state);
        if (result.frame) paint(result.frame);
        roadsterControls.status(result.controlStatus ?? null);
        if (result.finished) { running = false; status.textContent = `App ended: ${result.reason}`; break; }
      }
      return result;
    });
    tickChain = job.catch(() => {});
    return job;
  },
  async tap(button, source = 'buttons') {
    if (!['buttons', 'pad'].includes(source)) throw new Error('source must be buttons or pad');
    await api.step(2, { [source]: [button] });
    return api.step(2, { [source]: [] });
  },
  setAppControls(values) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('App controls must be an object');
    appControls = JSON.parse(JSON.stringify(values));
  },
  appAction(action, value = null) {
    if (typeof action !== 'string') return Promise.reject(new Error('App action must be a string'));
    const controlRevision = roadsterControls.beforeAction(action, value);
    const actionGeneration = generation;
    const job = tickChain.then(async () => {
      if (actionGeneration !== generation) throw new Error('Boot changed during app action');
      const result = await request('appAction', { action, value });
      roadsterControls.afterAction(action, result, controlRevision);
      return result;
    });
    tickChain = job.catch(() => {});
    return job;
  },
  inspect: expression => request('inspect', { expression }),
  exec: code => request('exec', { code }),
  readFile: path => request('readFile', { path }),
  exportState: () => request('state'),
  snapshot: () => canvas.toDataURL('image/png'),
  pause() { running = false; document.querySelector('#play').textContent = 'Play'; },
  play() {
    if (running || !booted) return;
    running = true; document.querySelector('#play').textContent = 'Pause';
    const playGeneration = generation;
    async function next() {
      if (!running || playGeneration !== generation) return;
      try { await api.step(); }
      catch (error) { log(error.message); running = false; status.textContent = 'Runtime error'; }
      if (running) setTimeout(next, 33);
    }
    next();
  },
  get app() { return currentApp; },
  get frame() { return lastFrame && { width: lastFrame.width, height: lastFrame.height }; },
};
window.badge = api;
roadsterControls = createRoadsterControls(api, error => log(error.message));
const index = await (await fetch('/generated/index.json')).json();
if (index.capabilities.pico3d) document.querySelector('#fidelity').textContent = 'Native MicroPython + PicoVector 2D + pico3d. Logical buttons and IMU are synthetic. Host rendering does not establish badge speed, radios, battery life, or physical touch.';
for (const slug of index.apps) document.querySelector('#app').add(new Option(slug.replaceAll('_', ' '), slug));
const selectedApp = new URLSearchParams(location.search).get('app');
if (index.apps.includes(selectedApp)) document.querySelector('#app').value = selectedApp;
async function bootSelected() {
  api.pause();
  try { await api.boot(document.querySelector('#app').value); if (!testMode) api.play(); }
  catch (error) { log(error.message); status.textContent = 'Boot failed'; }
}
document.querySelector('#boot').onclick = bootSelected;
document.querySelector('#play').onclick = () => running ? api.pause() : api.play();
document.querySelector('#step').onclick = () => { api.pause(); api.step().catch(error => log(error.message)); };
document.querySelector('#screenshot').onclick = () => {
  const anchor = document.createElement('a'); anchor.href = api.snapshot(); anchor.download = `${currentApp}.png`; anchor.click();
};
function setHeld(button, down) {
  const source = document.querySelector('#source').value;
  const next = new Set(inputs[source]); down ? next.add(button) : next.delete(button);
  inputs[source] = [...next];
}
for (const button of document.querySelectorAll('[data-button]')) {
  button.onpointerdown = event => { event.preventDefault(); button.setPointerCapture(event.pointerId); setHeld(button.dataset.button, true); };
  button.onpointerup = button.onpointercancel = () => setHeld(button.dataset.button, false);
}
const keys = { ArrowLeft: 'LEFT', ArrowRight: 'RIGHT', ArrowUp: 'UP', ArrowDown: 'DOWN', ' ': 'SELECT', Enter: 'SELECT', Backspace: 'BACK', m: 'MENU', Escape: 'HOME' };
function key(event, down) {
  if (currentApp === 'roadster_sandbox') return;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  if (keys[event.key]) { event.preventDefault(); setHeld(keys[event.key], down); }
}
addEventListener('keydown', event => key(event, true)); addEventListener('keyup', event => key(event, false));
function releaseInputs() { inputs.buttons = []; inputs.pad = []; roadsterControls.clear(); }
addEventListener('blur', releaseInputs);
document.addEventListener('visibilitychange', () => { if (document.hidden) releaseInputs(); });
document.querySelector('#source').onchange = () => { inputs.buttons = []; inputs.pad = []; };
for (const axis of ['ax', 'ay']) document.querySelector('#' + axis).oninput = event => {
  inputs.imu[axis === 'ax' ? 0 : 1] = Number(event.target.value);
  document.querySelector('#' + axis + '-value').textContent = event.target.value;
};
document.querySelector('#inspect').onclick = async () => {
  try { document.querySelector('#value').textContent = JSON.stringify(await api.inspect(document.querySelector('#expression').value), null, 2); }
  catch (error) { document.querySelector('#value').textContent = error.message; }
};
window.badgeReady = true;
if (!testMode) await bootSelected();
