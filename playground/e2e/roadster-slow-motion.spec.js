import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-slow-motion');
const degrees = 180 / Math.PI;
const unit = v => { const length = Math.hypot(...v); return v.map(value => value / length); };
// Recorded face-facing gravity. rotated=True is a test-only orientation
// fixture because the native preview explicitly cannot rotate like hardware.
const neutral = unit([-.015381114, -.9128306, -.408049]);
const center = neutral.map(value => Math.round(value * 16384)).concat([0, 0, 0]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) =>
  badge.step(frames, options), { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const state = page => inspect(page, 'badge_snapshot()');

function imuAt(model, axis, angle, rate = 0) {
  const n = model.neutral, b = model[axis === 'roll' ? 'basis_x' : 'basis_y'];
  const radians = angle / degrees;
  const gravity = n.map((value, i) => value * Math.cos(radians) + b[i] * Math.sin(radians));
  const tangent = n.map((value, i) => -value * Math.sin(radians) + b[i] * Math.cos(radians));
  const gyro = cross(tangent, gravity).map(value => Math.round(value * rate / .00875));
  return gravity.map(value => Math.round(value * 16384)).concat(gyro);
}

async function calibrate(page) {
  await exec(page, 'motion_input.rotated = True');
  await action(page, 'center');
  await step(page, 46);
  const model = (await state(page)).motion.model;
  expect(model.calibrated).toBe(true);
  expect(model.tilt_valid).toBe(true);
  expect(model.mode).toBe('both');
  return model;
}

async function fly(page, dt = 25) {
  await exec(page, `
from physics import q_inverse_rotate as _slow_body_rates
sim.x, sim.y, sim.z = 0., 1000., 0.
sim.vx, sim.vy, sim.vz = 0., 0., 0.
sim.q = (1., 0., 0., 0.)
sim.omega = [0., 0., 0.]
stunt.reset(sim)
_slow_rows = []
`);
  await step(page, 4, { dt });
  expect((await state(page)).motion.model.armed).toBe(true);
}

async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const sourceHashes = Object.fromEntries(manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')).map(row => [row.path, row.sha256]));
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Real MicroPython/WASM app, IMU parser, source/input lifecycle, fixed-step physics and native pico3d frames. IMU and button signals are synthetic. Recorded face-facing gravity and test-only rotated=True do not validate physical orientation or hand feel. An airborne pose fixture isolates controls; it is not a gameplay jump. dt is injected framework timing, not measured hardware rendering performance.',
    sourceHashes, ...data, final: await state(page),
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
  await step(page, 2);
});

for (const scene of ['flat', 'ramp', 'loop', 'collection']) {
  test(`${scene} keeps fresh airborne tilt and bounded jet control at 167/250/500 ms`, async ({ page }) => {
    test.setTimeout(120000);
    const model = await calibrate(page), results = [];
    // Keep the historical loop regression without exposing it through the scene UI.
    if (scene === 'loop') await exec(page, "reset_sim('loop')");
    else await action(page, 'scene', scene);
    await exec(page, `
_slow_observe = observe_physics
def _observe_slow_tick(dt, simulation, values):
    _slow_observe(dt, simulation, values)
    _slow_rows.append([simulation.time, dt,
        list(_slow_body_rates(simulation.q, simulation.omega)),
        list(simulation.nozzle_forces), list(values[4]),
        simulation.contact_count, simulation.chassis_contacts])
observe_physics = _observe_slow_tick
`);
    for (const dt of [167, 250, 500]) {
      await fly(page, dt);
      await step(page, 20, { dt, imu: imuAt(model, 'roll', 24) });
      const snapshot = await state(page), rows = await inspect(page, '_slow_rows');
      const rates = rows.map(row => row[2][2] * degrees);
      expect(snapshot.motion.model.armed).toBe(true);
      expect(snapshot.motion.model.tilt_valid).toBe(true);
      expect(snapshot.motion.model.flick_active).toBe(false);
      expect(snapshot.motion.model.flick_pending).toBe(false);
      expect(snapshot.touch_buttons).toEqual([]);
      expect(snapshot.clicky_buttons).toEqual([]);
      expect(snapshot.rotation.motion_targets[0] * degrees).toBeCloseTo(125, 0);
      expect(rates.at(-1)).toBeGreaterThan(60);
      expect(Math.max(...rates)).toBeLessThan(155);
      expect(rows.some(row => row[3][4]+row[3][6]-row[3][5]-row[3][7] > 100)).toBe(true);
      expect(rows.every(row => row[5] === 0 && row[6] === 0)).toBe(true);
      expect(rows.every(row => row[1] === 1 / 40)).toBe(true);
      expect(await inspect(page, 'trick.active')).toBe('');
      // A slow-frame fallback still feeds the bounded force mixer. It must
      // accelerate through the intervening fixed ticks rather than set a rate.
      const acceleration = rows.slice(1).map((row, i) =>
        (row[2][2]-rows[i][2][2]) / row[1]);
      expect(Math.max(...acceleration)).toBeGreaterThan(1);
      expect(Math.max(...acceleration)).toBeLessThan(5);
      results.push({ dt, snapshot, peakDps: Math.max(...rates), finalDps: rates.at(-1),
        peakAcceleration: Math.max(...acceleration), rows });
      await step(page, 4, { dt });
      const centered = await state(page);
      expect(centered.motion.model.armed).toBe(true);
      expect(centered.rotation.level_state).toBe('level');
      expect(centered.driving.nozzles.some(value => value > 0)).toBe(true);
    }
    await save(page, `slow-tilt-${scene}`, { scene, model, results });
  });
}

