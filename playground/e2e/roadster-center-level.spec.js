import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-center-level');
const degrees = 180 / Math.PI;
const grip = [-.015381114, -.9128306, -.408049];
const magnitude = Math.hypot(...grip);
const center = grip.map(value => Math.round(value / magnitude * 16384)).concat([0, 0, 0]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) =>
  badge.step(frames, options), { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const state = page => inspect(page, 'badge_snapshot()');
const pose = page => inspect(page, '[sim.q, sim.omega, sim.y, sim.contact_count, sim.chassis_contacts]');

function imuAt(model, axis, angle, rate = 0) {
  const n = model.neutral, b = model[axis === 'roll' ? 'basis_x' : 'basis_y'];
  const radians = angle / degrees;
  const gravity = n.map((value, i) => value * Math.cos(radians) + b[i] * Math.sin(radians));
  const tangent = n.map((value, i) => -value * Math.sin(radians) + b[i] * Math.cos(radians));
  const gyro = cross(tangent, gravity).map(value => Math.round(value * rate / .00875));
  return gravity.map(value => Math.round(value * 16384)).concat(gyro);
}

async function calibrate(page) {
  // The preview has no display-orientation API. This selects the measured
  // face-facing grip transform, not a claim about physical auto-rotation.
  await exec(page, 'motion_input.rotated = True');
  await action(page, 'center');
  await step(page, 46);
  const model = (await state(page)).motion.model;
  expect(model.calibrated).toBe(true);
  expect(model.tilt_valid).toBe(true);
  return model;
}

async function fixture(page, angle = 0, axis = 'roll') {
  const a = angle / degrees / 2;
  const q = axis === 'roll' ? [Math.cos(a), 0, 0, Math.sin(a)] : [Math.cos(a), Math.sin(a), 0, 0];
  await exec(page, `
from physics import q_inverse_rotate as _level_body_rates, q_rotate as _level_world
from stunt_feedback import _body_rotation as _level_delta
sim.x, sim.y, sim.z = 0., 5000., 0.
sim.vx, sim.vy, sim.vz = 0., 0., 0.
sim.q = ${JSON.stringify(q)}
sim.omega = [0., 0., 0.]
stunt.reset(sim)
_level_rows = []
_level_previous = sim.q
_level_phase = 'fixture'
if '_level_observer' not in globals():
    _level_observer = observe_physics
def _trace_level(dt, simulation, values):
    global _level_previous
    _level_observer(dt, simulation, values)
    delta = _level_delta(_level_previous, simulation.q)
    _level_previous = simulation.q
    _level_rows.append([_level_phase, simulation.time, dt,
        list(_level_body_rates(simulation.q, simulation.omega)), list(delta),
        list(simulation.nozzle_forces), list(simulation.q), list(values[4]),
        simulation.y, simulation.contact_count, simulation.chassis_contacts,
        _level_world(simulation.q, (0., 1., 0.))[1], rotation_control.snapshot()])
observe_physics = _trace_level
`);
  await step(page, 6);
}

async function segment(page, phase, frames, options = {}) {
  await exec(page, `_level_phase = ${JSON.stringify(phase)}`);
  await step(page, frames, options);
}

async function slowReturn(page, model, axis, angle) {
  await exec(page, "_level_phase = 'slow-return'");
  const sign = Math.sign(angle), rate = -sign * 40;
  for (let value = angle; Math.abs(value) > 1; value += rate * .025) {
    await step(page, 1, { imu: imuAt(model, axis, value, rate) });
  }
  await step(page, 2);
}

function metrics(rows) {
  const last = rows.at(-1);
  return {
    samples: rows.length,
    lastTiltDegrees: Math.acos(Math.min(1, Math.max(-1, last[11]))) * degrees,
    lastAngularSpeedDps: Math.hypot(...last[3]) * degrees,
    lastPitchRollSpeedDps: Math.hypot(last[3][0], last[3][2]) * degrees,
    lastYawSpeedDps: last[3][1] * degrees,
    rollTravelDegrees: rows.reduce((sum, row) => sum + row[4][1] * degrees, 0),
    pitchTravelDegrees: rows.reduce((sum, row) => sum + row[4][0] * degrees, 0),
    peakRateDps: Math.max(...rows.map(row => Math.hypot(...row[3]) * degrees)),
    deliveredForceSamples: rows.filter(row => row[5].some(force => force > 1)).length,
    minimumHeight: Math.min(...rows.map(row => row[8])),
  };
}

async function save(page, name, data = {}) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM, native pico3d/PicoVector frames, app input lifecycle, sensor filter and fixed-step physics. Logical inputs and IMU are synthetic. recorded face-facing grip plus test-only rotated=True does not validate physical orientation or hand feel. High-altitude test poses permit correction without collision; they do not guarantee a level gameplay landing or measure badge performance.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    ...data, final: await state(page), pose: await pose(page), hud: await inspect(page, 'hud_state'),
  }, null, 2) + '\n');
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

