import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-flick-completion');
const degrees = 180 / Math.PI;
const unit = v => v.map(x => x / Math.hypot(...v));
const neutral = unit([-.015381114, -.9128306, -.408049]);
const center = neutral.map(x => Math.round(x * 16384)).concat([0, 0, 0]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) => badge.step(frames, options),
  { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const state = page => inspect(page, "{'badge':badge_snapshot(),'active':trick.active,'phase':trick.phase,'committed':trick.committed,'rotation':trick.rotation,'message':trick.message,'up':sim.basis()[1][1],'y':sim.y,'omega':list(sim.omega),'q':list(sim.q),'time':sim.time,'rows':len(_gesture_rows),'starts':list(_gesture_starts),'completions':list(_gesture_completions)}");
const directions = [
  ['right', 'roll', 1, 'kickflip'], ['left', 'roll', -1, 'kickflip'],
  ['back', 'pitch', 1, 'backflip'], ['forward', 'pitch', -1, 'frontflip'],
];

function imuAt(model, axis, angle, rate = 0) {
  const n = model.neutral, b = model[axis === 'roll' ? 'basis_x' : 'basis_y'];
  const a = angle / degrees;
  const gravity = n.map((v, i) => v * Math.cos(a) + b[i] * Math.sin(a));
  const tangent = n.map((v, i) => -v * Math.sin(a) + b[i] * Math.cos(a));
  return gravity.map(v => Math.round(v * 16384)).concat(cross(tangent, gravity).map(v => Math.round(v * rate / .00875)));
}

async function calibrate(page) {
  await exec(page, 'motion_input.rotated = True');
  await action(page, 'center');
  await step(page, 46);
  const model = await inspect(page, 'motion_input.model');
  expect(model.calibrated).toBe(true);
  expect(model.mode).toBe('both');
  return model;
}

async function trace(page, height = null) {
  await exec(page, `
from stunt_feedback import _body_rotation as _gesture_delta
from physics import q_inverse_rotate as _gesture_rates
${height === null ? '' : `sim.x, sim.y, sim.z = 0., ${height}., 0.\nsim.vx, sim.vy, sim.vz = 0., 0., 0.\nsim.q = (1.,0.,0.,0.)\nsim.omega = [0.,0.,0.]`}
_gesture_rows = []
_gesture_starts = []
_gesture_completions = []
_gesture_previous = sim.q
_gesture_previous_active = ''
_gesture_phase = 'quiet'
_gesture_original_observer = observe_physics
def _observe_gesture(dt, simulation, values):
    global _gesture_previous, _gesture_previous_active
    _gesture_original_observer(dt, simulation, values)
    delta = _gesture_delta(_gesture_previous, simulation.q)
    _gesture_previous = simulation.q
    if trick.active and not _gesture_previous_active:
        _gesture_starts.append((simulation.time, trick.active, trick.committed))
    if _gesture_previous_active and not trick.active:
        _gesture_completions.append({'time':simulation.time,'height':simulation.y,
            'up':simulation.basis()[1][1],'omega':list(simulation.omega),
            'body_rates':list(_gesture_rates(simulation.q, simulation.omega)),
            'message':trick.message,'rotation':trick.rotation})
    _gesture_previous_active = trick.active
    _gesture_rows.append((_gesture_phase, simulation.time, dt, list(delta),
        list(_gesture_rates(simulation.q, simulation.omega)), list(simulation.nozzle_forces),
        simulation.y, simulation.basis()[1][1], trick.active, trick.phase,
        trick.rotation, simulation.contact_count, simulation.chassis_contacts,
        list(simulation.q), list(values[4])))
observe_physics = _observe_gesture
`);
  await step(page, 20);
}

async function flick(page, model, axis, sign, options = {}) {
  await exec(page, "_gesture_phase = 'flick'");
  const samples = [];
  let angle = 0;
  const rates = (axis === 'roll' ? [20,40,80,150,250,450,0] : [20,50,90,150,240,320,0]).map(v => v * sign);
  for (const rate of rates) {
    angle += rate * .025;
    await step(page, 1, { imu: imuAt(model, axis, angle, rate), ...options });
    samples.push(await state(page));
  }
  // Resolve the vertical intent window without an artificial opposite gyro.
  await step(page, 8, { imu: imuAt(model, axis, angle), ...options });
  samples.push(await state(page));
  return { angle, samples };
}

async function returnToCenter(page, model, axis, angle, options = {}) {
  await exec(page, "_gesture_phase = 'return-center'");
  const sign = Math.sign(angle), rate = -sign * 40;
  for (let current = angle; Math.abs(current) > 1; current += rate * .025) {
    await step(page, 1, { imu: imuAt(model, axis, current, rate), ...options });
  }
  await step(page, 8, options);
}

const metrics = (rows, axis, sign) => {
  const index = axis === 'roll' ? 1 : 0, bodySign = axis === 'roll' ? sign : -sign;
  return {
    signedDegrees: rows.reduce((sum, row) => sum + row[3][index] * degrees * bodySign, 0),
    maxTickDegrees: Math.max(...rows.map(row => Math.hypot(...row[3]) * degrees)),
    peakForce: Math.max(...rows.flatMap(row => row[5])),
    heightRange: [Math.min(...rows.map(row => row[6])), Math.max(...rows.map(row => row[6]))],
    contacts: rows.filter(row => row[11] || row[12]).length,
  };
};

async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const prefix = '/system/apps/roadster_sandbox/';
  const sourceHashes = {};
  for (const file of manifest.files.filter(file => file.path.startsWith(prefix))) {
    const name = file.path.slice(prefix.length);
    const hash = createHash('sha256').update(await readFile(resolve('../apps/roadster_sandbox', name))).digest('hex');
    expect(hash, `staged ${name}`).toBe(file.sha256);
    sourceHashes[name] = hash;
  }
  await writeFile(`${output}/flick-${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM app, sensor filter, fixed-step forces and native pico3d rendering. Inputs/IMU intervals are synthetic; face-facing grip sets rotated=True because preview orientation is unavailable. High-altitude fixtures isolate committed rotation and are explicitly distinct from natural gameplay jumps. Does not establish RP2350 performance or physical hand feel.',
    sourceHashes, ...data, final: await state(page),
  }, null, 2) + '\n');
  await writeFile(`${output}/flick-${name}.png`, Buffer.from((await page.evaluate(() => badge.snapshot())).split(',')[1], 'base64'));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 2);
});

for (const [direction, axis, sign, name] of directions) {
  test(`ordinary airborne ${direction} movement keeps manual tilt without committing a flip`, async ({ page }) => {
    const model = await calibrate(page);
    await trace(page, 100);
    const before = await state(page);
    const samples = [];
    let angle = 0;
    // These wrist rates crossed the former gesture thresholds. The current release
    // thresholds keep them as continuous tilt, including a held endpoint.
    const rates = (axis === 'roll' ? [20,40,80,120,140,120,80,40,0] :
      [20,40,80,120,150,170,120,60,0]).map(value => value * sign);
    await exec(page, "_gesture_phase = 'ordinary-tilt'");
    for (const rate of rates) {
      angle += rate * .025;
      await step(page, 1, { imu: imuAt(model, axis, angle, rate) });
      samples.push(await state(page));
    }
    await step(page, 16, { imu: imuAt(model, axis, angle) });
    const held = await state(page), rows = await inspect(page, '_gesture_rows');
    samples.push(held);
    for (const sample of samples) {
      expect(sample.active).toBe('');
      expect(sample.committed).toBe(false);
      expect(sample.badge.motion.model.flick_count).toBe(before.badge.motion.model.flick_count);
      expect(sample.badge.motion.model.flick_pending).toBe(false);
      expect(sample.badge.motion.model.tilt_valid).toBe(true);
    }
    expect(held.starts).toHaveLength(0);
    expect(held.badge.motion_allowed).toBe(true);
    expect(held.badge.motion.model[axis === 'roll' ? 'tilt_x' : 'tilt_y'] * sign).toBeGreaterThan(.1);
    const result = metrics(rows, axis, sign);
    expect(result.peakForce).toBeGreaterThan(100);
    expect(result.signedDegrees).toBeGreaterThan(1);
    await save(page, `${direction}-ordinary-tilt`, { fixture: { height: 100 }, before, rates, angle, samples, held, result, rows });
  });

  test(`one airborne ${direction} flick completes one ${name} after returning to neutral`, async ({ page }) => {
    test.setTimeout(120000);
    const model = await calibrate(page);
    await trace(page, 100);
    const before = await state(page);
    const gesture = await flick(page, model, axis, sign);
    const accepted = await state(page);
    expect(accepted.active).toBe(name);
    expect(accepted.committed).toBe(true);
    expect(accepted.badge.committed_flip).toBe(true);
    expect(await inspect(page, 'hud_state["tilt_gauge"]["status"]')).toBe('FLIP');
    expect(accepted.badge.touch_buttons).toEqual([]);
    expect(accepted.badge.clicky_buttons).toEqual([]);
    expect(accepted.badge.motion.model.flick_count).toBe(before.badge.motion.model.flick_count + 1);
    await returnToCenter(page, model, axis, gesture.angle);
    const centered = await state(page);
    expect(centered.active).toBe(name);
    expect(centered.committed).toBe(true);
    await save(page, `${direction}-committed`, { fixture: { height: 100 }, before, gesture, accepted, centered });
    await exec(page, "_gesture_phase = 'centered-completion'");
    // Observe the completed airborne maneuver, then a short settled interval.
    // A fixed five-second wait also includes the unrelated 100-metre drop and
    // its hard landing, whose rebound can undo an already completed rotation.
    let completionWaitFrames = 0;
    while (completionWaitFrames < 200) {
      await step(page, 8);
      completionWaitFrames += 8;
      if (!(await state(page)).active) break;
    }
    await step(page, 8);
    const completed = await state(page), rows = await inspect(page, '_gesture_rows');
    const result = metrics(rows, axis, sign);
    await save(page, `${direction}-completed`, { fixture: { height: 100, completionWaitFrames, settlingFrames: 8 }, before, gesture, accepted, centered, completed, result, rows });
    expect(result.contacts, 'The elevated rotation fixture must finish before landing').toBe(0);
    expect(completed.active).toBe('');
    expect(completed.starts).toHaveLength(1);
    expect(completed.starts[0][1]).toBe(name);
    expect(completed.starts[0][2]).toBe(true);
    expect(result.signedDegrees).toBeGreaterThan(350);
    expect(result.signedDegrees).toBeLessThan(390);
    expect(result.maxTickDegrees).toBeLessThan(12);
    expect(result.peakForce).toBeGreaterThan(1000);
    expect(completed.up).toBeGreaterThan(.98);
    expect(completed.completions).toHaveLength(1);
    const finish = completed.completions[0];
    expect(finish.message).toContain('complete');
    expect(finish.up).toBeGreaterThan(.99);
    // Controller release and the following settled interval are both airborne.
    expect(Math.hypot(finish.body_rates[0], finish.body_rates[2])).toBeLessThan(.2);
    expect(metrics(rows.filter(row => row[1] <= finish.time), axis, sign).signedDegrees).toBeGreaterThan(350);
    expect(completed.badge.motion.model.flick_count).toBe(before.badge.motion.model.flick_count + 1);
  });
}

test('held lift and continuing tilt cannot restart or cancel a committed flick', async ({ page }) => {
  test.setTimeout(120000);
  const model = await calibrate(page);
  await trace(page, 100);
  const gesture = await flick(page, model, 'roll', 1, { pad: ['SELECT'] });
  expect((await state(page)).committed).toBe(true);
  await step(page, 24, { pad: ['SELECT'], imu: imuAt(model, 'roll', gesture.angle) });
  const held = await state(page);
  expect(held.active).toBe('kickflip');
  expect(held.committed).toBe(true);
  expect(held.starts).toHaveLength(1);
  await returnToCenter(page, model, 'roll', gesture.angle, { pad: ['SELECT'] });
  await step(page, 160);
  const done = await state(page), rows = await inspect(page, '_gesture_rows');
  expect(done.active).toBe('');
  expect(done.starts).toHaveLength(1);
  expect(metrics(rows, 'roll', 1).signedDegrees).toBeGreaterThan(350);
  await save(page, 'held-lift-one-commit', { gesture, held, done, rows });
});

for (const cancellation of ['menu', 'reset', 'manual', 'raw', 'center', 'scenes', 'help']) {
  test(`${cancellation} cancels a committed flick without replay on release`, async ({ page }) => {
    const model = await calibrate(page);
    await trace(page, 100);
    const gesture = await flick(page, model, 'roll', 1);
    const accepted = await state(page);
    expect(accepted.committed).toBe(true);
    if (cancellation === 'menu') await step(page, 2, { pad: ['MENU'] });
    else if (cancellation === 'manual') await step(page, 2, { buttons: ['LEFT'] });
    else if (cancellation === 'raw') await step(page, 2, { appControls: { nozzles: [0,0,0,0,0,0,0,0,1,0] } });
    else await action(page, cancellation);
    await step(page, 2);
    const cancelled = await state(page);
    expect(cancelled.active).toBe('');
    expect(cancelled.committed).toBe(false);
    if (cancellation === 'menu') await step(page, 2, { pad: ['MENU'] });
    else if (cancellation === 'scenes') await step(page, 2, { pad: ['MENU'] });
    else if (cancellation === 'help') await action(page, 'help', false);
    await step(page, 60);
    const released = await state(page);
    expect(released.active).toBe('');
    expect(released.starts).toHaveLength(1);
    expect(released.badge.motion.model.flick_count).toBe(accepted.badge.motion.model.flick_count);
    await save(page, `cancel-${cancellation}`, { gesture, accepted, cancelled, released });
  });
}

for (const [direction, axis, sign, name] of directions) {
  test(`natural lift, released pad and ${direction} flick complete a ${name} before landing`, async ({ page }) => {
  test.setTimeout(120000);
  const model = await calibrate(page);
  await trace(page);
  await step(page, 80, { pad: ['SELECT'] });
  const launched = await state(page);
  expect(launched.y).toBeGreaterThan(6);
  await step(page, 4);
  const gesture = await flick(page, model, axis, sign);
  const accepted = await state(page);
  expect(accepted.active).toBe(name);
  await returnToCenter(page, model, axis, gesture.angle);
  await step(page, 200);
  const completed = await state(page), rows = await inspect(page, '_gesture_rows');
  const result = metrics(rows, axis, sign);
  expect(completed.active).toBe('');
  expect(completed.starts).toHaveLength(1);
  expect(result.signedDegrees).toBeGreaterThan(350);
  expect(result.signedDegrees).toBeLessThan(390);
  expect(completed.up).toBeGreaterThan(.97);
  expect(completed.message).toContain('complete');
  expect(rows.some(row => row[8] === name && row[10] > 5.5)).toBe(true);
  await save(page, `natural-jump-${direction}`, { fixture: 'No pose edits: ordinary inner-pad lift, release, raw-IMU flick and neutral return.', launched, gesture, accepted, completed, result, rows });
  });
}


test('render stalls discard sensor pulses but preserve an already committed flip', async ({ page }) => {
  test.setTimeout(120000);
  const model = await calibrate(page);
  await trace(page, 100);
  const gesture = await flick(page, model, 'roll', -1);
  const accepted = await state(page);
  expect(accepted.committed).toBe(true);
  await exec(page, "_gesture_phase = 'slow-centered-completion'");
  await step(page, 40, { dt: 500 });
  const completed = await state(page), rows = await inspect(page, '_gesture_rows');
  const result = metrics(rows, 'roll', -1);
  expect(completed.active).toBe('');
  expect(completed.starts).toHaveLength(1);
  expect(result.signedDegrees).toBeGreaterThan(350);
  expect(result.signedDegrees).toBeLessThan(390);
  expect(completed.badge.motion.model.flick_count).toBe(accepted.badge.motion.model.flick_count);
  expect(completed.badge.motion.model.flick_active).toBe(false);
  expect(completed.up).toBeGreaterThan(.98);
  expect(completed.time - accepted.time).toBeLessThan(5);
  await save(page, 'slow-committed-completion', { gesture, accepted, completed, result, rows, injectedPollMilliseconds: 500 });
});
