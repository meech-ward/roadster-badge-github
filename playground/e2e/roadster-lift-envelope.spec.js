import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-lift-envelope');
const neutral = [0, 0, 16384, 0, 0, 0];
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
async function step(page, frames, options = {}) {
  // Every interpreter request has a five-second deadline. Keep long real
  // trajectories split into short chunks instead of increasing that deadline.
  while (frames > 0) {
    const count = Math.min(frames, 60);
    await page.evaluate(({ count, options }) => badge.step(count, options), {
      count, options: { dt: 25, buttons: [], pad: [], imu: neutral, appControls: {}, ...options },
    });
    frames -= count;
  }
}
const state = page => inspect(page, `{'height':sim.y-GROUND_Y,'vy':sim.vy,
    'contacts':sim.contact_count,'airborne':sim.airborne,'up':sim.basis()[1][1],
    'forces':list(sim.nozzle_forces),'fan':physical_fan,'fan_force':sim.fan_force,
    'air_fraction':sim.air_fraction,'time':sim.time,'app':badge_snapshot()}`);
async function calibrate(page) {
  await action(page, 'center');
  await step(page, 48);
  const snapshot = await state(page);
  expect(snapshot.app.motion.phase).toBe('ready');
  expect(snapshot.app.motion.model.calibrated).toBe(true);
  return snapshot.app.motion.model;
}
function imuAt(model, axis, angle) {
  const n = model.neutral, b = model[axis === 'roll' ? 'basis_x' : 'basis_y'];
  const radians = angle * Math.PI / 180;
  return n.map((value, i) => Math.round((value * Math.cos(radians) + b[i] * Math.sin(radians)) * 16384)).concat([0, 0, 0]);
}
async function trace(page) {
  await exec(page, `
from stunt_feedback import _body_rotation as _lift_delta
_lift_rows = []
_lift_phase = 'idle'
_lift_previous = sim.q
_lift_observer = observe_physics
def _observe_lift(dt, simulation, values):
    global _lift_previous
    _lift_observer(dt, simulation, values)
    delta = _lift_delta(_lift_previous, simulation.q)
    _lift_previous = simulation.q
    _lift_rows.append([_lift_phase, simulation.time, simulation.y-GROUND_Y,
        simulation.vy, list(simulation.nozzle_forces), simulation.contact_count,
        list(delta), simulation.basis()[1][1]])
observe_physics = _observe_lift
`);
}
async function phase(page, label) {
  await exec(page, `_lift_phase = ${JSON.stringify(label)}`);
}
async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const prefix = '/system/apps/roadster_sandbox/';
  const sourceHashes = {};
  for (const row of manifest.files.filter(row => row.path.startsWith(prefix))) {
    const name = row.path.slice(prefix.length);
    const current = createHash('sha256').update(await readFile(resolve('../apps/roadster_sandbox', name))).digest('hex');
    expect(current, `Staged ${name} must match the canonical app source`).toBe(row.sha256);
    sourceHashes[name] = current;
  }
  expect(Object.keys(sourceHashes).length).toBeGreaterThan(15);
  const testSha256 = createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex');
  await writeFile(`${output}/lift-${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM app, logical input lifecycle, fixed-step rigid-body physics and native pico3d pixels. Synthetic IMU and elapsed intervals do not establish physical badge frame rate, physical touch timing or human feel. Any elevated or empty-air test fixture is identified separately.',
    sourceHashes, testSha256, ...data, final: await state(page),
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/lift-${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 2);
});

test('held lift slows smoothly into a bounded hover and releases, lands and relaunches naturally', async ({ page }) => {
  test.setTimeout(120000);
  await calibrate(page);
  await trace(page);
  await phase(page, 'held');
  await step(page, 600, { pad: ['SELECT'] });
  const held = await state(page), heldRows = await inspect(page, '_lift_rows');
  const [ceiling, speedLimit] = await inspect(page, '(LIFT_CEILING, __import__("badge_controls").LIFT_MAX_SPEED)');
  const rising = heldRows.filter(row => row[3] > .1);
  const peakSpeed = Math.max(...heldRows.map(row => row[3]));
  // These are deliberately broad physical acceptance bounds, not the user's
  // illustrative six/twelve/twenty-foot knots or a prescribed trajectory.
  expect(held.height).toBeGreaterThan(ceiling - .3);
  expect(held.height).toBeLessThan(ceiling + .2);
  expect(Math.abs(held.vy)).toBeLessThan(.1);
  expect(peakSpeed).toBeGreaterThan(speedLimit * .6);
  expect(peakSpeed).toBeLessThan(speedLimit * 1.02);
  expect(Math.max(...heldRows.map(row => row[2]))).toBeLessThan(ceiling + .2);
  const low = rising.find(row => row[2] >= ceiling * .25), high = rising.find(row => row[2] >= ceiling * .75);
  expect(low[3]).toBeGreaterThan(high[3] * 1.5);
  expect(held.forces.slice(4, 8).every(force => force > 3000 && force < 10000)).toBe(true);
  expect(held.air_fraction).toBe(1);
  await save(page, 'hover', { held, peakSpeed, low, high, rows: heldRows });

  await phase(page, 'release');
  await step(page, 180);
  const landed = await state(page);
  expect(landed.contacts).toBe(4);
  expect(landed.airborne).toBe(false);
  expect(landed.forces.every(force => force === 0)).toBe(true);
  expect(Math.abs(landed.height)).toBeLessThan(.1);
  await phase(page, 'relaunch');
  await step(page, 40, { pad: ['SELECT'] });
  const relaunched = await state(page);
  expect(relaunched.airborne).toBe(true);
  expect(relaunched.height).toBeGreaterThan(2);
  expect(relaunched.vy).toBeGreaterThan(2);
  expect(relaunched.air_fraction).toBe(1);
  await save(page, 'release-relaunch', { held, landed, relaunched, rows: await inspect(page, '_lift_rows') });
});

test('tilt still commands roll and pitch while held lift is already at its soft hover', async ({ page }) => {
  const model = await calibrate(page), trials = [];
  const ceiling = await inspect(page, 'LIFT_CEILING');
  await trace(page);
  for (const axis of ['roll', 'pitch']) {
    // This elevated test fixture isolates rotation at the governor's hover.
    // The preceding test separately reaches that height with ordinary input.
    await exec(page, `
from badge_controls import LIFT_CEILING
sim.x, sim.y, sim.z = 0., GROUND_Y+LIFT_CEILING, 0.
sim.vx = sim.vy = sim.vz = 0.
sim.q = (1.,0.,0.,0.)
sim.omega = [0.,0.,0.]
rotation_control.reset()
_lift_previous = sim.q
_lift_rows = []
`);
    await phase(page, 'settle');
    await step(page, 16, { pad: ['SELECT'] });
    const before = await state(page);
    await phase(page, axis);
    await step(page, 48, { pad: ['SELECT'], imu: imuAt(model, axis, 24) });
    const after = await state(page), rows = (await inspect(page, '_lift_rows')).filter(row => row[0] === axis);
    const angleIndex = axis === 'roll' ? 1 : 0;
    const angle = rows.reduce((sum, row) => sum + row[6][angleIndex], 0) * 180 / Math.PI;
    const torque = row => axis === 'roll' ? row[4][4]+row[4][6]-row[4][5]-row[4][7] : row[4][4]+row[4][5]-row[4][6]-row[4][7];
    expect(before.height).toBeGreaterThan(ceiling - .3);
    expect(after.app.motion.model.armed).toBe(true);
    expect(after.app.rotation.motion_active[axis === 'roll' ? 0 : 1]).toBe(true);
    expect(rows.some(row => Math.abs(torque(row)) > 500)).toBe(true);
    expect(Math.abs(angle)).toBeGreaterThan(10);
    expect(after.air_fraction).toBe(1);
    trials.push({ axis, before, after, angle, rows });
    await step(page, 20);
  }
  await save(page, 'tilt-authority', { fixture: 'Upright pose at soft hover, full air; no pose changes during the measured lean.', trials });
});

test('neutral uncalibrated held lift recomputes jet power on every fixed tick during a slow poll', async ({ page }) => {
  expect((await state(page)).app.motion.model.calibrated).toBe(false);
  await step(page, 1, { dt: 200 });
  await exec(page, `
# Sample the smooth taper, where stronger lift is below jet saturation.
# Initial launch deliberately requests full power for several fixed ticks.
sim.y = GROUND_Y+LIFT_CEILING*.75
sim.vy = 0.
_lift_calls = []
_lift_physical_driving = physical_driving
def _trace_lift_driving(dt, simulation):
    result = _lift_physical_driving(dt, simulation)
    _lift_calls.append([frame_count, dt, simulation.time, simulation.y-GROUND_Y,
        simulation.vy, list(result['nozzles'])])
    return result
physical_driving = _trace_lift_driving
`);
  await step(page, 3, { dt: 200, pad: ['SELECT'] });
  const calls = await inspect(page, '_lift_calls');
  const feedback = calls.filter(row => row[1] > 0);
  expect(feedback).toHaveLength(8);
  const frameIds = [...new Set(feedback.map(row => row[0]))];
  expect(frameIds).toHaveLength(2);
  for (const frame of frameIds) {
    const ticks = feedback.filter(row => row[0] === frame);
    expect(ticks).toHaveLength(4);
    expect(ticks.every(row => Math.abs(row[1] - 1/40) < 1e-12)).toBe(true);
    expect(new Set(ticks.map(row => row[5][4].toFixed(8))).size).toBe(4);
    expect(ticks.at(-1)[5][4]).toBeLessThan(ticks[0][5][4]);
  }
  await step(page, 2, { dt: 200 });
  const released = await state(page);
  expect(released.forces.every(force => force === 0)).toBe(true);
  expect(released.app.motion.model.calibrated).toBe(false);
  await save(page, 'fixed-tick-feedback', { calls, feedback, released });
});

test('raw development jets retain full authority above hover and fan commands remain independent', async ({ page }) => {
  await exec(page, `
sim.x, sim.y, sim.z = 0., GROUND_Y+LIFT_CEILING+4., 0.
sim.vx = sim.vy = sim.vz = 0.
sim.q = (1.,0.,0.,0.)
sim.omega = [0.,0.,0.]
`);
  await step(page, 2, { pad: ['SELECT'] });
  const governed = await state(page);
  expect(governed.forces.every(force => force === 0)).toBe(true);
  const raw = [0,0,0,0,1,1,1,1,0,0];
  await step(page, 6, { pad: ['SELECT'], appControls: { nozzles: raw } });
  const independent = await state(page);
  const ratedLift = await inspect(page, "[n[3] for n in __import__('physics').NOZZLES[4:8]]");
  expect(independent.forces.slice(4, 8)).toEqual(ratedLift);
  expect(independent.vy).toBeGreaterThan(governed.vy + 1);
  await action(page, 'reset');
  await step(page, 2);
  await step(page, 2, { buttons: ['SELECT'] });
  await step(page, 50);
  const fan = await state(page);
  expect(fan.fan).toBe(true);
  const weight = await inspect(page, "sim.mass*__import__('physics').GRAVITY");
  expect(fan.fan_force).toBeGreaterThan(weight * 2);
  await step(page, 12, { pad: ['SELECT'] });
  const fanLift = await state(page);
  expect(fanLift.fan).toBe(true);
  expect(fanLift.app.driving.fan).toBe(1);
  expect(fanLift.forces.slice(4, 8).every(force => force > 0)).toBe(true);
  await save(page, 'raw-jets-fan', { fixture: 'Upright pose 4 m above soft hover isolates raw-jet authority; fan sequence uses ordinary reset onto flat ground.', governed, independent, fan, fanLift });
});
