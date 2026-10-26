import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-tilt-reliability');
const degrees = 180 / Math.PI;
const grip = [-.015381114, -.9128306, -.408049];
const length = Math.hypot(...grip);
const center = grip.map(value => Math.round(value / length * 16384)).concat([0, 0, 0]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) => badge.step(frames, options),
  { frames, options: { dt: 100, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const state = page => inspect(page, `{'app':badge_snapshot(), 'gauge':hud_state.get('tilt_gauge'),
    'airborne':sim.airborne,'contacts':[sim.contact_count,sim.chassis_contacts],
    'signal':physical_motion_signal,'forces':list(sim.nozzle_forces),
    'rates':list(_reliable_body_rates(sim.q,sim.omega)), 'q':list(sim.q),
    'height':sim.y,'up':sim.basis()[1][1]}`);

function imuAt(model, axis, angle, rate = 0) {
  const n = model.neutral, b = model[axis === 'roll' ? 'basis_x' : 'basis_y'];
  const radians = angle / degrees;
  const gravity = n.map((v, i) => v * Math.cos(radians) + b[i] * Math.sin(radians));
  const tangent = n.map((v, i) => -v * Math.sin(radians) + b[i] * Math.cos(radians));
  return gravity.map(v => Math.round(v * 16384))
    .concat(cross(tangent, gravity).map(v => Math.round(v * rate / .00875)));
}

async function calibrate(page) {
  await exec(page, 'motion_input.rotated = True\nfrom physics import q_inverse_rotate as _reliable_body_rates');
  await action(page, 'center');
  await step(page, 48, { dt: 25 });
  const model = (await state(page)).app.motion.model;
  expect(model.calibrated).toBe(true);
  expect(model.mode).toBe('both');
  return model;
}

async function fly(page) {
  await exec(page, `
from stunt_feedback import _body_rotation as _reliable_body_delta
sim.x,sim.y,sim.z = 0.,1000.,0.
sim.vx,sim.vy,sim.vz = 0.,0.,0.
sim.q = (1.,0.,0.,0.)
sim.omega = [0.,0.,0.]
stunt.reset(sim)
_reliable_rows = []
_reliable_previous = sim.q
_reliable_phase = 'quiet'
if '_reliable_observer' not in globals():
    _reliable_observer = observe_physics
def _observe_reliable(dt,simulation,values):
    global _reliable_previous
    _reliable_observer(dt,simulation,values)
    delta = _reliable_body_delta(_reliable_previous,simulation.q)
    _reliable_previous = simulation.q
    _reliable_rows.append([_reliable_phase,simulation.time,list(delta),
        list(_reliable_body_rates(simulation.q,simulation.omega)),
        list(simulation.nozzle_forces),simulation.contact_count,simulation.chassis_contacts])
observe_physics = _observe_reliable
`);
  await step(page, 8, { dt: 180 });
  await step(page, 5, { dt: 100 });
  expect((await state(page)).app.motion.model.armed).toBe(true);
  expect((await state(page)).app.motion.model.flick_ready).toBe(true);
}

async function poll(page, model, axis, angle, rate, dt, phase) {
  await exec(page, `_reliable_phase = ${JSON.stringify(phase)}`);
  await step(page, 1, { dt, imu: imuAt(model, axis, angle, rate) });
  return { input: { axis, angle, rate, dt, phase }, ...(await state(page)) };
}

async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const sourceHashes = Object.fromEntries(manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')).map(row => [row.path, row.sha256]));
  const testSha256 = createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex');
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM app, IMU range conversion/filter, input lifecycle, force mixer, fixed physics and native pico3d pixels. Synthetic sensor traces and test-only rotated=True use recorded face-facing calibration. An elevated pose isolates flight controls; no input or app module is mocked. Injected framework milliseconds are not physical badge performance or human hand-feel evidence. Input samples are observed one framework poll after injection.',
    testSha256, sourceHashes, ...data, final: await state(page), physicsRows: await inspect(page, '_reliable_rows'),
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 2, { dt: 25 });
});

test('a deliberate vertical lean stays live while its flick intent is unresolved', async ({ page }) => {
  const model = await calibrate(page), samples = [];
  await fly(page);
  for (const [angle, rate, phase] of [[0,0,'quiet'],[6,240,'move'],[12,240,'move'],[18,240,'move'],[18,0,'held'],[18,0,'held'],[18,0,'held'],[18,0,'held']]) {
    samples.push(await poll(page, model, 'pitch', angle, rate, 25, phase));
  }
  const unresolved = samples.filter(s => s.app.motion.model.armed && s.app.motion.model.tilt_valid &&
    Math.abs(s.app.motion.model.tilt_y) > .15 && s.app.motion.model.flick_pending);
  const blocked = unresolved.filter(s => Math.abs(s.signal[1]) < .001 && Math.abs(s.signal[3]) < .001);
  await save(page, 'vertical-intent-tilt', { model, samples, unresolvedCount: unresolved.length, blockedCount: blocked.length });
  expect(unresolved.length, 'The trace must actually exercise pending vertical intent').toBeGreaterThan(0);
  expect(blocked, 'Visible deliberate pitch must not be completely withheld by the flick recognizer').toHaveLength(0);
});

