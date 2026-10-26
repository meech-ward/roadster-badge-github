import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/sandbox-speed');
const liftJets = [0, 0, 0, 0, 1, 1, 1, 1, 0, 0];
const state = page => page.evaluate(() => badge.inspect(
  '[sim.time,sim.x,sim.y,sim.z,sim.vx,sim.vy,sim.vz,sim.q,sim.omega,sim.air_mass,sim.contact_count,sim.chassis_contacts]'));
const telemetry = page => page.evaluate(() => badge.inspect(
  '{"status":control_status(),"fov":scene.camera_fov,"distance":scene.camera_distance,' +
  '"side_offset":scene.camera_side_offset,"streaks":scene.speed_streak_count,' +
  '"visual_intensity":scene.visual_intensity,"forces":sim.nozzle_forces}'));

async function record(name, data, page) {
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify(data, null, 2) + '\n');
  if (page) {
    const png = await page.evaluate(() => badge.snapshot());
    await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
  }
}

async function freshReset(page) {
  // Drain previous-poll elapsed time before resetting so repeated input
  // schedules start from the same physical tick, including the first frame.
  await page.evaluate(() => badge.step(2, { dt: 0 }));
  await page.evaluate(() => badge.appAction('reset'));
  await page.evaluate(() => badge.step(2, { dt: 0 }));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await page.evaluate(() => badge.appAction('scene', 'flat'));
});

test('keyboard rear boost produces native speed effects and a measured faster 0–60 launch', async ({ page }) => {
  const runs = [];
  for (const boosted of [false, true]) {
    await freshReset(page);
    await page.keyboard.down('w');
    if (boosted) await page.keyboard.down('b');
    const samples = [];
    let midLaunch = null;
    for (let i = 0; i < 50; i++) {
      await page.evaluate(() => badge.step(2));
      const sample = await telemetry(page);
      samples.push(sample);
      if (!midLaunch && sample.status.speed_mph > 40) {
        midLaunch = sample;
        await record(boosted ? 'speed-boost-launch' : 'speed-motor-launch', sample, page);
        if (boosted) await page.screenshot({ path: `${output}/speed-boost-launch-page.png`, fullPage: true });
      }
      if (sample.status.acceleration_state === 'complete') break;
    }
    await page.keyboard.up('w');
    if (boosted) await page.keyboard.up('b');
    const last = samples.at(-1);
    expect(last.status.acceleration_state).toBe('complete');
    expect(last.status.speed_mph).toBeGreaterThanOrEqual(60);
    if (boosted) {
      expect(last.status.acceleration_time).toBeGreaterThan(.4);
      expect(last.status.acceleration_time).toBeLessThan(1.);
    } else {
      expect(last.status.acceleration_time).toBeGreaterThan(1.);
      expect(last.status.acceleration_time).toBeLessThan(4.);
    }
    expect(samples.some(row => row.status.acceleration_state === 'running')).toBe(true);
    expect(midLaunch).not.toBeNull();
    if (boosted) {
      expect(midLaunch.status.rear_force).toBeGreaterThan(10000);
      expect(midLaunch.status.boost_mix).toBeGreaterThan(.1);
      expect(midLaunch.visual_intensity).toBeGreaterThan(.1);
      expect(midLaunch.fov).toBeGreaterThan(50);
      expect(midLaunch.fov).toBeLessThanOrEqual(62);
      expect(midLaunch.distance).toBeGreaterThan(11);
      expect(midLaunch.streaks).toBeGreaterThan(0);
      expect(midLaunch.streaks).toBeLessThanOrEqual(12);
      await expect(page.locator('#roadster-speed-readout')).toContainText('mph');
    } else {
      expect(last.status.rear_force).toBe(0);
      expect(last.status.boost_mix).toBe(0);
    }
    runs.push({ boosted, samples, midLaunch, final: last, physical: await state(page) });
  }
  expect(runs[1].final.status.acceleration_time).toBeLessThan(runs[0].final.status.acceleration_time);
  await record('speed-native-launches', { runtime: 'MicroPython WASM / native pico3d',
    conditions: 'Keyboard W or W+B, full tank, flat ground, fan off, 33 ms rendered frames.',
    interpretation: 'Measured simulated acceleration; not a physical-vehicle or hardware frame-rate claim.', runs });
});

