import { test, expect } from '@playwright/test';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-scene-experience');
const step = (page, frames = 2, options = {}) => page.evaluate(({ frames, options }) => badge.step(frames, options), {
  frames, options: { dt: 25, buttons: [], pad: [], imu: [0,0,16384,0,0,0], appControls: {}, ...options },
});
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
async function tap(page, name, source = 'pad') {
  await step(page, 2, { [source]: [name] });
  await step(page, 2);
}
async function save(page, name, extra = {}) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const frame = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(frame.split(',')[1], 'base64'));
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d pixels; logical touch/clicky/IMU inputs are synthetic. Does not establish badge frame rate or physical capacitive HOME mapping.',
    sourceHashes: manifest.files.filter(f => f.path.startsWith('/system/apps/roadster_sandbox/')),
    status: await inspect(page, 'control_status()'), badge: await inspect(page, 'badge_snapshot()'), ...extra,
  }, null, 2)+'\n');
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 3);
});

test('middle touch HOME opens a scene chooser; browsing and cancelling preserve live physics', async ({ page }) => {
  await exec(page, 'sim.y = 10.; sim.vy = 1.');
  const before = await inspect(page, '[sim.time, sim.y, scene_mode]');
  const opened = await step(page, 3, { pad: ['HOME'] });
  expect(opened.finished).toBe(false);
  expect(await inspect(page, '[scenes_open, scene_index, scene_mode]')).toEqual([true, 0, 'flat']);
  await step(page, 12, { pad: ['HOME'] });
  expect(await inspect(page, 'scene_index')).toBe(0); // Held pad never repeats.
  await step(page, 2);
  await tap(page, 'HOME');
  expect(await inspect(page, '[scene_index, scene_mode]')).toEqual([1, 'flat']);
  await save(page, 'scene-picker-open-ramp');
  await tap(page, 'SELECT'); // inner-right pad cancels
  const after = await inspect(page, '[scenes_open, sim.time, sim.y, scene_mode]');
  expect(after[0]).toBe(false);
  expect(after[1]).toBeGreaterThan(before[0]);
  expect(after[2]).not.toBe(before[1]);
  expect(after[2]).toBeGreaterThan(2);
  expect(after[3]).toBe('flat');
});

test('picker makes all three scenes explicit; outer pad confirms without firing old thrust', async ({ page }) => {
  const chosen = [];
  for (const [index, name] of ['flat', 'ramp', 'collection'].entries()) {
    await action(page, 'scenes');
    for (let i = 0; i < 3 && await inspect(page, 'scene_index') !== index; i++) await tap(page, 'HOME');
    await step(page, 2, { pad: ['BACK'] });
    expect(await inspect(page, '[scenes_open, scene_mode]')).toEqual([false, name]);
    const state = await inspect(page, 'badge_snapshot()');
    expect(state.driving.nozzles.every(n => n === 0)).toBe(true);
    expect(state.release_pads).toBe(true);
    await step(page, 2);
    const data = await inspect(page, '[scene_mode, sim.scene_name, len(sim.obstacles), scene.follow_view, scene.triangle_count]');
    expect(data[1]).toBe(name === 'collection' ? 'flat' : name);
    expect(data[2] > 0).toBe(name === 'collection');
    expect(data[4]).toBeGreaterThan(0);
    if (name === 'ramp') expect(data[3]).toBe('behind');
    chosen.push(data);
    await save(page, `scene-${name}-ground`);
  }
  await save(page, 'scene-picker-confirmed-scenes', { chosen });
});