for (const [name, axis, sign] of [['right', 'roll', 1], ['left', 'roll', -1], ['back', 'pitch', 1], ['forward', 'pitch', -1]]) {
  test(`${name} tilt rotates through jets and centered grip returns upright`, async ({ page }) => {
    test.setTimeout(120000);
    const model = await calibrate(page);
    await fixture(page);
    await segment(page, 'tilt', 72, { imu: imuAt(model, axis, sign * 25) });
    const tilted = await state(page), tiltedPose = await pose(page);
    const tiltedRows = await inspect(page, '_level_rows');
    const body = tiltedRows.at(-1)[3], component = axis === 'roll' ? 2 : 0;
    expect(body[component] * sign * (axis === 'roll' ? 1 : -1) * degrees).toBeGreaterThan(65);
    const gauge = await inspect(page, 'hud_state["tilt_gauge"]');
    expect(gauge.valid).toBe(true);
    expect(gauge.percent[axis === 'roll' ? 0 : 1] * sign).toBeGreaterThan(60);
    expect(gauge.marker[axis === 'roll' ? 0 : 1] * sign).toBeGreaterThan(7);
    await save(page, `gauge-${name}`, { model, tilted, tiltedPose, metrics: metrics(tiltedRows) });
    const eventsBeforeReturn = tilted.motion.model.flick_count;
    await slowReturn(page, model, axis, sign * 25);
    await segment(page, 'return-center', 25);
    expect((await state(page)).motion.model.flick_count).toBe(eventsBeforeReturn);
    expect(await inspect(page, 'trick.active')).toBe('');
    const leveling = await state(page);
    expect(leveling.rotation.level_state).toBe('level');
    expect(await inspect(page, 'hud_state["tilt_gauge"]["status"]')).toBe('LEVEL');
    await save(page, `level-${name}-correcting`, { tilted, leveling });
    await segment(page, 'settle', 270);
    const rows = await inspect(page, '_level_rows'), result = metrics(rows);
    const correctionRows = rows.filter(row => row[0] === 'return-center' || row[0] === 'settle');
    expect(result.lastTiltDegrees).toBeLessThan(5);
    // Center means upright pitch/roll. Free heading/yaw is deliberately not
    // locked by the assist, so retain its measurement without demanding zero.
    expect(result.lastPitchRollSpeedDps).toBeLessThan(3);
    expect(correctionRows.some(row => row[5].some(force => force > 1))).toBe(true);
    expect(rows.every(row => row[9] === 0 && row[10] === 0)).toBe(true);
    expect(rows.every(row => row[2] === 1 / 40)).toBe(true);
    expect(await inspect(page, 'trick.active')).toBe('');
    await save(page, `level-${name}-settled`, { model, tilted, leveling, result, rows });
  });
}