test('empty rear jets and lift alone do not produce false forward-boost feedback', async ({ page }) => {
  await freshReset(page);
  await page.evaluate(() => badge.exec('from physics import TANK_RESIDUAL\nu26_runtime.app.sim.air_mass=TANK_RESIDUAL'));
  await page.keyboard.down('b');
  await page.evaluate(() => badge.step(20));
  await page.keyboard.up('b');
  const empty = await telemetry(page);
  expect(empty.forces.slice(0, 4)).toEqual([0, 0, 0, 0]);
  expect(empty.status.rear_force).toBe(0);
  expect(empty.status.boost_mix).toBe(0);
  expect(empty.status.speed_mph).toBeLessThan(.01);
  expect(empty.visual_intensity).toBe(0);
  expect(empty.streaks).toBe(0);
  expect(empty.fov).toBe(50);
  expect(empty.status.acceleration_state).not.toBe('complete');

  await freshReset(page);
  await page.evaluate(lift => badge.step(22, { appControls: { nozzles: lift } }), liftJets);
  const lift = await telemetry(page);
  const airborne = await state(page);
  expect(airborne[2]).toBeGreaterThan(2);
  expect(airborne[10]).toBe(0);
  expect(lift.forces.slice(4, 8).every(force => force > 100)).toBe(true);
  expect(lift.status.rear_force).toBe(0);
  expect(lift.status.boost_mix).toBe(0);
  // Small settling pitch can produce real horizontal drift. The readout
  // must report that drift, without treating vertical lift as forward speed.
  expect(lift.status.speed_mph).toBeLessThan(.1);
  expect(lift.status.speed_mph).toBeCloseTo(Math.hypot(airborne[4], airborne[6]) * 2.2369362920544, 8);
  expect(Math.abs(airborne[5])).toBeGreaterThan(5);
  expect(lift.visual_intensity).toBe(0);
  expect(lift.streaks).toBe(0);
  expect(lift.status.acceleration_state).not.toBe('running');
  expect(lift.status.acceleration_state).not.toBe('complete');
  await record('speed-no-false-boost', { empty, lift, airborne }, page);

  await freshReset(page);
  await page.keyboard.down('w');
  await page.evaluate(() => badge.step(12));
  expect((await telemetry(page)).status.acceleration_state).toBe('running');
  await page.keyboard.down('j');
  await page.evaluate(() => badge.step(18));
  await page.keyboard.up('w');
  await page.keyboard.up('j');
  const interrupted = await telemetry(page);
  expect(interrupted.status.acceleration_state).toBe('invalid');
  expect(interrupted.status.acceleration_reason).toBe('Left level ground');
  expect(interrupted.status.acceleration_time).toBeGreaterThan(0);
  await expect(page.locator('#roadster-speed-readout')).toContainText('0–60 mph Left level ground');
  await expect(page.locator('#roadster-speed-readout')).not.toContainText(
    interrupted.status.acceleration_time.toFixed(2) + ' s');
  await record('speed-airborne-invalidates-launch', interrupted, page);
});

