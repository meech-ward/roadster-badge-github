// One interpreter per boot. Main-thread deadlines can terminate runaway Python.
let mp;
let input = { ticks: 0, buttons: [], pad: [], imu: [0, 0, 16384, 0, 0, 0] };
let frame;
let events = [];
let index;
const digest = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
function parents(path) {
  let current = '';
  for (const part of path.split('/').slice(1, -1)) {
    current += '/' + part;
    if (!mp.FS.analyzePath(current).exists) mp.FS.mkdir(current);
  }
}
function readonly(path) {
  for (const name of mp.FS.readdir(path).filter(x => x !== '.' && x !== '..')) {
    const p = path + '/' + name;
    if (mp.FS.isDir(mp.FS.stat(p).mode)) readonly(p);
    else mp.FS.chmod(p, 0o444);
  }
  mp.FS.chmod(path, 0o555);
}
function stateFiles(path = '/state') {
  const files = {};
  for (const name of mp.FS.readdir(path).filter(x => x !== '.' && x !== '..')) {
    const p = path + '/' + name;
    if (mp.FS.isDir(mp.FS.stat(p).mode)) Object.assign(files, stateFiles(p));
    else files[p] = [...mp.FS.readFile(p)];
  }
  return files;
}
async function handle(type, data) {
  if (type === 'init') {
    input = { ...input, ...data.inputs };
    // Virtual time controls Python timers; runtime/supervisor deadlines use the
    // browser main thread's clock. This is not a CPU-speed model.
    Date.now = () => Date.UTC(2026, 9, 23) + input.ticks;
    index = data.index;
    for (const name of ['micropython.mjs', 'micropython.wasm']) {
      const bytes = await (await fetch('/runtime/' + name)).arrayBuffer();
      if (await digest(bytes) !== index.runtime[name]) throw new Error('Runtime hash mismatch: ' + name);
    }
    const runtimeUrl = new URL('/runtime/micropython.mjs', self.location.origin).href;
    const { loadMicroPython } = await import(/* @vite-ignore */ runtimeUrl);
    mp = await loadMicroPython({ heapsize: 8 * 1024 * 1024,
      stdout: text => postMessage({ log: text }), stderr: text => postMessage({ log: text }) });
    mp.registerJsModule('sim_host', {
      inputs: () => JSON.stringify(input),
      native_pico3d: () => Boolean(index.capabilities.pico3d),
      event: (kind, value) => events.push({ kind, value }),
      present: (width, height) => { frame = { width, height }; },
    });
    for (const file of index.files.filter(x => !x.path.startsWith('/system/apps/') || x.path.startsWith('/system/apps/' + data.slug + '/'))) {
      const response = await fetch(file.url);
      if (!response.ok) throw new Error('Missing file: ' + file.path);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (await digest(bytes) !== file.sha256) throw new Error('Source hash mismatch: ' + file.path);
      parents(file.path);
      mp.FS.writeFile(file.path, bytes);
    }
    mp.FS.mkdir('/state');
    for (const [path, bytes] of Object.entries(data.state ?? {})) {
      if (!path.startsWith('/state/') || path.split('/').includes('..')) throw new Error('Invalid state path');
      parents(path); mp.FS.writeFile(path, new Uint8Array(bytes));
    }
    readonly('/system'); readonly('/rom'); mp.FS.ignorePermissions = false;
    await mp.runPython(`import sys\nsys.path.insert(0, '/sim')\nfor _name in ('_input', 'st7789', 'powman'):\n    sys.modules[_name] = __import__('/sim/' + _name)\nimport u26_runtime\nu26_runtime.install()\nimport random\nrandom.seed(${Number(data.seed ?? 1)})\nu26_runtime.launch_app(${JSON.stringify(data.slug)})`);
    return { profile: index.profile, upstream: index.upstream, apps: index.apps };
  }
  if (!mp) throw new Error('Boot an app first');
  if (type === 'step') {
    input = { ...input, ...data, ticks: input.ticks + data.dt };
    delete input.dt;
    delete input.appControls;
    frame = null;
    // Explicit app-level controls are ordinary Python data, independent of the
    // synthetic badge inputs. Only apps that provide this hook opt in.
    if (data.appControls !== undefined) {
      await mp.runPython('import json\nu26_runtime.app.set_controls(json.loads(' + JSON.stringify(JSON.stringify(data.appControls)) + '))');
    }
    await mp.runPython('u26_runtime.step()');
    // JSON through a global avoids implementation-dependent proxy conversion.
    await mp.runPython("import json\n_sim_control_status = getattr(u26_runtime.app, 'control_status', None)\n_sim_status = json.dumps({'finished': u26_runtime.finished, 'reason': u26_runtime.reason, 'ticks': badge.ticks, 'controlStatus': _sim_control_status() if _sim_control_status else None})");
    const outcome = JSON.parse(mp.globals.get('_sim_status'));
    if (frame) outcome.frame = { ...frame, bytes: mp.FS.readFile('/frame.rgba') };
    outcome.events = events.splice(0);
    return outcome;
  }
  if (type === 'appAction') {
    await mp.runPython('import json\n_sim_action = json.loads(' + JSON.stringify(JSON.stringify(data)) + ')\n_sim_action_result = json.dumps(u26_runtime.app.control_action(_sim_action["action"], _sim_action.get("value")))');
    return JSON.parse(mp.globals.get('_sim_action_result'));
  }
  if (type === 'inspect') {
    await mp.runPython('_sim_inspect = u26_runtime.inspect(' + JSON.stringify(data.expression) + ')');
    return JSON.parse(mp.globals.get('_sim_inspect'));
  }
  if (type === 'exec') { await mp.runPython(data.code); return null; }
  if (type === 'readFile') return mp.FS.readFile(data.path);
  if (type === 'state') return stateFiles();
  throw new Error('Unknown operation: ' + type);
}
// Serialize all VM access: Asyncify permits one in-flight Python call.
let chain = Promise.resolve();
self.onmessage = ({ data: { id, type, data } }) => {
  chain = chain.then(async () => {
    try { postMessage({ id, ok: true, value: await handle(type, data ?? {}) }); }
    catch (error) { postMessage({ id, ok: false, error: String(error.stack ?? error) }); }
  });
};
