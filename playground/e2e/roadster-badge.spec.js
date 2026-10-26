import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-badge');
// Firmware names describe its original menu mapping, not the game's pad labels.
// In the upside-down playing grip, the outer right pad is now game SELECT.
const OUTER = 'BACK';
const INNER = 'SELECT';
const center = [0, 0, 16384, 0, 0, 0];
const step = (page, frames, options = {}) => page.evaluate(
  ({ frames, options }) => badge.step(frames, options),
  { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(
  ({ name, value }) => badge.appAction(name, value), { name, value });
const read = async page => {
  const [snapshot, physics, trick, forces] = await page.evaluate(() => badge.inspect(
    '[badge_snapshot(), [sim.x, sim.y, sim.z, sim.speed, sim.air_fraction, sim.omega], trick.active, sim.nozzle_forces]'));
  return { ...snapshot, physics, trick, forces };
};

async function tap(page, name) {
  await step(page, 2, { pad: [name] });
  await step(page, 2);
}

async function calibrate(page) {
  await action(page, 'center');
  await step(page, 44);
  const ready = await read(page);
  expect(ready.motion.phase).toBe('ready');
  expect(ready.motion.model.calibrated).toBe(true);
  expect(ready.motion.model.channels).toEqual({ left: 0, right: 0, up: 0, down: 0 });
  return ready;
}

async function record(name, page, data) {
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Native MicroPython/WASM and pico3d pixels; injected IMU and synthetic range registers. No physical timing or orientation claim.',
    ...data,
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}

async function angularSamples(page, axis, rates, pad = [INNER]) {
  let angle = 0;
  const samples = [];
  for (const rate of rates) {
    angle += rate * .025;
    const radians = angle * Math.PI / 180;
    const imu = axis === 'x'
      ? [Math.round(16384 * Math.sin(radians)), 0, Math.round(16384 * Math.cos(radians)), 0, Math.round(-rate / .00875), 0]
      : [0, Math.round(16384 * Math.sin(radians)), Math.round(16384 * Math.cos(radians)), Math.round(rate / .00875), 0, 0];
    await step(page, 1, { pad, imu });
    samples.push({ rate, angle, imu, snapshot: await read(page) });
  }
  // Each callback reads the preceding input poll. Process the final sample.
  const restingImu = [...samples.at(-1).imu.slice(0, 3), 0, 0, 0];
  await step(page, 1, { pad, imu: restingImu });
  samples.push({ rate: 0, angle, imu: restingImu, snapshot: await read(page) });
  return { samples, restingImu };
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

test('badge setup centers in the game, renders native pixels, and HOME restores sensor ranges', async ({ page }) => {
  const initial = await read(page);
  expect(initial.active).toBe(true);
  expect(initial.quality).toBe('badge');
  expect(initial.physics_hz).toBe(40);
  expect(initial.air_mode).toBe('unlimited');
  expect(initial.physics[4]).toBe(1);
  expect(initial.motion.phase).toBe('center');
  expect(initial.motion.orientation_available).toBe(false);
  expect(initial.motion.sensor_profile.current_regs).toEqual([0x48, 0x48]);
  expect(initial.motion.model.mode).toBe('both');
  // Centering is confirmed by the outer pad as well as menu choices.
  await tap(page, OUTER);
  await step(page, 42);
  const ready = await read(page);
  expect(ready.motion.phase).toBe('ready');
  expect(ready.motion.model.calibrated).toBe(true);
  expect(ready.driving.nozzles.every(value => value === 0)).toBe(true);
  expect(ready.physics_hz).toBe(40);
  expect(ready.sim_time).toBeGreaterThan(initial.sim_time);
  expect(ready.motion.model.accel_scale).toBeCloseTo(1 / 8192, 8);
  expect(ready.motion.model.gyro_scale).toBeCloseTo(.035, 6);
  expect(await page.evaluate(() => badge.frame)).toEqual({ width: 320, height: 240 });
  const colors = await page.locator('canvas').evaluate(canvas => {
    const pixels = canvas.getContext('2d').getImageData(0, 36, 320, 178).data;
    return new Set(Array.from({ length: pixels.length / 4 }, (_, i) =>
      `${pixels[4 * i]},${pixels[4 * i + 1]},${pixels[4 * i + 2]}`)).size;
  });
  expect(colors).toBeGreaterThan(20);
  await record('ready-roadster', page, { initial, ready, colors });
  await action(page, 'center');
  await step(page, 48, { pad: [OUTER] });
  const heldDuringCenter = await read(page);
  expect(heldDuringCenter.motion.phase).toBe('ready');
  expect(heldDuringCenter.release_pads).toBe(true);
  expect(heldDuringCenter.driving.nozzles.every(value => value === 0)).toBe(true);
  await step(page, 2);
  expect((await read(page)).release_pads).toBe(false);
  const home = await step(page, 2, { buttons: ['HOME'] });
  expect(home).toMatchObject({ finished: true, reason: 'home' });
  const exited = await read(page);
  expect(exited.exited).toBe(true);
  expect(exited.motion.sensor_profile.current_regs).toEqual([0x40, 0x40]);
  expect(exited.motion.sensor_profile.active).toBe(false);
  expect(await page.evaluate(() => badge.inspect('badge.imu()'))).toEqual(center);
  await record('home-restores-ranges', page, { heldDuringCenter, home, exited });
});

test('D-pad drives, brakes and reverses; outer SELECT boosts and inner BACK lifts', async ({ page }) => {
  await calibrate(page);
  await step(page, 24, { pad: ['UP'] });
  const driving = await read(page);
  expect(driving.driving.throttle).toBe(1);
  expect(driving.physics[3]).toBeGreaterThan(4);
  await step(page, 2, { pad: ['DOWN'] });
  const braking = await read(page);
  expect(braking.driving.brake).toBe(1);
  expect(braking.driving.throttle).toBe(0);
  expect(braking.sim_time).toBeGreaterThan(driving.sim_time);
  await step(page, 65, { pad: ['DOWN'] });
  const reverse = await read(page);
  expect(reverse.driving.throttle).toBe(-1);
  expect(reverse.physics[3]).toBeLessThan(-1);
  expect(reverse.sim_time).toBeGreaterThan(braking.sim_time + 1.5);
  await step(page, 2, { pad: ['LEFT'] });
  expect((await read(page)).driving.steer).toBe(1);
  await step(page, 2, { pad: ['RIGHT'] });
  expect((await read(page)).driving.steer).toBe(-1);
  await step(page, 2, { pad: [OUTER] });
  const boost = await read(page);
  expect(boost.driving.nozzles).toEqual([1, 1, 1, 1, 0, 0, 0, 0, 0, 0]);
  expect(boost.forces.slice(0, 4).every(force => force > 0)).toBe(true);
  await step(page, 2, { pad: [INNER] });
  const lift = await read(page);
  expect(lift.driving.nozzles.slice(0, 4)).toEqual([0, 0, 0, 0]);
  expect(lift.driving.nozzles.slice(8)).toEqual([0, 0]);
  // Held lift now asks for a physical ascent speed, so these four symmetric
  // duties vary with height and vertical speed instead of staying fully open.
  const liftDuty = lift.driving.nozzles[4];
  expect(liftDuty).toBeGreaterThan(0);
  expect(liftDuty).toBeLessThanOrEqual(1);
  expect(lift.driving.nozzles.slice(4, 8)).toEqual(Array(4).fill(liftDuty));
  expect(lift.forces.slice(4, 8).every(force => force > 0)).toBe(true);
  await step(page, 2, { pad: [OUTER, INNER] });
  const combined = await read(page);
  expect(combined.driving.nozzles.slice(0, 4)).toEqual([1, 1, 1, 1]);
  expect(combined.driving.nozzles.slice(8)).toEqual([0, 0]);
  const combinedLiftDuty = combined.driving.nozzles[4];
  expect(combinedLiftDuty).toBeGreaterThan(0);
  expect(combinedLiftDuty).toBeLessThanOrEqual(1);
  expect(combined.driving.nozzles.slice(4, 8)).toEqual(Array(4).fill(combinedLiftDuty));
  expect(combined.forces.slice(0, 8).every(force => force > 0)).toBe(true);
  expect(combined.trick).toBe('');
  await record('drive-boost-lift', page, { driving, braking, reverse, boost, lift, combined });
});

test('outer SELECT confirms, inner BACK closes options, and held pads cannot leak thrust', async ({ page }) => {
  await calibrate(page);
  await step(page, 18, { pad: ['UP'] });
  const before = await read(page);
  await tap(page, 'MENU');
  expect((await read(page)).menu_open).toBe(true);
  await step(page, 12);
  const open = await read(page);
  expect(open.sim_time).toBeGreaterThan(before.sim_time + .3);
  expect(open.physics[2]).toBeGreaterThan(before.physics[2]);
  expect(open.driving.nozzles.every(value => value === 0)).toBe(true);
  await record('options-live-physics', page, { before, open });
  await tap(page, 'DOWN'); // Reset car is first; select Fan explicitly.
  // Holding the confirmation pad after
  // closing options must not unexpectedly start its normal rear-boost action.
  await step(page, 2, { pad: [OUTER] });
  await step(page, 10, { pad: [OUTER] });
  const guarded = await read(page);
  expect(guarded.menu_open).toBe(false);
  expect(guarded.release_pads).toBe(true);
  expect(guarded.driving.fan).toBe(1);
  expect(guarded.driving.nozzles.every(value => value === 0)).toBe(true);
  await step(page, 2);
  await step(page, 3, { pad: [OUTER, INNER] });
  const resumed = await read(page);
  expect(resumed.release_pads).toBe(false);
  expect(resumed.driving.fan).toBe(1);
  expect(resumed.driving.nozzles.slice(0, 8)).toEqual(Array(8).fill(1));
  expect(resumed.forces.slice(0, 8).every(value => value > 0)).toBe(true);
  expect(resumed.trick).toBe('');
  await step(page, 2);
  await tap(page, 'MENU');
  await step(page, 2, { pad: [INNER] });
  await step(page, 10, { pad: [INNER] });
  const dismissed = await read(page);
  expect(dismissed.menu_open).toBe(false);
  expect(dismissed.release_pads).toBe(true);
  expect(dismissed.driving.fan).toBe(1);
  expect(dismissed.driving.nozzles.every(value => value === 0)).toBe(true);
  await step(page, 2);
  await step(page, 2, { pad: [INNER] });
  expect((await read(page)).driving.nozzles.slice(4, 8)).toEqual(Array(4).fill(1));
  await record('fan-and-guarded-thrust', page, { guarded, resumed, dismissed });
});

test('four continuous tilt directions fire physical roll and pitch jets without invoking presets', async ({ page }) => {
  const records = [];
  for (const [direction, axis, sign] of [
    ['right', 'x', 1], ['left', 'x', -1], ['up', 'y', 1], ['down', 'y', -1],
  ]) {
    await action(page, 'reset');
    await calibrate(page);
    await action(page, 'motion', 'tilt');
    await step(page, 12, { pad: [INNER] });
    const rates = (axis === 'x' ? [20, 40, 80, 150, 250] : [20, 40, 60, 100, 150]).map(rate => rate * sign);
    const stroke = await angularSamples(page, axis, rates);
    if (axis === 'y') await step(page, 6, { pad: [INNER], imu: stroke.restingImu });
    const snapshot = await read(page);
    expect(snapshot.motion.model.channels[direction]).toBeGreaterThan(.1);
    expect(snapshot.trick).toBe('');
    const duties = snapshot.driving.nozzles;
    const bodyRates = await page.evaluate(() => badge.inspect(
      "__import__('physics').q_inverse_rotate(sim.q, sim.omega)"));
    if (axis === 'x') {
      expect((duties[4] + duties[6] - duties[5] - duties[7]) * sign).toBeGreaterThan(0);
      expect(bodyRates[2] * sign).toBeGreaterThan(.05);
      expect(duties[sign > 0 ? 8 : 9]).toBeGreaterThan(0);
    } else {
      expect((duties[4] + duties[5] - duties[6] - duties[7]) * sign).toBeGreaterThan(0);
      expect(bodyRates[0] * -sign).toBeGreaterThan(.05);
    }
    records.push({ direction, stroke, snapshot, bodyRates });
    await record(`motion-${direction}`, page, records.at(-1));
  }
  await record('four-motion-directions', page, { records });
});

test('flick-only mode replaces a short downward preparation with the upward stroke before pitch jets fire', async ({ page }) => {
  await calibrate(page);
  await action(page, 'motion', 'flick');
  await step(page, 12, { pad: [INNER] });
  const before = await read(page);
  expect(before.motion.model.mode).toBe('flick');
  const stroke = await angularSamples(page, 'y', [0, -210, -240, -220, 0, 200, 320, 300, 0]);
  for (let i = 0; i < 4; i++) {
    await step(page, 1, { pad: [INNER], imu: stroke.restingImu });
    stroke.samples.push({ rate: 0, snapshot: await read(page) });
  }
  const pending = stroke.samples.filter(sample => sample.snapshot.motion.model.flick_pending);
  expect(pending.some(sample => sample.snapshot.motion.model.pending_flick_y < 0)).toBe(true);
  expect(pending.some(sample => sample.snapshot.motion.model.pending_flick_y > 0)).toBe(true);
  for (const { snapshot } of stroke.samples) {
    expect(snapshot.motion.model.channels.down).toBe(0);
    // Front lift pair gives nose-up; the rear pair must never dominate during
    // this accepted reversal. Neutral inner BACK still supplies symmetric lift.
    const duties = snapshot.driving.nozzles;
    expect(duties[4] + duties[5]).toBeGreaterThanOrEqual(duties[6] + duties[7]);
    expect(snapshot.motion.model.flick_pending ? snapshot.trick === '' : ['', 'backflip'].includes(snapshot.trick)).toBe(true);
  }
  expect((await read(page)).trick).toBe('backflip');
  const upward = stroke.samples.find(sample => sample.snapshot.motion.model.channels.up > .3);
  expect(upward).toBeDefined();
  expect(upward.snapshot.motion.model.event_count).toBe(before.motion.model.event_count + 1);
  expect(upward.snapshot.motion.model.last_flick.y).toBeGreaterThan(.5);
  await record('down-preparation-up-stroke', page, { before, stroke, upward });
});

test('limited air depletes physically and outer SELECT refills without resetting the car', async ({ page }) => {
  test.setTimeout(120000);
  await action(page, 'air', 'limited');
  await calibrate(page);
  const jumps = [];
  // Governing the ascent consumes less air. Repeat real jumps until the
  // remaining pressure cannot support the car, rather than assuming exactly
  // two full-power launches must deplete the tank to a fixed fraction.
  for (let i = 0; i < 4; i++) {
    await step(page, 50, { pad: [INNER] });
    const ascent = await read(page);
    // The first launch must be strong. Later launches may lose lifting
    // power during the hold as the limited tank crosses its weight threshold.
    if (i === 0) expect(ascent.physics[1]).toBeGreaterThan(2);
    else expect(ascent.physics[1] > .6 || ascent.lift_status.ratio < 1).toBe(true);
    // Keep the original eight seconds inside the badge catch-up budget.
    await step(page, 160, { dt: 50 });
    const landing = await read(page);
    expect(landing.physics[1]).toBeLessThan(.6);
    expect(landing.lift_status.up).toBeGreaterThan(.99);
    if (ascent.physics[1] > .6) {
      expect(landing.impact_refinements).toBeGreaterThan(ascent.impact_refinements);
    } else expect(landing.impact_refinements).toBeGreaterThanOrEqual(ascent.impact_refinements);
    expect(await page.evaluate(() => badge.inspect('sim.contact_count'))).toBe(4);
    jumps.push({ ascent, landing });
    if (landing.lift_status.ratio < 1) break;
  }
  expect(jumps.length).toBeGreaterThanOrEqual(1);
  expect(jumps.at(-1).landing.lift_status.ratio).toBeLessThan(1);
  // A pressure regulator maintains rated thrust until the reserve is nearly
  // spent. Verify actual supply loss rather than an old tank percentage.
  expect(jumps.at(-1).landing.physics[4]).toBeGreaterThanOrEqual(0);
  expect(jumps.at(-1).landing.physics[4]).toBeLessThan(jumps[0].ascent.physics[4]);
  await step(page, 16, { pad: [INNER] });
  const weak = await read(page);
  const status = await page.evaluate(() => badge.inspect('control_status()'));
  expect(weak.air_mode).toBe('limited');
  expect(weak.lift_status.reason).toBe('low_pressure');
  expect(weak.lift_status.ratio).toBeLessThan(1);
  expect(weak.physics[1]).toBeLessThan(1);
  expect(weak.physics[4]).toBeGreaterThanOrEqual(0);
  expect(weak.physics[4]).toBeLessThanOrEqual(jumps.at(-1).landing.physics[4]);
  expect(weak.forces.slice(4, 8).every(force => force >= 0)).toBe(true);
  expect(await page.evaluate(() => badge.inspect(
    "sum(sim.nozzle_forces[4:8]) < sim.mass*__import__('physics').GRAVITY"))).toBe(true);
  expect(weak.hud_hint).toMatch(/LOW PRESSURE/);
  expect(weak.hud_hint).toMatch(/REFILL/);
  expect(status.message).toMatch(/refill/i);
  await record('limited-air-warning', page, { jumps, weak, status,
    instrumentation: 'None: every jump and landing uses normal controls and unmodified physics state; the bounded sequence stops once pressure cannot support the car.' });
  await step(page, 2);
  await tap(page, 'MENU');
  await tap(page, 'DOWN');
  await tap(page, 'DOWN');
  const beforeRefill = await read(page);
  expect(beforeRefill.menu_index).toBe(2);
  await step(page, 2, { pad: [OUTER] });
  await step(page, 8, { pad: [OUTER] });
  const refilled = await read(page);
  expect(refilled.menu_open).toBe(false);
  expect(refilled.release_pads).toBe(true);
  expect(refilled.physics[4]).toBe(1);
  expect(refilled.lift_status.reason).toBe('ready');
  expect(refilled.sim_time).toBeGreaterThan(beforeRefill.sim_time);
  // Physics keeps advancing during the confirmation frames. It may continue
  // coasting, but cannot teleport back to the flat scene's starting position.
  expect(Math.abs(refilled.physics[0] - beforeRefill.physics[0])).toBeLessThan(.5);
  expect(Math.abs(refilled.physics[2] - beforeRefill.physics[2])).toBeLessThan(.5);
  expect(refilled.driving.nozzles.every(value => value === 0)).toBe(true);
  await step(page, 2);
  await step(page, 32, { pad: [INNER] });
  const relaunched = await read(page);
  expect(relaunched.physics[1]).toBeGreaterThan(2);
  expect(relaunched.physics[1]).toBeLessThan(5);
  expect(await page.evaluate(() => badge.inspect('sim.airborne and sim.vy > 2.'))).toBe(true);
  expect(relaunched.forces.slice(4, 8).every(force => force > 0)).toBe(true);
  expect(relaunched.sim_time).toBeGreaterThan(refilled.sim_time + .7);
  expect(relaunched.trick).toBe('');
  await record('refill-and-relaunch', page, { beforeRefill, refilled, relaunched });
});

test('outer SELECT enables unlimited air in options and repeated hops retain governed lift', async ({ page }) => {
  test.setTimeout(120000);
  await action(page, 'air', 'limited');
  await calibrate(page);
  await tap(page, 'MENU');
  await tap(page, 'UP'); // Wrap from Reset car to Controls.
  await tap(page, 'UP'); // Air supply is immediately above Controls.
  const menu = await read(page);
  expect(menu.menu_index).toBe(7);
  expect(await page.evaluate(() => badge.inspect('MENU_ITEMS'))).toHaveLength(9);
  await record('air-supply-options', page, { menu });
  await step(page, 2, { pad: [OUTER] });
  await step(page, 8, { pad: [OUTER] });
  const enabled = await read(page);
  expect(enabled.air_mode).toBe('unlimited');
  expect(enabled.menu_open).toBe(false);
  expect(enabled.release_pads).toBe(true);
  expect(enabled.driving.nozzles.every(value => value === 0)).toBe(true);
  await step(page, 2);
  const hops = [];
  for (let i = 0; i < 3; i++) {
    await step(page, 48, { pad: [INNER] });
    const airborne = await read(page);
    const [ground, maxClimb] = await page.evaluate(() => badge.inspect("[GROUND_Y, __import__('badge_controls').LIFT_MAX_SPEED]"));
    // 1.2 seconds of held lift now climbs almost twice as quickly, while
    // staying below the speed-limited distance from the resting pose.
    expect(airborne.physics[1] - ground).toBeGreaterThan(5);
    expect(airborne.physics[1] - ground).toBeLessThan(1.2 * maxClimb);
    expect(airborne.physics[4]).toBe(1);
    expect(await page.evaluate(() => badge.inspect('sim.airborne and sim.vy > 2.'))).toBe(true);
    const liftForce = airborne.forces[4];
    expect(liftForce).toBeGreaterThan(3000);
    const ratedForce = await page.evaluate(() => badge.inspect(
      "__import__('physics').NOZZLES[4][3]"));
    expect(liftForce).toBeLessThan(ratedForce);
    expect(airborne.forces.slice(4, 8)).toEqual(Array(4).fill(liftForce));
    // Preserve ten simulated seconds; overloaded100ms polls drop elapsed time.
    await step(page, 200, { dt: 50 });
    const landed = await read(page);
    expect(landed.physics[1]).toBeLessThan(.6);
    expect(landed.physics[4]).toBe(1);
    expect(landed.lift_status.reason).toBe('ready');
    expect(landed.lift_status.up).toBeGreaterThan(.99);
    expect(landed.impact_refinements).toBeGreaterThan(airborne.impact_refinements);
    expect(await page.evaluate(() => badge.inspect('sim.contact_count'))).toBe(4);
    expect(landed.sim_time).toBeGreaterThan(airborne.sim_time + 9);
    expect(landed.trick).toBe('');
    hops.push({ airborne, landed });
  }
  const launchHeights = hops.map(hop => hop.airborne.physics[1]);
  expect(Math.max(...launchHeights) - Math.min(...launchHeights)).toBeLessThan(.3);
  await record('unlimited-repeat-hops', page, { menu, enabled, hops });
  await tap(page, 'MENU');
  await tap(page, 'UP');
  await tap(page, 'UP'); // Reopening starts on Reset car; choose Air supply again.
  await tap(page, OUTER);
  const limited = await read(page);
  expect(limited.air_mode).toBe('limited');
  expect(limited.sim_time).toBeGreaterThan(hops.at(-1).landed.sim_time);
  expect(limited.physics[4]).toBe(1);
  await record('air-supply-toggle-back', page, { limited });
});

test('unlimited air retains fan suction and body-relative lift with visible physical warnings', async ({ page }) => {
  await calibrate(page);
  await action(page, 'air', 'unlimited');
  const ratedLift = await page.evaluate(() => badge.inspect(
    "[nozzle[3] for nozzle in __import__('physics').NOZZLES[4:8]]"));
  await tap(page, 'MENU');
  await tap(page, 'DOWN');
  await tap(page, OUTER); // Fan on.
  await step(page, 80);
  await step(page, 12, { pad: [INNER] });
  const fanHeld = await read(page);
  expect(fanHeld.air_mode).toBe('unlimited');
  expect(fanHeld.physics[4]).toBe(1);
  expect(fanHeld.forces.slice(4, 8)).toEqual(ratedLift);
  expect(fanHeld.physics[1]).toBeLessThan(.6);
  expect(fanHeld.lift_status.reason).toBe('fan');
  expect(fanHeld.hud_hint).toMatch(/FAN HOLDS CAR/);
  await record('unlimited-fan-warning', page, { fanHeld });
  await step(page, 2);
  await tap(page, 'MENU');
  await tap(page, 'DOWN');
  await tap(page, OUTER); // Fan off; let residual suction decay physically.
  await step(page, 24);
  // The calibrated badge deliberately levels a centered airborne car through
  // differential jets. First verify that production path, then use the public
  // individual-nozzle controls to isolate unassisted body-relative lift.
  const invert = () => page.evaluate(() => badge.exec('import u26_runtime\n_s = u26_runtime.app.sim\n_s.q = (0., 0., 0., 1.)\n_s.y = 3.\n_s.vx = _s.vy = _s.vz = 0.\n_s.omega = [0., 0., 0.]'));
  await invert();
  await step(page, 3, { pad: [INNER] });
  const assisted = await read(page);
  const assistedRotation = await page.evaluate(() => badge.inspect('rotation_control.snapshot()'));
  expect(assistedRotation.level_state).toBe('level');
  expect(assistedRotation.level_strength).toBeGreaterThan(0);
  expect(assisted.forces.slice(4, 8).some((force, i) => force < ratedLift[i])).toBe(true);
  expect(assisted.physics[5].some(rate => Math.abs(rate) > .01)).toBe(true);
  expect(assisted.physics[1]).toBeLessThan(3);
  await record('unlimited-inverted-leveling', page, { assisted, assistedRotation });
  await step(page, 2);
  await invert();
  // Raw nozzle input is an actual laptop control: it suppresses center assist
  // and commands all four lift jets equally, without modifying the controller.
  await step(page, 3, { appControls: { nozzles: [0, 0, 0, 0, 1, 1, 1, 1, 0, 0] } });
  const inverted = await read(page);
  const rawRotation = await page.evaluate(() => badge.inspect('rotation_control.snapshot()'));
  expect(rawRotation.level_strength).toBe(0);
  expect(inverted.air_mode).toBe('unlimited');
  expect(inverted.physics[4]).toBe(1);
  expect(inverted.forces.slice(4, 8)).toEqual(ratedLift);
  expect(inverted.physics[1]).toBeLessThan(3);
  expect(inverted.physics[5].every(rate => Math.abs(rate) < 1e-8)).toBe(true);
  expect(inverted.lift_status.reason).toBe('orientation');
  expect(inverted.hud_hint).toMatch(/LIFT POINTS WITH CAR/);
  expect(inverted.trick).toBe('');
  await record('unlimited-inverted-warning', page, { inverted, rawRotation,
    instrumentation: 'Set q upside down, y=3 m, linear and angular velocity zero. Normal badge lift first demonstrates intentional center assist; the public raw-nozzle input then isolates body-relative force. No controller flags, contact flags or thrust physics modified.' });
});