test('three-scene picker wraps in both directions and removed loop cannot reset live play', async ({ page }) => {
  expect(await inspect(page, '[row[0] for row in SCENES]')).toEqual(['flat', 'ramp', 'collection']);
  expect(await page.locator('#roadster-scene option').evaluateAll(options => options.map(option => option.value))).toEqual(['collection', 'ramp', 'flat']);
  await exec(page, 'sim.y = 12.; sim.vy = 1.; sim.vx = 2.');
  const before = await inspect(page, '[id(sim), sim.time, sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz, sim.q, scene_mode, controls]');
  await expect(action(page, 'scene', 'loop')).rejects.toThrow('scene must be collection, ramp or flat');
  expect(await inspect(page, '[id(sim), sim.time, sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz, sim.q, scene_mode, controls]')).toEqual(before);
  await action(page, 'scenes');
  const browsed = [];
  for (const [direction, index] of [['LEFT', 2], ['RIGHT', 0], ['RIGHT', 1], ['RIGHT', 2], ['RIGHT', 0]]) {
    await tap(page, direction);
    const current = await inspect(page, '[scene_index, scene_mode, sim.time, sim.y]');
    expect(current[0]).toBe(index);
    expect(current[1]).toBe('flat');
    expect(current[2]).toBeGreaterThan(before[1]);
    expect(current[3]).toBeGreaterThan(2);
    browsed.push({ direction, current });
  }
  await save(page, 'scene-picker-three-choices', { before, browsed });
  await tap(page, 'SELECT'); // Inner-right cancels; no scene reset.
  expect(await inspect(page, '[scenes_open, scene_mode]')).toEqual([false, 'flat']);
  expect(await inspect(page, 'sim.time')).toBeGreaterThan(before[1]);
});

test('scene picker suppresses rotation and fan toggles; physical HOME still exits cleanly', async ({ page }) => {
  await action(page, 'center');
  await step(page, 46);
  await exec(page, 'sim.y = 15.');
  await step(page, 3);
  await action(page, 'scenes');
  await step(page, 2, { buttons: ['RIGHT'], pad: ['SELECT'] });
  // Cancelled on inner pad; its held press cannot become lift on the next frame.
  const blocked = await inspect(page, 'badge_snapshot()');
  expect(blocked.driving.nozzles.every(n => n === 0)).toBe(true);
  expect(blocked.motion.model.armed).toBe(false);
  await step(page, 2);
  await action(page, 'scenes');
  await step(page, 2, { buttons: ['SELECT'] }); // confirm, not fan toggle
  expect(await inspect(page, 'physical_fan')).toBe(false);
  const finished = await step(page, 2, { buttons: ['HOME'] });
  expect(finished).toMatchObject({ finished: true, reason: 'home' });
  expect(await inspect(page, '[exited, badge.default_clear is original_clear]')).toEqual([true, true]);
});

test('fan has an obvious ON/OFF card and distinguishes running without ground suction', async ({ page }) => {
  await step(page, 25);
  expect(await inspect(page, 'hud_state["fan_state"]')).toBe('OFF');
  await save(page, 'fan-off-card');
  await tap(page, 'SELECT', 'buttons');
  await step(page, 24);
  expect(await inspect(page, '[physical_fan, hud_state["fan_state"]]')).toEqual([true, 'GRIPPING']);
  await save(page, 'fan-on-gripping-card');
  await exec(page, 'sim.y = 20.; sim.vy = 0.');
  // The pressure chamber releases over its specified physical time constant;
  // leave enough time for residual suction to decay below the HUD threshold.
  const releaseFrames = await inspect(page, "int(8.*__import__('vehicle_spec').FAN_FALL_TIME/.025)+2");
  await step(page, releaseFrames);
  expect(await inspect(page, 'sim.fan_force')).toBeLessThan(100);
  expect(await inspect(page, '[physical_fan, hud_state["fan_state"]]')).toEqual([true, 'NO SEAL']);
  await save(page, 'fan-on-airborne-card');
  await tap(page, 'SELECT', 'buttons');
  expect(await inspect(page, '[physical_fan, hud_state["fan_state"]]')).toEqual([false, 'OFF']);
  await action(page, 'help');
  await step(page, 2);
  await save(page, 'controls-scenes-fan');
});
