import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-ground-camera');
const center = [0, 0, 16384, 0, 0, 0];
const step = (page, frames, options = {}) => page.evaluate(
  ({ frames, options }) => badge.step(frames, options),
  { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(
  ({ name, value }) => badge.appAction(name, value), { name, value });
const exec = (page, code) => page.evaluate(
  code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const read = page => page.evaluate(() => badge.inspect(`{
    'badge': badge_snapshot(), 'position': [sim.x,sim.y,sim.z], 'q':sim.q,
    'airborne':sim.airborne, 'wheel_contacts':sim.contact_count,
    'eye':[scene.background.ex-sim.x,scene.background.ey-sim.y,scene.background.ez-sim.z],
    'look':[scene.ground_look_yaw,scene.ground_look_height],
    'tilt_status':hud_state['tilt_gauge']['status'],
    'camera':scene.camera_view}`));

function lean(model, axis, degrees) {
  const radians = degrees * Math.PI / 180;
  const basis = axis === 'x' ? model.basis_x : model.basis_y;
  return model.neutral.map((value, i) => Math.round(16384 *
    (value * Math.cos(radians) + basis[i] * Math.sin(radians)))).concat([0, 0, 0]);
}

async function record(page, name, data) {
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, `${name}.json`), JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d with synthetic IMU and logical input. No physical sensor feel or device timing claim.',
    ...data,
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(resolve(output, `${name}.png`), Buffer.from(png.split(',')[1], 'base64'));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await action(page, 'center');
  await step(page, 48);
  expect((await read(page)).badge.motion.phase).toBe('ready');
});

test('grounded lean looks in four directions and centers without firing jets', async ({ page }) => {
  test.setTimeout(120000);
  const model = (await read(page)).badge.motion.model;
  const rows = [];
  for (const [name, axis, degrees] of [
    ['left', 'x', -24], ['right', 'x', 24], ['look-up', 'y', 24], ['look-down', 'y', -24],
  ]) {
    await action(page, 'reset');
    await step(page, 48);
    const neutral = await read(page);
    await step(page, 48, { imu: lean(model, axis, degrees) });
    const looking = await read(page);
    expect(looking.badge.camera_look.active).toBe(true);
    expect(looking.tilt_status).toBe('LOOK');
    expect(looking.airborne).toBe(false);
    expect(looking.badge.motion.model.armed).toBe(false);
    expect(looking.badge.driving.nozzles).toEqual(Array(10).fill(0));
    expect(looking.badge.trick).toBe('');
    expect(looking.position[0]).toBeCloseTo(neutral.position[0], 5);
    expect(looking.position[2]).toBeCloseTo(neutral.position[2], 5);
    const index = axis === 'x' ? 0 : 1;
    const direction = Math.sign(degrees) * (axis === 'x' ? 1 : -1);
    expect(looking.look[index] * direction).toBeGreaterThan(index === 0 ? .15 : .6);
    expect(Math.abs(looking.look[1 - index])).toBeLessThan(.001);
    if (axis === 'x') {
      expect((looking.eye[0] - neutral.eye[0]) * Math.sign(degrees)).toBeLessThan(-1);
      expect(looking.eye[1]).toBeCloseTo(neutral.eye[1], 5);
    } else {
      // Lowering the eye looks upward at the car; raising it looks downward.
      expect((looking.eye[1] - neutral.eye[1]) * Math.sign(degrees)).toBeLessThan(-.6);
    }
    await record(page, name, { neutral, looking });
    await step(page, 64);
    const returned = await read(page);
    expect(Math.abs(returned.look[0])).toBeLessThan(.01);
    expect(Math.abs(returned.look[1])).toBeLessThan(.03);
    rows.push({ name, neutral, looking, returned });
  }
  await record(page, 'four-axis-and-center', { rows });
});

test('ground look respects scenes, view resets, invalid samples, menus and airborne jets', async ({ page }) => {
  test.setTimeout(120000);
  const model = (await read(page)).badge.motion.model;
  const right = lean(model, 'x', 24);
  const rows = [];
  for (const sceneName of ['flat', 'ramp', 'collection']) {
    for (const view of ['chase', 'behind']) {
      await action(page, 'scene', sceneName);
      await action(page, 'camera', view);
      await step(page, 36, { imu: right, pad: ['UP'] });
      const state = await read(page);
      expect(state.badge.camera_look.active, `${sceneName}/${view}`).toBe(true);
      expect(state.tilt_status).toBe('LOOK');
      expect(state.look[0]).toBeGreaterThan(.15);
      expect(state.badge.driving.throttle).toBe(1);
      expect(state.badge.driving.nozzles).toEqual(Array(10).fill(0));
      rows.push({ sceneName, view, state });
    }
  }
  await action(page, 'scene', 'flat');
  await action(page, 'motion', 'flick');
  await step(page, 32, { imu: right });
  expect((await read(page)).badge.camera_look.active).toBe(true);
  await action(page, 'camera', 'behind');
  expect((await read(page)).look).toEqual([0, 0]);
  await step(page, 20, { imu: right });
  await step(page, 2, { imu: [100000, 0, 0, 0, 0, 0] });
  const clipped = await read(page);
  expect(clipped.badge.motion.model.sensor_clipped).toBe(true);
  expect(clipped.badge.camera_look.active).toBe(false);
  expect(clipped.tilt_status).toBe('CLIP');
  await action(page, 'help', true);
  await step(page, 48, { imu: right });
  const overlay = await read(page);
  expect(overlay.badge.camera_look.active).toBe(false);
  expect(overlay.tilt_status).not.toBe('LOOK');
  expect(Math.abs(overlay.look[0])).toBeLessThan(.01);
  await action(page, 'help', false);
  await action(page, 'inspect', 'roadster');
  await step(page, 8, { imu: right });
  const inspection = await read(page);
  expect(inspection.badge.camera_look.active).toBe(false);
  expect(inspection.tilt_status).not.toBe('LOOK');
  expect(inspection.look).toEqual([0, 0]);
  await action(page, 'scene', 'flat');
  await action(page, 'motion', 'both');
  await step(page, 48);
  await step(page, 36, { imu: right });
  const beforeLift = await read(page);
  expect(beforeLift.badge.camera_look.active).toBe(true);
  expect(beforeLift.tilt_status).toBe('LOOK');
  // Inner right is firmware SELECT; this remains lift, with live air tilt.
  await step(page, 16, { imu: right, pad: ['SELECT'] });
  const flying = await read(page);
  expect(flying.airborne).toBe(true);
  expect(flying.badge.camera_look.active).toBe(false);
  expect(flying.tilt_status).not.toBe('LOOK');
  expect(flying.badge.motion_allowed).toBe(true);
  expect(flying.badge.driving.nozzles.some(value => value > 0)).toBe(true);
  expect(Math.abs(flying.look[0])).toBeLessThan(Math.abs(beforeLift.look[0]));
  // An inverted chassis touch must not masquerade as wheel-supported looking.
  await exec(page, "sim.x=0.; sim.y=.55; sim.z=-20.; sim.q=(0.,0.,0.,1.); sim.vx=sim.vy=sim.vz=0.; sim.omega[:]=[0.,0.,0.]");
  await step(page, 4, { imu: right });
  const upsideDown = await read(page);
  expect(upsideDown.badge.camera_look.active).toBe(false);
  expect(upsideDown.tilt_status).not.toBe('LOOK');
  await action(page, 'center');
  expect((await read(page)).look).toEqual([0, 0]);
  await record(page, 'gates-and-air-handoff', { rows, clipped, overlay, inspection, beforeLift, flying, upsideDown });
});
