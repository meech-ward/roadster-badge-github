import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const evidence = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/sandbox-physics');
async function record(name, data) {
  await mkdir(evidence, { recursive: true });
  await writeFile(`${evidence}/${name}.json`, JSON.stringify(data, null, 2) + '\n');
}
async function picture(page, name) {
  await mkdir(evidence, { recursive: true });
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${evidence}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}
test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
});

test('sandbox is native 3D and keeps simulating after roof contact', async ({ page }) => {
  await picture(page, 'loop-ready');
  const counts = await page.evaluate(() => badge.inspect('[scene.TRIANGLE_COUNT, scene.MAX_SCENE_TRIANGLES]'));
  expect(counts[0]).toBeGreaterThan(1500);
  expect(counts[0]).toBeLessThanOrEqual(2400);
  expect(counts[1]).toBeLessThanOrEqual(8000);
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.evaluate(() => badge.exec('u26_runtime.app.sim.y = 1.2\nu26_runtime.app.sim.q = (0.0, 0.0, 0.0, 1.0)'));
  await page.evaluate(() => badge.step(100));
  const roof = await page.evaluate(() => badge.inspect('[sim.time, sim.y, sim.q, sim.contact_count]'));
  expect(roof[0]).toBeGreaterThan(3);
  expect(roof[1]).toBeGreaterThan(0.1);
  expect(roof[1]).toBeLessThan(1);
  expect(1 - 2 * (roof[2][1] ** 2 + roof[2][3] ** 2)).toBeLessThan(-0.5);
  await picture(page, 'roof-contact');
  await page.evaluate(() => badge.step(30));
  const later = await page.evaluate(() => badge.inspect('[sim.time, sim.y, sim.q]'));
  expect(later[0]).toBeGreaterThan(roof[0] + .9);
  expect(later[1]).toBeGreaterThan(.1);
  await record('roof-contact', { counts, roof, later });
});

test('all lift nozzles launch; a side pair produces physical roll', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.evaluate(() => badge.step(24, { appControls: { nozzles: [0,0,0,0,1,1,1,1,0,0] } }));
  const jump = await page.evaluate(() => badge.inspect('[sim.y, sim.vy, sim.air_fraction, sim.nozzle_forces, sim.q]'));
  expect(jump[0]).toBeGreaterThan(1);
  expect(jump[1]).toBeGreaterThan(0);
  expect(jump[2]).toBeLessThan(1);
  expect(jump[3].slice(4,8).every(x => x > 0)).toBe(true);
  await picture(page, 'manual-lift');
  await page.evaluate(() => badge.step(16, { appControls: { nozzles: [0,0,0,0,1,0,1,0,0,0] } }));
  const roll = await page.evaluate(() => badge.inspect('[sim.y, sim.omega, sim.q, sim.air_fraction]'));
  expect(Math.abs(roll[1][2])).toBeGreaterThan(.3);
  expect(Math.abs(roll[2][3])).toBeGreaterThan(.05);
  await picture(page, 'manual-roll');
  await record('lift-roll', { jump, roll });
});

test('fan holds near the inverted loop surface and releasing it lets gravity act', async ({ page }) => {
  // Historical regression fixture: Full Loop is no longer a selectable scene.
  await page.evaluate(() => badge.exec('u26_runtime.app.reset_sim("loop")'));
  // The fan must spool up before relying on it, just as when driving up a loop.
  await page.evaluate(() => badge.step(60, { appControls: { fan: 1 } }));
  await page.evaluate(() => badge.exec('from physics import LOOP_RADIUS, LOOP_Z\ns = u26_runtime.app.sim\ns.y = 2 * LOOP_RADIUS - .5\ns.z = LOOP_Z\ns.q = (0.0, 1.0, 0.0, 0.0)'));
  await page.evaluate(() => badge.step(70, { appControls: { fan: 1 } }));
  const held = await page.evaluate(() => badge.inspect('[sim.y, sim.z, sim.fan_force, sim.contact_count, sim.time]'));
  expect(held[0]).toBeGreaterThan(14.5);
  expect(held[2]).toBeGreaterThan(15000);
  await picture(page, 'fan-inverted');
  await page.evaluate(() => badge.step(50, { appControls: { fan: 0 } }));
  const released = await page.evaluate(() => badge.inspect('[sim.y, sim.vy, sim.fan_force, sim.time]'));
  expect(released[0]).toBeLessThan(held[0] - 1);
  expect(released[1]).toBeLessThan(0);
  expect(released[3]).toBeGreaterThan(held[4] + 1);
  await record('inverted-fan', { held, released });
});