for (const [name, axis, sign] of [['right', 'roll', 1], ['left', 'roll', -1], ['back', 'pitch', 1], ['forward', 'pitch', -1]]) {
  test(`a visible six-degree ${name} lean rotates without centered-grip counterthrust`, async ({ page }) => {
    test.setTimeout(120000);
    const model = await calibrate(page);
    await fixture(page);
    expect((await state(page)).rotation.level_state).toBe('steady');
    await segment(page, 'small-lean', 160, { imu: imuAt(model, axis, sign * 6) });
    const snapshot = await state(page), rows = await inspect(page, '_level_rows');
    const result = metrics(rows), component = axis === 'roll' ? 2 : 0;
    const signedRate = rows.at(-1)[3][component] * sign * (axis === 'roll' ? 1 : -1) * degrees;
    const travel = axis === 'roll' ? result.rollTravelDegrees : result.pitchTravelDegrees;
    expect(snapshot.motion.model.armed).toBe(true);
    expect(snapshot.motion.model.tilt_valid).toBe(true);
    expect(snapshot.motion.model.flick_active).toBe(false);
    expect(snapshot.rotation.level_state).toBe('tilt');
    expect(snapshot.rotation.level_strength).toBe(0);
    expect(signedRate).toBeGreaterThan(8);
    expect(Math.abs(travel)).toBeGreaterThan(20);
    expect(result.deliveredForceSamples).toBeGreaterThan(20);
    const gauge = await inspect(page, 'hud_state["tilt_gauge"]');
    expect(gauge.status).toBe('TILT');
    expect(gauge.percent[axis === 'roll' ? 0 : 1] * sign).toBeGreaterThanOrEqual(5);
    await save(page, `small-lean-${name}`, { model, snapshot, result, gauge, rows });
  });
}

test('held tilt completes multiple full rolls, then neutral settles without snapping the body', async ({ page }) => {
  test.setTimeout(180000);
  const model = await calibrate(page);
  await fixture(page);
  await segment(page, 'hold', 320, { imu: imuAt(model, 'roll', 30) });
  const heldRows = await inspect(page, '_level_rows'), held = metrics(heldRows);
  expect(Math.abs(held.rollTravelDegrees)).toBeGreaterThan(720);
  expect(held.peakRateDps).toBeLessThan(170);
  const eventsBeforeReturn = (await state(page)).motion.model.flick_count;
  await slowReturn(page, model, 'roll', 30);
  await segment(page, 'center', 340);
  expect((await state(page)).motion.model.flick_count).toBe(eventsBeforeReturn);
  expect(await inspect(page, 'trick.active')).toBe('');
  const rows = await inspect(page, '_level_rows'), result = metrics(rows);
  const centerRows = rows.filter(row => row[0] === 'center');
  expect(result.lastTiltDegrees).toBeLessThan(5);
  expect(result.lastPitchRollSpeedDps).toBeLessThan(3);
  expect(centerRows.some(row => row[5].some(force => force > 1))).toBe(true);
  // Small inter-tick changes and observed delivered force distinguish a real
  // correction from setting the pose to upright when the badge centers.
  expect(Math.max(...centerRows.map(row => Math.hypot(...row[4]) * degrees))).toBeLessThan(4);
  await exec(page, `
from badge_controls import RotationControl as _LevelOnly
from physics import Simulation as _LevelSimulation
_level_probe = _LevelSimulation('flat')
_level_probe.y = 100.
_level_probe.q = (.7071067811865476,0.,0.,.7071067811865476)
_level_probe.airborne = True
_level_probe.contact_count = _level_probe.chassis_contacts = 0
_level_pose_before = (_level_probe.x, _level_probe.y, _level_probe.z, _level_probe.vx, _level_probe.vy, _level_probe.vz, tuple(_level_probe.q), tuple(_level_probe.omega), _level_probe.air_fraction)
_level_test = _LevelOnly()
_level_command = _level_test.mix(1./60., _level_probe, motion_signal=(0.,0.,0.,0.), level_enabled=True)
_level_pose_after = (_level_probe.x, _level_probe.y, _level_probe.z, _level_probe.vx, _level_probe.vy, _level_probe.vz, tuple(_level_probe.q), tuple(_level_probe.omega), _level_probe.air_fraction)
`);
  const [before, after, command] = await inspect(page, '[_level_pose_before, _level_pose_after, _level_command]');
  expect(after).toEqual(before);
  expect(command.nozzles.some(value => value > 0)).toBe(true);
  await save(page, 'level-multiple-rolls', { model, held, result, rows, mixerPoseBefore: before, mixerPoseAfter: after, command });
});

