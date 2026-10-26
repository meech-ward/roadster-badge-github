import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, readdir, cp, rm } from 'node:fs/promises';
import { dirname, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(here, '..');
const pin = 'eb3b3fbf27e6e61635aef5de11ab17e6303e8c20';
const url = 'https://github.com/pimoroni/badgeware-web-simulator.git';
const source = resolve(root, '.cache/upstream/badgeware-web-simulator');
await readFile(resolve(root, '.cache/pico3d-runtime/probe-result.json')).catch(() => {
  throw new Error('Native runtime is not built. Run npm run build:runtime first; see README.md.');
});
const run = (...args) => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
await mkdir(dirname(source), { recursive: true });
try { run('-C', source, 'rev-parse', '--git-dir'); }
catch { run('clone', url, source); }
if (run('-C', source, 'status', '--porcelain')) throw new Error('Upstream cache has local changes; refusing inaccurate provenance: ' + source);
if (run('-C', source, 'rev-parse', 'HEAD') !== pin) {
  run('-C', source, 'fetch', 'origin', pin);
  run('-C', source, 'checkout', '--detach', pin);
}
// Also materialize a previously created --no-checkout cache at the right HEAD.
run('-C', source, 'checkout', '--detach', pin);
// Never take credentials or factory dumps as simulator input. Only these named
// upstream paths and the explicit apps/ tree are staged.
const generated = resolve(here, 'public/generated');
await rm(generated, { recursive: true, force: true });
await mkdir(generated, { recursive: true });
await mkdir(resolve(here, 'public/runtime'), { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const native3d = true;
if (process.env.BADGE_RUNTIME && process.env.BADGE_RUNTIME !== 'pico3d') throw new Error('Roadster requires BADGE_RUNTIME=pico3d');
const runtimeSource = resolve(root, '.cache/pico3d-runtime');
const runtime = {};
for (const name of ['micropython.mjs', 'micropython.wasm']) {
  const bytes = await readFile(resolve(runtimeSource, name)).catch(error => {
    if (native3d) throw new Error('Build native 3D first: npm run build:runtime');
    throw error;
  });
  await writeFile(resolve(here, 'public/runtime', name), bytes);
  runtime[name] = hash(bytes);
}
let nativeProvenance = null;
if (native3d) {
  nativeProvenance = JSON.parse(await readFile(resolve(runtimeSource, 'provenance.json'), 'utf8'));
  const probe = JSON.parse(await readFile(resolve(runtimeSource, 'probe-result.json'), 'utf8'));
  if (!probe.passed || !probe.scene_mesh_render_passed) throw new Error('Native 3D render probe has not passed');
  for (const [name, value] of Object.entries(runtime)) {
    if (nativeProvenance.sha256[name] !== value || probe.sha256[name] !== value) throw new Error('Native build/probe hash mismatch: ' + name);
  }
}
await cp(resolve(source, 'LICENSE'), resolve(here, 'public/runtime/LICENSE'));
for (const name of ['licenses', 'THIRD_PARTY_NOTICES.txt', 'provenance.json']) {
  await cp(resolve(runtimeSource, name), resolve(here, 'public/runtime', name), { recursive: true });
}
const files = [];
async function stage(path, destination, patch = null) {
  let bytes = await readFile(path);
  if (patch) bytes = Buffer.from(patch(bytes.toString('utf8')));
  const output = resolve(generated, destination.slice(1));
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, bytes);
  files.push({ path: destination, url: '/generated/' + destination.slice(1), sha256: hash(bytes) });
}
for (const name of await readdir(resolve(source, 'simulator/filesystem/badgeware'))) {
  if (!name.endsWith('.py') || name === 'rtc.py') continue;
  await stage(resolve(source, 'simulator/filesystem/badgeware', name), '/sim/badgeware/' + name,
    name === '__init__.py' ? text => {
      const anchor = '__import__("badgeware/rtc")';
      if (text.split(anchor).length !== 2) throw new Error('upstream RTC import anchor changed');
      return text.replace(anchor, '# Universe has no external RTC; deliberately omitted by profile.');
    } : null);
}
for (const name of await readdir(resolve(here, 'compat'))) {
  if (name.endsWith('.py')) await stage(resolve(here, 'compat', name), '/sim/' + name);
}
// Keep all upstream font bytes, since picovector's font namespace opens them by name.
async function walk(path, visitor) {
  for (const entry of await readdir(path, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error('symlink forbidden: ' + path + '/' + entry.name);
    if (entry.name.startsWith('.') || entry.name === '__pycache__') continue;
    const full = resolve(path, entry.name);
    if (entry.isDirectory()) await walk(full, visitor);
    else await visitor(full);
  }
}
const fonts = resolve(source, 'simulator/filesystem/rom/fonts');
await walk(fonts, path => stage(path, '/rom/fonts/' + relative(fonts, path)));
const apps = [];
const appRoot = resolve(root, 'apps');
for (const entry of await readdir(appRoot, { withFileTypes: true })) {
  if (!entry.isDirectory() || !/^[a-z][a-z0-9_-]{0,63}$/.test(entry.name)) continue;
  await readFile(resolve(appRoot, entry.name, '__init__.py'));
  apps.push(entry.name);
  await walk(resolve(appRoot, entry.name), async path => {
    if (/secrets|credentials|\.env/i.test(path.split('/').at(-1))) throw new Error('sensitive file forbidden: ' + path);
    if (/\.(pyc|zip)$/.test(path)) return;
    await stage(path, '/system/apps/' + relative(appRoot, path));
  });
}
await writeFile(resolve(generated, 'index.json'), JSON.stringify({ profile: 'universe-2026-preview-v1', upstream: { url, commit: pin }, runtime, nativeProvenance, capabilities: { pico3d: native3d }, apps, files }, null, 2));
console.log(`Prepared ${apps.length} apps; MicroPython + native ${native3d ? '2D/3D' : '2D'} graphics. Profile ${native3d ? 'pico3d' : 'default'}.`);