test('ordinary moving tilt remains usable across 80 to 500 millisecond frames', async ({ page }) => {
  const model = await calibrate(page), trials = [];
  for (const [axis, sign] of [['roll', 1], ['roll', -1], ['pitch', 1], ['pitch', -1]]) {
    await fly(page);
    const samples = [];
    let angle = 0;
    for (const dt of [80,120,180,140,220,500]) {
      angle += sign*60*dt/1000;
      samples.push(await poll(page, model, axis, angle, sign*60, dt, 'moving'));
    }
    for (const dt of [180,100,220,80,140,180]) samples.push(await poll(page, model, axis, angle, 0, dt, 'held'));
    const missing = samples.filter(s => Math.max(Math.abs(s.app.motion.model.rate_x),
      Math.abs(s.app.motion.model.rate_y)) > 30 && !s.app.motion.model.tilt_valid);
    const resumed = samples.at(-1);
    trials.push({ axis, sign, samples, missingCount: missing.length });
    expect(resumed.app.motion.model.armed).toBe(true);
    expect(resumed.app.rotation.motion_active[axis === 'roll' ? 0 : 1]).toBe(true);
  }
  await save(page, 'variable-cadence-moving-tilt', { model, trials });
  expect(trials.flatMap(t => t.samples.filter(s =>
    Math.max(Math.abs(s.app.motion.model.rate_x), Math.abs(s.app.motion.model.rate_y)) > 30 && !s.app.motion.model.tilt_valid)),
    'Plausible ordinary moving gravity samples must not lose all tilt control solely because a render took over150ms').toHaveLength(0);
});

test('small visible lean makes sustained rotation instead of being trapped by center assist', async ({ page }) => {
  const model = await calibrate(page), trials = [];
  for (const angle of [6,7,8,10]) {
    await fly(page);
    // A gradual40deg/s lean avoids flick thresholds; the held sample is then
    // observed at ordinary slow badge cadence long enough to see its effect.
    for (let lean = 1; lean <= angle; lean++) await poll(page, model, 'roll', lean, 40, 25, 'lean');
    await step(page, 12, { dt: 100, imu: imuAt(model, 'roll', angle) });
    await exec(page, "_reliable_rows = []\n_reliable_phase = 'held'");
    await step(page, 60, { dt: 100, imu: imuAt(model, 'roll', angle) });
    const snapshot = await state(page), rows = await inspect(page, '_reliable_rows');
    const rotationDegrees = rows.reduce((sum, row) => sum + row[2][1]*degrees, 0);
    trials.push({ angle, snapshot, rotationDegrees });
  }
  await save(page, 'small-lean-center-assist', { model, trials });
  for (const trial of trials.filter(t => t.angle >= 7)) {
    expect(trial.rotationDegrees, `${trial.angle}deg lean should request ongoing rotation`).toBeGreaterThan(20);
  }
});

test('natural landing and a second lift retain tilt, and menu rearming is visible and recoverable', async ({ page }) => {
  test.setTimeout(120000);
  const model = await calibrate(page), snapshots = {};
  // No flight fixture for the takeoff/landing sequence: preserve ordinary
  // contacts and use the actual inner-right lift pad through the input adapter.
  await exec(page, '_reliable_rows = []');
  await step(page, 12, { dt: 100, pad: ['SELECT'] });
  snapshots.firstLift = await state(page);
  expect(snapshots.firstLift.airborne).toBe(true);
  await step(page, 140, { dt: 100 });
  snapshots.landed = await state(page);
  expect(snapshots.landed.airborne).toBe(false);
  await step(page, 12, { dt: 100, pad: ['SELECT'] });
  await step(page, 2, { dt: 100 });
  snapshots.secondLift = await state(page);
  expect(snapshots.secondLift.airborne).toBe(true);
  await step(page, 8, { dt: 100, imu: imuAt(model, 'roll', 22) });
  snapshots.secondTilt = await state(page);
  expect(snapshots.secondTilt.app.motion.model.armed).toBe(true);
  expect(snapshots.secondTilt.app.driving.nozzles.some(value => value > 0)).toBe(true);
  await fly(page);
  await action(page, 'help');
  await step(page, 4, { dt: 180, imu: imuAt(model, 'roll', 22) });
  snapshots.help = await state(page);
  expect(snapshots.help.app.motion.model.armed).toBe(false);
  await action(page, 'help', false);
  await step(page, 4, { dt: 180, imu: imuAt(model, 'roll', 22) });
  snapshots.closedLeaning = await state(page);
  expect(snapshots.closedLeaning.app.motion_neutral_required).toBe(false);
  await step(page, 6, { dt: 180 });
  snapshots.rearmed = await state(page);
  expect(snapshots.rearmed.app.motion_neutral_required).toBe(false);
  await step(page, 8, { dt: 180, imu: imuAt(model, 'roll', -22) });
  snapshots.resumed = await state(page);
  expect(snapshots.resumed.app.motion.model.armed).toBe(true);
  expect(snapshots.resumed.app.driving.nozzles.some(value => value > 0)).toBe(true);
  await save(page, 'landing-menu-recovery', { model, snapshots });
});