test('all four held directions survive 500 ms polling and fresh opposite tilt brakes through jets', async ({ page }) => {
  const model = await calibrate(page), results = [];
  for (const [axis, sign] of [['roll', 1], ['roll', -1], ['pitch', 1], ['pitch', -1]]) {
    await fly(page, 500);
    await step(page, 14, { dt: 500, imu: imuAt(model, axis, 18 * sign) });
    const held = await state(page), before = await inspect(page, 'list(_slow_body_rates(sim.q, sim.omega))');
    const component = axis === 'roll' ? 2 : 0, bodySign = axis === 'roll' ? 1 : -1;
    expect(held.motion.model.armed).toBe(true);
    expect(before[component] * bodySign * sign * degrees).toBeGreaterThan(30);
    await step(page, 4, { dt: 500, imu: imuAt(model, axis, -18 * sign) });
    const after = await inspect(page, 'list(_slow_body_rates(sim.q, sim.omega))');
    const reversed = await state(page);
    expect(after[component] * bodySign * sign).toBeLessThan(before[component] * bodySign * sign);
    expect(reversed.rotation.motion_targets[axis === 'roll' ? 0 : 1] * sign).toBeLessThan(0);
    expect(reversed.driving.nozzles.some(value => value > 0)).toBe(true);
    results.push({ axis, sign, held, before, after, reversed });
  }
  await save(page, 'slow-tilt-four-directions', { model, results });
});

test('slow polls discard sensor pulses and unresolved intent while committed flips finish once', async ({ page }) => {
  const model = await calibrate(page), trials = [];
  await fly(page);
  for (const axis of ['roll', 'pitch']) {
    await action(page, 'motion', 'flick');
    await step(page, 28);
    const rate = axis === 'roll' ? 350 : 240;
    await step(page, 2, { imu: imuAt(model, axis, rate * .025, rate) });
    const started = await state(page);
    expect(axis === 'roll' ? started.motion.model.flick_active : started.motion.model.flick_pending).toBe(true);
    const count = started.motion.model.flick_count;
    expect(started.committed_flip).toBe(axis === 'roll');
    await step(page, 3, { dt: 500 });
    const stalled = await state(page);
    expect(stalled.motion.model.armed).toBe(axis !== 'roll');
    expect(stalled.committed_flip).toBe(axis === 'roll');
    expect(stalled.motion.model.tilt_valid).toBe(true);
    expect(stalled.motion.model.flick_active).toBe(false);
    expect(stalled.motion.model.flick_pending).toBe(false);
    expect(stalled.motion.model.flick_ready).toBe(false);
    expect(stalled.motion.model.flick_count).toBe(count);
    // An accepted flip retains control; unresolved vertical intent is
    // discarded and may resume the independent centered-grip assist.
    expect(axis === 'roll' ? ['off'] : ['level', 'steady']).toContain(stalled.rotation.level_state);
    // One new stroke followed by its held orientation is physically
    // consistent; a repeated gyro with unchanged gravity would invent drift.
    await step(page, 1, { imu: imuAt(model, axis, -rate * .025, -rate) });
    await step(page, 3, { imu: imuAt(model, axis, -rate * .025) });
    const early = await state(page);
    expect(early.motion.model.flick_count).toBe(count);
    expect(early.motion.model.flick_active).toBe(false);
    expect(early.motion.model.flick_pending).toBe(false);
    await step(page, axis === 'roll' ? 140 : 40);
    expect(await inspect(page, 'trick.active')).toBe('');
    expect((await state(page)).motion.model.flick_ready).toBe(true);
    await step(page, 2, { imu: imuAt(model, 'roll', 0, 350) });
    const fresh = await state(page);
    expect(fresh.motion.model.flick_count).toBe(count + 1);
    expect(fresh.motion.model.flick_active).toBe(true);
    expect(fresh.committed_flip).toBe(true);
    trials.push({ axis, started, stalled, early, fresh });
  }
  await save(page, 'slow-flick-discard-recover', { model, trials });
});