test('menus, raw control, calibration, invalid IMU and contact suppress center assist', async ({ page }) => {
  const model = await calibrate(page), snapshots = {};
  for (const sceneName of ['flat', 'ramp', 'loop', 'collection']) {
    // Keep the historical loop regression without exposing it through the scene UI.
    if (sceneName === 'loop') await exec(page, "reset_sim('loop')");
    else await action(page, 'scene', sceneName);
    await step(page, 12);
    const grounded = await state(page);
    expect(await inspect(page, 'sim.airborne')).toBe(false);
    expect(grounded.driving.nozzles).toEqual(Array(10).fill(0));
    expect(grounded.rotation.level_strength).toBe(0);
    snapshots[`ground-${sceneName}`] = grounded;
  }
  await action(page, 'scene', 'flat');
  await fixture(page, 70);
  await action(page, 'help');
  await step(page, 8);
  snapshots.help = await state(page);
  expect(snapshots.help.driving.nozzles).toEqual(Array(10).fill(0));
  expect(snapshots.help.rotation.level_strength).toBe(0);
  await action(page, 'help', false);
  await step(page, 8);
  await fixture(page, 70);
  await step(page, 8, { appControls: { nozzles: [0,0,0,0,0,0,0,0,1,0] } });
  snapshots.raw = await state(page);
  expect(snapshots.raw.driving.nozzles).toEqual([0,0,0,0,0,0,0,0,1,0]);
  expect(snapshots.raw.rotation.level_strength).toBe(0);
  await step(page, 8);
  await fixture(page, 70);
  // Normal-cadence freefall retains the gyro estimate by design. A missing
  // gravity sample after a long poll gap cannot establish a valid attitude.
  await step(page, 4, { dt: 500, imu: [0,0,0,0,0,0] });
  snapshots.invalid = await state(page);
  expect(snapshots.invalid.motion.model.tilt_valid).toBe(false);
  expect(snapshots.invalid.driving.nozzles).toEqual(Array(10).fill(0));
  expect(snapshots.invalid.rotation.level_strength).toBe(0);
  expect(await inspect(page, 'hud_state["tilt_gauge"]["marker"]')).toBe(null);
  expect(await inspect(page, 'hud_state["tilt_gauge"]["status"]')).toBe('WAIT');
  await save(page, 'gauge-invalid', { model, snapshots });
  await action(page, 'center');
  await step(page, 8);
  snapshots.calibrating = await state(page);
  expect(snapshots.calibrating.motion.phase).toBe('calibrating');
  expect(snapshots.calibrating.rotation.level_strength).toBe(0);
  expect(snapshots.calibrating.driving.nozzles).toEqual(Array(10).fill(0));
  await save(page, 'level-context-guards', { model, snapshots });
});

test('manual rotation overrides center assist and native HUD cache matches a full redraw', async ({ page }) => {
  const model = await calibrate(page);
  await fixture(page, 80);
  await step(page, 20, { buttons: ['RIGHT'] });
  const manual = await state(page);
  expect(manual.rotation.level_strength).toBe(0);
  expect(manual.driving.nozzles.some(value => value > 0)).toBe(true);
  await save(page, 'level-manual-override', { model, manual });
  await step(page, 180);
  const records = [];
  for (const [name, options] of [
    ['center', {}], ['left', { imu: imuAt(model, 'roll', -30) }],
    ['right', { imu: imuAt(model, 'roll', 30) }], ['up', { imu: imuAt(model, 'pitch', 25) }],
    ['down', { imu: imuAt(model, 'pitch', -25) }], ['invalid', { dt: 500, imu: [0,0,0,0,0,0] }],
    ['clipped', { imu: [65534,0,0,0,0,0] }], ['center-again', {}],
  ]) {
    await step(page, 24, options);
    const pixels = () => page.locator('canvas').evaluate(canvas =>
      Array.from(canvas.getContext('2d').getImageData(0, 0, 320, 240).data));
    const retained = await pixels();
    await exec(page, '_level_saved_update = __import__("u26_runtime").active.update\nhud.invalidate()\n__import__("u26_runtime").active.update = draw_hud');
    // A drawing-only callback transfers the native framebuffer without
    // advancing physics or the sensor filter between the two images.
    await step(page, 1, { dt: 0, ...options });
    const redrawn = await pixels();
    await exec(page, '__import__("u26_runtime").active.update = _level_saved_update');
    const differences = retained.reduce((sum, value, i) => sum + Number(value !== redrawn[i]), 0);
    expect(differences, name).toBe(0);
    const hud = await inspect(page, 'hud_state');
    if (name === 'invalid' || name === 'clipped') {
      expect(hud.tilt_gauge.status).toBe(name === 'invalid' ? 'WAIT' : 'CLIP');
      expect(hud.tilt_gauge.marker).toBe(null);
      expect(hud.tilt_gauge.percent).toEqual([null, null]);
    }
    records.push({ name, differences, sha256: createHash('sha256').update(Buffer.from(retained)).digest('hex'), hud });
    await save(page, `gauge-cache-${name}`, { model, record: records.at(-1) });
  }
  await save(page, 'level-hud-cache', { model, manual, records });
});

