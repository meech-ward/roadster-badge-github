import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/tesla-collection');
const slugs = ['roadster', 'original_roadster', 'model_s', 'model_3', 'model_x',
  'model_y', 'model_y_l', 'cybertruck', 'semi', 'cybercab', 'robovan'];

async function record(name, data) {
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify(data, null, 2) + '\n');
}

async function picture(page, name) {
  await mkdir(output, { recursive: true });
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
  return png;
}

const state = page => page.evaluate(() => badge.inspect(
  '[sim.time,sim.x,sim.y,sim.z,sim.vx,sim.vy,sim.vz,sim.q,sim.air_fraction,sim.contact_count,sim.chassis_contacts,sim.surface]'));

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
});

test('eleven Tesla models render through native pico3d with bounded detail caching', async ({ page }) => {
  const initial = await page.evaluate(() => badge.inspect('control_status()'));
  expect(initial.scene).toBe('collection');
  expect(initial.parked_count).toBe(10);
  expect(await page.locator('#roadster-vehicle option').evaluateAll(options => options.map(option => option.value))).toEqual(slugs);
  const rows = [];
  const images = new Set();
  for (const slug of slugs) {
    const action = await page.evaluate(slug => badge.appAction('inspect', slug), slug);
    expect(action.inspecting).toBe(slug);
    await page.evaluate(() => badge.step(2));
    const detail = await page.evaluate(() => badge.inspect(
      '[scene.triangle_count,scene.MAX_SCENE_TRIANGLES,scene.fleet_scene.submitted_triangles,list(scene.fleet_scene.high_models),list(scene.fleet_scene.low_models),list(scene.fleet_scene.visible_ids),scene.TRIANGLE_COUNT,scene.VERTEX_COUNT]'));
    const colors = await page.locator('canvas').evaluate(canvas => {
      const bytes = canvas.getContext('2d').getImageData(0, 36, 320, 178).data;
      return new Set(Array.from({ length: bytes.length / 4 }, (_, i) =>
        `${bytes[i*4]},${bytes[i*4+1]},${bytes[i*4+2]}`)).size;
    });
    expect(detail[0]).toBeGreaterThan(50);
    expect(detail[0]).toBeLessThanOrEqual(detail[1]);
    expect(detail[1]).toBeLessThanOrEqual(8000);
    expect(detail[2]).toBeLessThanOrEqual(5000);
    expect(detail[3].length).toBeLessThanOrEqual(2);
    expect(detail[4]).toHaveLength(10);
    if (slug !== 'roadster') {
      expect(detail[3]).toContain(slug);
      expect(detail[5]).toContain(slug);
    }
    expect(colors).toBeGreaterThan(10);
    images.add(await picture(page, `${slug}-three-quarter`));
    rows.push({ slug, renderedTriangles: detail[0], sceneCapacity: detail[1],
      fleetSubmittedTriangles: detail[2], highDetailCache: detail[3],
      residentSilhouettes: detail[4], visibleVehicles: detail[5],
      roadsterTriangles: detail[6], roadsterVertices: detail[7], colors });
  }
  expect(images.size).toBe(slugs.length);
  await page.evaluate(() => badge.appAction('inspect', 'roadster'));
  const views = [];
  for (const view of ['front', 'side', 'rear']) {
    await page.evaluate(view => badge.appAction('camera', view), view);
    await page.evaluate(() => badge.step(2));
    views.push(await picture(page, `roadster-${view}`));
  }
  expect(new Set(views).size).toBe(3);
  await record('native-model-catalog', { runtime: 'MicroPython WASM / native pico3d', rows,
    roadsterViews: ['front', 'side', 'rear'], hardwareTested: false });
});