test('rearming accepts a fresh valid lean; slow freefall cannot unlock motion', async ({ page }) => {
  const model = await calibrate(page);
  await fly(page, 500);
  // Input is read from the previous framework poll. Latch invalid gravity
  // before requesting rearm so a legitimate queued neutral cannot satisfy it.
  await step(page, 3, { dt: 500, imu: [0, 0, 0, 0, 0, 0] });
  await action(page, 'motion', 'tilt');
  await step(page, 3, { dt: 500, imu: [0, 0, 0, 0, 0, 0] });
  const invalid = await state(page);
  expect(invalid.motion.model.tilt_valid).toBe(false);
  expect(invalid.motion.model.armed).toBe(false);
  expect(invalid.motion_neutral_required).toBe(true);
  expect(invalid.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 3, { dt: 500, imu: imuAt(model, 'roll', 24) });
  const leaning = await state(page);
  expect(leaning.motion.model.tilt_valid).toBe(true);
  expect(leaning.motion.model.armed).toBe(true);
  expect(leaning.motion_neutral_required).toBe(false);
  expect(leaning.driving.nozzles.some(value => value > 0)).toBe(true);
  expect(leaning.motion.model.flick_active).toBe(false);
  await step(page, 4, { dt: 500 });
  const neutralState = await state(page);
  expect(neutralState.motion_neutral_required).toBe(false);
  expect(neutralState.motion.model.armed).toBe(true);
  await step(page, 4, { dt: 500, imu: imuAt(model, 'roll', 24) });
  const resumed = await state(page);
  expect(resumed.motion.model.armed).toBe(true);
  expect(resumed.driving.nozzles.some(value => value > 0)).toBe(true);
  await action(page, 'help');
  await step(page, 3, { dt: 500, imu: imuAt(model, 'roll', 24) });
  const blocked = await state(page);
  expect(blocked.motion.model.armed).toBe(false);
  expect(blocked.driving.nozzles).toEqual(Array(10).fill(0));
  await action(page, 'help', false);
  await step(page, 4, { dt: 500, imu: imuAt(model, 'roll', 24) });
  const afterMenu = await state(page);
  expect(afterMenu.motion.model.armed).toBe(true);
  expect(afterMenu.motion_neutral_required).toBe(false);
  expect(afterMenu.driving.nozzles.some(value => value > 0)).toBe(true);
  expect(afterMenu.motion.model.flick_active).toBe(false);
  await save(page, 'slow-tilt-fresh-sample-validity-guard', { model, invalid, leaning, neutralState, resumed, blocked, afterMenu });
});

test('calibration completes with conservative sample credit at 500 ms polling', async ({ page }) => {
  await exec(page, 'motion_input.rotated = True');
  await action(page, 'center');
  await step(page, 6, { dt: 500 });
  const partial = await state(page);
  expect(partial.motion.phase).toBe('calibrating');
  expect(partial.motion.model.calibrated).toBe(false);
  expect(partial.motion.model.calibration_progress).toBeLessThan(.8);
  expect(partial.motion.model.armed).toBe(false);
  await step(page, 8, { dt: 500 });
  const complete = await state(page);
  expect(complete.motion.phase).toBe('ready');
  expect(complete.motion.model.calibrated).toBe(true);
  expect(complete.motion.model.tilt_valid).toBe(true);
  expect(complete.driving.nozzles).toEqual(Array(10).fill(0));
  await save(page, 'slow-calibration', { partial, complete });
});