test('a clipped sensor cannot rearm motion until a fresh valid sample arrives', async ({ page }) => {
  const model = await calibrate(page);
  await fixture(page, 85);
  // Wide range scales injected accel counts by two, so this reaches the
  // signed sensor rail in the actual badge.imu parser.
  const clippedImu = [65534,0,0,0,0,0];
  const beforeClip = await state(page);
  await step(page, 4, { imu: clippedImu });
  const firstClip = await state(page);
  expect(firstClip.motion.model.sensor_clipped).toBe(true);
  expect(firstClip.motion.model.tilt_valid).toBe(false);
  expect(firstClip.motion.model.armed).toBe(false);
  expect(firstClip.motion.model.flick_active).toBe(false);
  expect(firstClip.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 4);
  const firstValid = await state(page);
  expect(firstValid.motion.model.armed).toBe(true);
  expect(firstValid.motion.model.flick_active).toBe(false);
  expect(firstValid.motion.model.flick_count).toBe(beforeClip.motion.model.flick_count);
  expect(Math.abs(firstValid.motion.model.angle_x)).toBeLessThan(.1);
  expect(Math.abs(firstValid.motion.model.angle_y)).toBeLessThan(.1);
  await step(page, 4, { imu: clippedImu });
  await action(page, 'motion', 'tilt');
  await step(page, 8, { imu: clippedImu });
  const clipped = await state(page), gauge = await inspect(page, 'hud_state["tilt_gauge"]');
  expect(clipped.motion.model.sensor_clipped).toBe(true);
  expect(clipped.motion_neutral_required).toBe(true);
  expect(clipped.motion.model.armed).toBe(false);
  expect(clipped.rotation.level_strength).toBe(0);
  expect(clipped.driving.nozzles).toEqual(Array(10).fill(0));
  expect(gauge.status).toBe('CLIP');
  expect(gauge.marker).toBe(null);
  await save(page, 'gauge-clipped-rearm-guard', { model, beforeClip, firstClip, firstValid, clipped, gauge });
  await step(page, 24, { imu: imuAt(model, 'roll', 8) });
  const leaning = await state(page);
  expect(leaning.motion_neutral_required).toBe(false);
  expect(leaning.motion.model.armed).toBe(true);
  expect(leaning.rotation.level_strength).toBe(0);
  expect(leaning.driving.nozzles.some(value => value > 0)).toBe(true);
  await step(page, 24);
  const recovered = await state(page);
  expect(recovered.motion_neutral_required).toBe(false);
  expect(recovered.motion.model.armed).toBe(true);
  expect(recovered.rotation.level_strength).toBeGreaterThan(0);
  await segment(page, 'settle-after-clip', 160);
  const result = metrics(await inspect(page, '_level_rows'));
  expect(result.lastTiltDegrees).toBeLessThan(5);
  expect(result.lastPitchRollSpeedDps).toBeLessThan(3);
  await save(page, 'level-recovered-after-clip', { model, clipped, leaning, recovered, result });
});