test('reduced effects leave physics and the launch timer intact and survive reset', async ({ page }) => {
  const beforeFrames = 12;
  const afterFrames = 18;
  await freshReset(page);
  await page.keyboard.down('w');
  await page.keyboard.down('b');
  await page.evaluate(frames => badge.step(frames), beforeFrames);
  const before = await state(page);
  const feedbackBefore = await telemetry(page);
  expect(feedbackBefore.status.acceleration_state).toBe('running');
  expect(feedbackBefore.streaks).toBeGreaterThan(0);
  await page.locator('#roadster-effects').click();
  await expect(page.locator('#roadster-effects')).toHaveAttribute('aria-pressed', 'true');
  expect(await state(page)).toEqual(before);
  const afterToggle = await telemetry(page);
  expect(afterToggle.status.effects).toBe('reduced');
  expect(afterToggle.status.acceleration_time).toBe(feedbackBefore.status.acceleration_time);
  await page.evaluate(frames => badge.step(frames), afterFrames);
  const reduced = await telemetry(page);
  const reducedPhysical = await state(page);
  await page.keyboard.up('w');
  await page.keyboard.up('b');
  expect(reduced.status.acceleration_state).toBe('complete');
  expect(reduced.status.acceleration_time).toBeGreaterThan(.4);
  expect(reduced.status.acceleration_time).toBeLessThan(1.);
  expect(reduced.status.rear_force).toBeGreaterThan(1000);
  expect(reduced.status.boost_mix).toBeGreaterThan(.1);
  expect(reduced.visual_intensity).toBe(0);
  expect(reduced.streaks).toBe(0);
  expect(reduced.fov).toBe(50);
  expect(reduced.distance).toBe(11);
  await record('speed-reduced-effects', { before, feedbackBefore, afterToggle, reduced, reducedPhysical }, page);

  await freshReset(page);
  const reset = await telemetry(page);
  expect(reset.status.effects).toBe('reduced');
  expect(reset.status.acceleration_state).toBe('ready');
  expect(reset.status.acceleration_time).toBeNull();
  expect(reset.status.boost_mix).toBe(0);
  await expect(page.locator('#roadster-effects')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#roadster-effects').click();
  await expect(page.locator('#roadster-effects')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.down('w');
  await page.keyboard.down('b');
  await page.evaluate(frames => badge.step(frames), beforeFrames + afterFrames);
  const fullPhysical = await state(page);
  const full = await telemetry(page);
  await page.keyboard.up('w');
  await page.keyboard.up('b');
  expect(fullPhysical).toEqual(reducedPhysical);
  expect(full.status.acceleration_time).toBe(reduced.status.acceleration_time);
  expect(full.streaks).toBeGreaterThan(0);
  expect(full.fov).toBeGreaterThan(reduced.fov);
  await record('speed-effects-physics-equivalence', { reset, reducedPhysical, fullPhysical,
    reducedTimer: reduced.status.acceleration_time, fullTimer: full.status.acceleration_time, full });
});

test('behind and chase cameras keep their selection while boosting and resetting', async ({ page }) => {
  await page.locator('#roadster-scene').selectOption('ramp');
  await page.evaluate(() => badge.step(2, { dt: 0 }));
  let sample = await telemetry(page);
  expect(sample.status.camera).toBe('behind');
  expect(sample.side_offset).toBe(0);
  const before = await state(page);
  await page.locator('#roadster-camera').selectOption('chase');
  expect(await state(page)).toEqual(before);
  await page.evaluate(() => badge.step(2, { dt: 0 }));
  const chase = await telemetry(page);
  expect(chase.status.camera).toBe('chase');
  expect(chase.side_offset).toBe(4.8);
  await freshReset(page);
  expect((await telemetry(page)).status.camera).toBe('chase');
  await page.locator('#roadster-camera').selectOption('behind');
  const aligned = await state(page);
  expect(aligned[1]).toBe(0);
  await page.keyboard.down('w');
  await page.keyboard.down('b');
  await page.evaluate(() => badge.step(20));
  await page.keyboard.up('w');
  await page.keyboard.up('b');
  sample = await telemetry(page);
  expect(sample.status.camera).toBe('behind');
  expect(sample.side_offset).toBe(0);
  expect(sample.fov).toBeGreaterThan(50);
  expect(sample.distance).toBeGreaterThan(11);
  expect(sample.streaks).toBeGreaterThan(0);
  expect((await state(page))[1]).toBeCloseTo(0, 6);
  await record('speed-ramp-behind', { before, chase, boosted: sample }, page);
  await page.screenshot({ path: `${output}/speed-ramp-behind-page.png`, fullPage: true });
  await freshReset(page);
  expect((await telemetry(page)).status.camera).toBe('behind');
  await expect(page.locator('#roadster-camera')).toHaveValue('behind');
});