test('closing an overlay at a five-degree lean resumes fresh tilt without hunting exact neutral', async ({ page }) => {
  const model = await calibrate(page), trials = [];
  for (const axis of ['roll', 'pitch']) {
    await fly(page);
    await action(page, 'help');
    await step(page, 4, { dt: 180, imu: imuAt(model, axis, 5) });
    const overlay = await state(page);
    expect(overlay.app.motion.model.armed).toBe(false);
    expect(overlay.app.driving.nozzles).toEqual(Array(10).fill(0));
    await action(page, 'help', false);
    await step(page, 4, { dt: 180, imu: imuAt(model, axis, 5) });
    const smallLean = await state(page);
    await step(page, 6, { dt: 180, imu: imuAt(model, axis, 24) });
    const deliberateLean = await state(page);
    trials.push({ axis, overlay, smallLean, deliberateLean });
  }
  await save(page, 'held-lean-menu-resume', { model, trials });
  for (const { axis, smallLean, deliberateLean } of trials) {
    for (const snapshot of [smallLean, deliberateLean]) {
      expect(snapshot.app.motion_neutral_required, axis).toBe(false);
      expect(snapshot.app.motion.model.armed, axis).toBe(true);
      expect(snapshot.gauge.status, axis).not.toBe('SENSE');
      expect(snapshot.app.driving.nozzles.some(value => value > 0), axis).toBe(true);
    }
    expect(deliberateLean.app.rotation.motion_targets[axis === 'roll' ? 0 : 1]).toBeGreaterThan(.8);
  }
});

test('one clipped gyro poll cannot hide a flick or corrupt the next valid tilt', async ({ page }) => {
  const model = await calibrate(page);
  await fly(page);
  await step(page, 8, { dt: 25 });
  const before = await state(page);
  // Preview injection is stock-equivalent units. The real MotionImuProfile
  // selects the wide gyro range, so the adapter divides131068 by4 and the
  // actual parser receives32767: the signed sensor rail for exactly one poll.
  const rail = center.slice(0, 3).concat([131068, 0, 0]);
  await step(page, 1, { dt: 25, imu: rail });
  await step(page, 1, { dt: 25 });
  const clipped = await state(page);
  await step(page, 1, { dt: 25 });
  const recovered = await state(page);
  await step(page, 12, { dt: 25 });
  const settled = await state(page);
  await save(page, 'gyro-clip-no-replay', { model, before, clipped, recovered, settled });
  {
    expect(clipped.app.motion.model.sensor_clipped).toBe(true);
    expect(clipped.app.motion.model.tilt_valid).toBe(false);
    expect(clipped.app.motion.model.armed).toBe(false);
    expect(clipped.gauge.status).toBe('CLIP');
    expect(clipped.app.driving.nozzles).toEqual(Array(10).fill(0));
    for (const snapshot of [recovered, settled]) {
      expect(snapshot.app.motion.model.sensor_clipped).toBe(false);
      expect(snapshot.app.motion.model.tilt_valid).toBe(true);
      expect(snapshot.app.motion.model.flick_active).toBe(false);
      expect(snapshot.app.motion.model.flick_pending).toBe(false);
      expect(snapshot.app.motion.model.flick_count).toBe(before.app.motion.model.flick_count);
      expect(snapshot.app.motion.model.angle_x).toBeCloseTo(before.app.motion.model.angle_x, 4);
      expect(snapshot.app.motion.model.angle_y).toBeCloseTo(before.app.motion.model.angle_y, 4);
      expect(snapshot.app.driving.nozzles).toEqual(Array(10).fill(0));
    }
  }
});