test('continuous physics is deterministic across render schedules in MicroPython', async ({ page }) => {
  const results = [];
  for (const fps of [15,30,60]) {
    await page.evaluate(fps => badge.exec(`from physics import Simulation\ns = Simulation('flat')\nfor i in range(${fps * 2}):\n    s.step(1/${fps}, {'throttle': .6, 'steer': .1, 'fan': 1})\nu26_runtime.app.schedule = [s.time,s.x,s.y,s.z,s.vx,s.vy,s.vz,s.q,s.air_fraction]`), fps);
    results.push(await page.evaluate(() => badge.inspect('schedule')));
  }
  const flattened = results.map(row => row.flat());
  const maxError = Math.max(...flattened.flatMap(row => row.map((v,i) => Math.abs(v - flattened[2][i]))));
  expect(maxError).toBeLessThan(.001);
  for (const row of results) expect(Math.abs(row[7].reduce((n,v) => n + v*v,0) - 1)).toBeLessThan(.00001);
  await record('render-schedules', { fps: [15,30,60], results, tolerance: .001, maxError });
});

test('real W and F keyboard inputs drive complete loops without a scripted path', async ({ page }) => {
  // Historical regression fixture: Full Loop is no longer a selectable scene.
  await page.evaluate(() => badge.exec('u26_runtime.app.reset_sim("loop")'));
  await page.keyboard.press('f');
  await page.keyboard.down('w');
  const samples = [];
  let previous = 0;
  let rotation = 0;
  let inverted = false;
  for (let i = 0; i < 65; i++) {
    await page.evaluate(() => badge.step(4));
    const row = await page.evaluate(() => badge.inspect('[sim.time,sim.x,sim.y,sim.z,sim.speed,sim.basis()[1][1],sim.contact_count,sim.air_fraction]'));
    const angle = Math.atan2(row[3] - 10, 8 - row[2]);
    rotation += Math.atan2(Math.sin(angle - previous), Math.cos(angle - previous));
    previous = angle;
    samples.push(row);
    if (!inverted && row[5] < -.6 && row[6] >= 2) {
      inverted = true;
      await picture(page, 'keyboard-loop-inverted');
    }
  }
  await page.keyboard.up('w');
  expect(inverted).toBe(true);
  expect(rotation).toBeGreaterThan(4 * Math.PI);
  expect(samples.every(row => row[7] === 1)).toBe(true);
  expect(samples.at(-1)[0]).toBeGreaterThan(8);
  await record('keyboard-loops', { samples, turns: rotation / (2 * Math.PI), inverted });
});

test('open ramp keeps a centered approach view through alignment and climbing', async ({ page }) => {
  await page.locator('#roadster-scene').selectOption('ramp');
  await page.evaluate(() => badge.step(2));
  await picture(page, 'ramp-centered-start');
  const alignments = [];
  for (const x of [-4, 0, 4]) {
    await page.evaluate(x => badge.exec(`s=u26_runtime.app.sim\ns.x=${x}\ns.z=-3`), x);
    await page.evaluate(() => badge.step(2));
    const row = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,scene.camera_yaw,scene.camera_view]'));
    expect(row[0]).toBeCloseTo(x, 3);
    expect(row[3]).toBeCloseTo(0, 5);
    expect(row[4]).toBe('behind');
    alignments.push(row);
    await picture(page, `ramp-alignment-${x === 0 ? 'center' : x < 0 ? 'right' : 'left'}`);
  }
  await page.keyboard.press('r');
  await page.keyboard.press('f');
  await page.keyboard.down('w');
  const samples = [];
  let climbing = false;
  for (let i = 0; i < 50; i++) {
    await page.evaluate(() => badge.step(2));
    const row = await page.evaluate(() => badge.inspect('[sim.time,sim.x,sim.y,sim.z,sim.contact_count,scene.camera_yaw]'));
    samples.push(row);
    // Chassis contacts introduce a small physical drift through the curl;
    // the camera follows it without the old large sideways transition.
    expect(Math.abs(row[1])).toBeLessThan(.25);
    expect(Math.abs(row[5])).toBeLessThan(.01);
    if (!climbing && row[2] > 3 && row[4] >= 2) {
      climbing = true;
      await picture(page, 'ramp-centered-climb');
    }
  }
  await page.keyboard.up('w');
  expect(climbing).toBe(true);
  await record('ramp-centered-camera', { alignments, samples, climbing });
});