test('inspection buttons retain physics, visit positions Roadster and driving restores chase view', async ({ page }) => {
  await page.locator('#roadster-vehicle').selectOption('semi');
  await page.locator('#roadster-inspect').click();
  await page.evaluate(() => badge.step(2));
  let status = await page.evaluate(() => badge.inspect('control_status()'));
  expect(status.inspecting).toBe('semi');
  expect(status.camera).toBe('orbit');
  const before = await state(page);
  await page.evaluate(() => badge.exec('s=u26_runtime.app.sim\nf=s.basis()[2]\ns.vx,s.vz=f[0]*3,f[2]*3'));
  await page.evaluate(() => badge.step(10));
  const movingWhileInspecting = await state(page);
  expect(movingWhileInspecting[0]).toBeGreaterThan(before[0]);
  expect(Math.hypot(movingWhileInspecting[1]-before[1], movingWhileInspecting[3]-before[3])).toBeGreaterThan(.3);
  for (const view of ['front', 'side']) {
    await page.locator('#roadster-camera').selectOption(view);
    await page.evaluate(() => badge.step(2));
    status = await page.evaluate(() => badge.inspect('control_status()'));
    expect(status.camera).toBe(view);
    expect(status.inspecting).toBe('semi');
  }
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/semi-inspection-controls.png`, fullPage: true });
  await page.locator('#roadster-vehicle').selectOption('cybertruck');
  await page.locator('#roadster-visit').click();
  await page.evaluate(() => badge.step(2));
  const visited = await state(page);
  const parked = await page.evaluate(() => badge.inspect('find_vehicle("cybertruck")'));
  expect(visited[1]).toBe(parked.x);
  expect(visited[3]).toBeCloseTo(parked.z-parked.length/2-9, 3);
  expect(visited[8]).toBe(1);
  await page.locator('#roadster-inspect').click();
  await page.evaluate(() => badge.step(2));
  await page.keyboard.down('w');
  const driving = await page.evaluate(() => badge.step(12));
  await page.keyboard.up('w');
  expect(driving.controlStatus.camera).toBe('chase');
  expect(driving.controlStatus.inspecting).toBe('');
  expect((await state(page))[3]).toBeGreaterThan(visited[3] + .4);
  await picture(page, 'cybertruck-approach');
  await page.keyboard.down('w');
  await page.keyboard.down('j');
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(2));
  await page.locator('#roadster-reset').click();
  const reset = await page.evaluate(() => badge.step(2));
  expect(reset.controlStatus.scene).toBe('collection');
  expect(reset.controlStatus.parked_count).toBe(10);
  expect(await page.evaluate(() => badge.inspect('controls'))).toEqual({
    throttle: 0, brake: 0, steer: 0, fan: false, nozzles: Array(10).fill(0) });
  expect(await page.evaluate(() => badge.inspect('len(sim.obstacles)'))).toBeGreaterThan(0);
  await page.keyboard.up('w');
  await page.keyboard.up('j');
  await page.locator('#roadster-scene').selectOption('ramp');
  const ramp = await page.evaluate(() => badge.step(2));
  expect(ramp.controlStatus.parked_count).toBe(0);
  expect(await page.evaluate(() => badge.inspect('[sim.scene_name,len(sim.obstacles)]'))).toEqual(['ramp', 0]);
  await record('native-collection-controls', { before, movingWhileInspecting, visited,
    parked, driving: driving.controlStatus, reset: reset.controlStatus, ramp: ramp.controlStatus });
});

test('actual parked Robovan roof supports landing and J lifts again without resetting', async ({ page }) => {
  await page.evaluate(() => badge.exec(
    'from physics import GROUND_Y\n' +
    'a = u26_runtime.app\nv = a.find_vehicle("robovan")\n' +
    'roof = max(v["collision_boxes"], key=lambda b: b[1]+b[4])\n' +
    'a.roof_test = [v["x"]+roof[0],roof[1]+roof[4],v["z"]+roof[2]]\n' +
    'a.sim.x,a.sim.y,a.sim.z = a.roof_test[0],a.roof_test[1]+4,a.roof_test[2]\n' +
    'a.sim.q = (1.,0.,0.,0.)'));
  await page.evaluate(() => badge.step(105));
  const roof = await page.evaluate(() => badge.inspect('roof_test'));
  const landed = await state(page);
  expect(landed[9]).toBe(4);
  const rideHeight = await page.evaluate(() => badge.inspect('__import__("physics").GROUND_Y'));
  expect(landed[2]).toBeCloseTo(roof[1] + rideHeight, 2);
  expect(Math.abs(landed[5])).toBeLessThan(.02);
  expect(landed[11]).toBe('vehicle:robovan');
  expect(landed[8]).toBe(1);
  await picture(page, 'robovan-roof-landing');
  await page.keyboard.down('j');
  await page.evaluate(() => badge.step(18));
  await page.keyboard.up('j');
  const lifted = await state(page);
  expect(lifted[2]).toBeGreaterThan(landed[2] + 2);
  expect(lifted[5]).toBeGreaterThan(6);
  expect(lifted[0]).toBeGreaterThan(landed[0] + .5);
  expect(lifted[8]).toBeLessThan(.9);
  expect(lifted[9]).toBe(0);
  await picture(page, 'robovan-roof-relift');
  await record('native-roof-landing', { roof, landed, lifted });
});

test('the side-by-side lineup can be jumped with driving and a kickflip from reset', async ({ page }) => {
  // Match the badge's unlimited practice supply while testing scene clearance.
  await page.evaluate(() => badge.appAction('air', 'unlimited'));
  const parked = await page.evaluate(() => badge.inspect('list(PARKED)'));
  const initial = await state(page);
  const forward = await page.evaluate(() => badge.inspect('sim.basis()[2]'));
  expect(parked).toHaveLength(10);
  expect(new Set(parked.map(v => v.z)).size).toBe(1);
  expect(parked.every(v => v.yaw === 0)).toBe(true);
  for (let i = 1; i < parked.length; i++) {
    expect(parked[i].x-parked[i].width/2).toBeGreaterThan(parked[i-1].x+parked[i-1].width/2);
    expect(parked[i].height).toBeGreaterThanOrEqual(parked[i-1].height);
  }
  expect(initial[1]).toBeLessThan(parked[0].x-12);
  expect(forward[0]).toBeGreaterThan(.99);
  await picture(page, 'jump-lineup-start');
  await page.keyboard.down('w');
  await page.keyboard.down('b');
  // Use the visible takeoff stripe, not a frame count tied to old boost tuning.
  const takeoffX = parked[0].x - parked[0].width / 2 - 11;
  for (let frame = 0; frame < 90; frame++) {
    await page.evaluate(() => badge.step(1));
    if ((await state(page))[1] >= takeoffX) break;
  }
  const takeoff = await state(page);
  expect(takeoff[1]).toBeGreaterThanOrEqual(takeoffX);
  expect(takeoff[1]).toBeLessThan(takeoffX + 1.);
  await page.keyboard.up('b');
  await page.keyboard.press('k');
  const samples = [], cleared = [];
  let seenAir = false, landed = null;
  for (let i = 0; i < 90; i++) {
    await page.evaluate(() => badge.step(2));
    const row = await state(page);
    samples.push(row);
    seenAir ||= row[9] === 0;
    // Crossing the lineup must be clear. Once beyond it, a real touchdown
    // may compress the suspension enough to brush the floor with the hull.
    if (cleared.length < parked.length) expect(row[10]).toBe(0);
    else if (row[10]) expect(row[11]).toBe('ground');
    for (const vehicle of parked) {
      if (row[1] >= vehicle.x && !cleared.includes(vehicle.id)) {
        expect(row[2]).toBeGreaterThan(vehicle.height+1.5);
        cleared.push(vehicle.id);
      }
    }
    if (cleared.length >= 4 && cleared.length <= 5) await picture(page, 'jump-lineup-airborne');
    if (seenAir && row[9] > 0) { landed = row; break; }
  }
  await page.keyboard.up('w');
  expect(cleared).toEqual(parked.map(v => v.id));
  expect(landed).not.toBeNull();
  expect(landed[1]).toBeGreaterThan(parked.at(-1).x+parked.at(-1).width/2+3);
  expect(landed[11]).toBe('ground');
  expect(await page.evaluate(() => badge.inspect('sim.basis()[1][1]'))).toBeGreaterThan(.98);
  await page.evaluate(() => badge.step(10));
  const continued = await state(page);
  expect(continued[0]).toBeGreaterThan(landed[0]);
  expect(continued[1]).toBeGreaterThan(landed[1]);
  await picture(page, 'jump-lineup-landing');
  await record('native-jump-lineup', { input: 'Unlimited practice; W+B to the painted takeoff stripe 11 metres before the row, release B, K, continue W until landing; 33 ms frames',
    initial, takeoff, parked, samples, cleared, landed, continued });
});

test('parked vehicle collision stops a fast approach and a high jump clears it', async ({ page }) => {
  await page.evaluate(() => badge.appAction('visit', 'robovan'));
  await page.evaluate(() => badge.step(2, { dt: 8 }));
  await page.evaluate(() => badge.exec('u26_runtime.app.sim.vz = 70.0'));
  const approach = [];
  let impact = null;
  for (let i = 0; i < 40; i++) {
    await page.evaluate(() => badge.step(1, { dt: 8 }));
    const row = await state(page);
    approach.push(row);
    if (row[10] > 0) { impact = row; break; }
  }
  expect(impact).not.toBeNull();
  expect(impact[11]).toBe('vehicle:robovan');
  expect(impact[6]).toBeLessThan(5);
  const clearance = await page.evaluate(() => badge.inspect(
    'max(sim.z + __import__("physics").q_rotate(sim.q,p)[2] for p in __import__("physics").HULL) - (find_vehicle("robovan")["z"]-find_vehicle("robovan")["collision_boxes"][0][5])'));
  expect(clearance).toBeLessThan(.01);
  await picture(page, 'robovan-fast-impact');
  await page.evaluate(() => badge.appAction('reset'));
  await page.evaluate(() => badge.exec(
    'a=u26_runtime.app\nv=a.find_vehicle("robovan")\n' +
    'a.sim.x=v["x"]\na.sim.z=v["z"]-10\na.sim.y=8\na.sim.vz=25\na.sim.q=(1.,0.,0.,0.)'));
  const jump = [];
  for (let i = 0; i < 25; i++) {
    await page.evaluate(() => badge.step(1));
    const row = await state(page);
    jump.push(row);
    expect(row[9] + row[10]).toBe(0);
    if (i === 12) await picture(page, 'robovan-clear-jump');
  }
  const target = await page.evaluate(() => badge.inspect('find_vehicle("robovan")'));
  expect(jump.at(-1)[3]).toBeGreaterThan(target.z + target.length/2 + 2);
  expect(jump.at(-1)[8]).toBe(1);
  await record('native-parked-impacts', { approach, impact, clearance, jump });
});

test('native 0–60 mph receipt measures motor drive and sub-second rear boost', async ({ page }) => {
  await page.evaluate(() => badge.exec(
    'from physics import Simulation,FIXED_DT\n' +
    'u26_runtime.app.acceleration_receipt=[]\n' +
    'for jets in (False,True):\n' +
    '    s=Simulation("flat")\n' +
    '    c={"throttle":1,"fan":0,"nozzles":[1]*4 if jets else []}\n' +
    '    for i in range(1200):\n' +
    '        previous=s.speed\n        s.step(FIXED_DT,c)\n' +
    '        if s.speed>=26.8224:\n' +
    '            crossing=s.time-FIXED_DT+FIXED_DT*(26.8224-previous)/(s.speed-previous)\n' +
    '            u26_runtime.app.acceleration_receipt.append({"rear_jets":jets,"seconds":crossing,"tick_seconds":s.time,"speed_mps":s.speed,"usable_air":s.air_fraction,"distance_m":s.distance})\n' +
    '            break'));
  const rows = await page.evaluate(() => badge.inspect('acceleration_receipt'));
  expect(rows).toHaveLength(2);
  expect(rows[0].seconds).toBeGreaterThan(1.);
  expect(rows[0].seconds).toBeLessThan(3.);
  expect(rows[1].seconds).toBeGreaterThan(.4);
  expect(rows[1].seconds).toBeLessThan(1.);
  expect(rows[1].seconds).toBeLessThan(rows[0].seconds);
  expect(rows[0].usable_air).toBe(1);
  expect(rows[1].usable_air).toBeLessThan(.9);
  await record('native-acceleration', { runtime: 'MicroPython WASM',
    conditions: 'Flat ground, full throttle, fan off, full initial tank, 120 Hz steps; interpolation at 26.8224 m/s.',
    interpretation: 'Simulation measurements, not verified Tesla specifications or hardware frame-rate measurements.', rows });
});
