import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-motion-ground');
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const inspect = (page, code) => page.evaluate(code => badge.inspect(code), code);
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const step = page => page.evaluate(() => badge.step(1, { dt: 0, buttons: [], pad: [], appControls: {} }));

// The visible ground below the horizon, minus a generous car rectangle,
// excludes the HUD and drivable car. Their animation cannot pass this test.
// Quantize to the badge's RGB565 precision before comparing colors. These are
// real browser/native pixels, not a substitute for testing the physical panel.
const groundPixels = page => page.locator('canvas').evaluate(canvas => {
  const rgba = canvas.getContext('2d').getImageData(0, 0, 320, 240).data;
  const pixels = [];
  for (let y = 100; y < 235; y++) {
    for (let x = 0; x < 320; x++) {
      if (x >= 85 && x <= 235 && y < 175) continue;
      const i = (y * 320 + x) * 4;
      pixels.push(((rgba[i] >> 3) << 11) | ((rgba[i + 1] >> 2) << 5) | (rgba[i + 2] >> 3));
    }
  }
  return pixels;
});
const changedPixels = (first, second) => first.reduce((count, color, index) => count + Number(color !== second[index]), 0);
const detailPixels = pixels => {
  const colors = new Map();
  for (const color of pixels) colors.set(color, (colors.get(color) || 0) + 1);
  return pixels.length - Math.max(...colors.values());
};

const poseFrame = async (page, x, y, z, time) => {
  await exec(page, `sim.x, sim.y, sim.z = ${x}, ${y}, ${z}
sim.q = (1., 0., 0., 0.)
sim.vx = sim.vy = sim.vz = 0.
sim.time = ${time}
feedback.intensity = feedback.speed_mix = 0.`);
  await step(page);
  return groundPixels(page);
};

test('native ground details show travel and height changes, then stay still after stopping', async ({ page }) => {
  test.setTimeout(300000);
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  // Freeze physics/input to isolate the native camera, projection, clipping,
  // depth and rasterization. Advance game time while stopped to reject animated
  // scenery that appears to move without actual world-space movement.
  await exec(page, `
def _ground_motion_frame():
    scene.camera_time = -1.
    scene.draw(sim, feedback, True, viewport=WORLD_VIEWPORT)
__import__('u26_runtime').active.update = _ground_motion_frame
`);
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const rows = [];
  for (const sceneName of ['flat', 'ramp', 'collection']) {
    await action(page, 'scene', sceneName);
    for (const [site, x, z] of [['grass', 70, 80], ['bare-apron', 30, 12], ['offsite', 400, 400]]) {
      for (const [heightName, y] of [['ground', .55], ['low-air', 4], ['medium-air', 12], ['high-air', 30]]) {
        const label = `${sceneName}/${site}/${heightName}`;
        const atRest = await poseFrame(page, x, y, z, 10);
        const png = await page.evaluate(() => badge.snapshot());
        await writeFile(resolve(output, `${sceneName}-${site}-${heightName}.png`), Buffer.from(png.split(',')[1], 'base64'));
        const laterAtRest = await poseFrame(page, x, y, z, 11);
        const sideTravel = await poseFrame(page, x + 2, y, z, 12);
        const forwardTravel = await poseFrame(page, x, y, z + 2, 13);
        const raised = await poseFrame(page, x, y + 3, z, 14);
        const landedAgain = await poseFrame(page, x, y, z, 15);
        const metrics = {
          detail: detailPixels(atRest),
          stopped: changedPixels(atRest, laterAtRest),
          sideTravel: changedPixels(atRest, sideTravel),
          forwardTravel: changedPixels(atRest, forwardTravel),
          rise: changedPixels(atRest, raised),
          returnToHeight: changedPixels(atRest, landedAgain),
        };
        // Broad visibility floors, not exact palette snapshots: a one-pixel
        // artifact cannot count as a ground cue across 31,875 sampled pixels.
        expect(metrics.detail, `${label}: visible detail outside the car`).toBeGreaterThan(100);
        expect(metrics.stopped, `${label}: no false movement while stopped`).toBe(0);
        expect(metrics.sideTravel, `${label}: lateral world-space travel visible`).toBeGreaterThan(24);
        expect(metrics.forwardTravel, `${label}: forward world-space travel visible`).toBeGreaterThan(24);
        expect(metrics.rise, `${label}: vertical travel changes ground projection`).toBeGreaterThan(24);
        expect(metrics.returnToHeight, `${label}: landing restores fixed world details`).toBe(0);
        const render = await inspect(page, `{
          'triangles': scene.triangle_count,
          'triangleCapacity': scene.scene_triangle_capacity,
          'arenaEstimate': scene.scene_arena_estimate,
          'obstacles': len(sim.obstacles),
          'pose': [sim.x, sim.y, sim.z, sim.q],
          'camera': [scene.background.ex, scene.background.ey, scene.background.ez]
        }`);
        expect(render.triangles).toBeGreaterThan(0);
        expect(render.triangles).toBeLessThanOrEqual(render.triangleCapacity);
        expect(render.arenaEstimate).toBeLessThan(sceneName === 'collection' ? 125000 : 105000);
        expect(render.obstacles > 0).toBe(sceneName === 'collection');
        rows.push({ scene: sceneName, site, height: y, pixels: atRest.length, metrics, render });
      }
    }
  }
  await writeFile(resolve(output, 'motion-ground.json'), JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d. Fixed poses and changing time isolate visible ground motion from physics, HUD and car animation. Pixel comparisons include RGB565 quantization, but do not measure physical panel visibility or badge frame rate.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    sampledRegion: 'x=0..319, y=100..234, excluding x=85..235/y=100..174; outside HUD, horizon and drivable car',
    rows,
  }, null, 2) + '\n');
});

const framePixels = page => page.locator('canvas').evaluate(canvas => {
  const rgba = canvas.getContext('2d').getImageData(0, 0, 320, 240).data;
  const pixels = [];
  for (let i = 0; i < rgba.length; i += 4) {
    pixels.push(((rgba[i] >> 3) << 11) | ((rgba[i + 1] >> 2) << 5) | (rgba[i + 2] >> 3));
  }
  return pixels;
});

const carInterior = (withCar, withoutCar) => {
  const mask = withCar.map((color, i) => color !== withoutCar[i]);
  const interior = [];
  // Erode one pixel all around the car silhouette, so edge rasterization and
  // revealed background cannot be mistaken for paint leaking through its body.
  for (let y = 51; y < 239; y++) {
    for (let x = 1; x < 319; x++) {
      const i = y * 320 + x;
      if ([-321, -320, -319, -1, 0, 1, 319, 320, 321].every(offset => mask[i + offset])) {
        interior.push(i);
      }
    }
  }
  return interior;
};

test('native surface paint is depth-occluded by the car on grass and pavement', async ({ page }) => {
  test.setTimeout(60000);
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await exec(page, `
_occlusion_render_car = scene.render_car
def _occlusion_no_car(*unused):
    pass
def _occlusion_frame():
    scene.camera_time = -1.
    scene.draw(sim, feedback, True, viewport=WORLD_VIEWPORT)
__import__('u26_runtime').active.update = _occlusion_frame
`);
  await mkdir(output, { recursive: true });
  const rows = [];
  try {
    // Put an actual grid crossing beneath the car. The grass pose avoids the
    // runway's existing paint, which could otherwise conceal grid coverage.
    for (const [sceneName, x, z] of [['flat', 64, 80], ['collection', 32, 8]]) {
      await action(page, 'scene', sceneName);
      await action(page, 'camera', 'behind');
      await exec(page, 'scene.render_car = _occlusion_render_car\nscene._surface_x = None');
      await poseFrame(page, x, .55, z, 10);
      const carWithGrid = await framePixels(page);
      const carPng = await page.evaluate(() => badge.snapshot());
      await writeFile(resolve(output, `occlusion-${sceneName}-car.png`), Buffer.from(carPng.split(',')[1], 'base64'));
      // Keep the camera and cached grid center unchanged; move only this one
      // test mesh below the far clip, preserving all other native rendering.
      await exec(page, 'scene._surface_transform = scene.pico3d.mat4().translate(scene._surface_x, -1000., scene._surface_z)');
      await step(page);
      const carWithoutGrid = await framePixels(page);
      await exec(page, 'scene.render_car = _occlusion_no_car');
      await step(page);
      const floorWithoutGrid = await framePixels(page);
      await exec(page, 'scene._surface_x = None');
      await step(page);
      const floorWithGrid = await framePixels(page);
      const floorPng = await page.evaluate(() => badge.snapshot());
      await writeFile(resolve(output, `occlusion-${sceneName}-floor.png`), Buffer.from(floorPng.split(',')[1], 'base64'));
      const interior = carInterior(carWithoutGrid, floorWithoutGrid);
      const underneath = interior.filter(i => floorWithoutGrid[i] !== floorWithGrid[i]);
      const sensitive = underneath.filter(i => carWithoutGrid[i] !== floorWithGrid[i]);
      const changedCar = interior.filter(i => carWithGrid[i] !== carWithoutGrid[i]);
      const metrics = { interior: interior.length, underneath: underneath.length,
        sensitive: sensitive.length, changedCar: changedCar.length };
      expect(metrics.interior, `${sceneName}: substantial native car mask`).toBeGreaterThan(100);
      expect(metrics.sensitive, `${sceneName}: detectable paint lies behind the car`).toBeGreaterThan(5);
      expect(metrics.changedCar, `${sceneName}: surface paint never draws through car interior`).toBe(0);
      rows.push({ scene: sceneName, pose: [x, .55, z], ...metrics });
    }
  } finally {
    await exec(page, 'scene.render_car = _occlusion_render_car\nscene._surface_x = None');
  }
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  await writeFile(resolve(output, 'motion-ground-occlusion.json'), JSON.stringify({
    boundary: 'Actual MicroPython/WASM native pico3d pixels. Four renders isolate the car, floor and surface grid. A one-pixel-eroded car mask must remain unchanged even where contrasting grid paint is visible beneath it in the car-free render. No hardware timing claim.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    rows,
  }, null, 2) + '\n');
});

test('native scene submission survives collection immediately before drawing at low and extreme heights', async ({ page }) => {
  test.setTimeout(120000);
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await exec(page, `
_gc_native_renderer = scene.renderer
_gc_draw_count = 0
class _GcDrawProxy:
    def draw(self, geometry):
        global _gc_draw_count
        __import__('gc').collect()
        _gc_draw_count += 1
        return _gc_native_renderer.draw(geometry)
def _gc_frame():
    scene.camera_time = -1.
    scene.draw(sim, feedback, True, viewport=WORLD_VIEWPORT)
__import__('u26_runtime').active.update = _gc_frame
`);
  const rows = [];
  try {
    for (const sceneName of ['flat', 'ramp', 'collection']) {
      // pico3d.scene requires its native surface when allocating the cache.
      // Wrap only after scene preparation; never pass the proxy to pico3d.
      await exec(page, 'scene.renderer = _gc_native_renderer');
      await action(page, 'scene', sceneName);
      await exec(page, 'scene.prepare_geometry(sim)\nscene.renderer = _GcDrawProxy()');
      const ids = await inspect(page, '[id(scene.geometry), id(scene.surface_grid)]');
      for (const view of ['chase', 'behind']) {
        await action(page, 'camera', view);
        for (const height of [.55, 30, 198, 201, 5000]) {
          for (const yaw of [-6, 0, 6]) {
            const halfYaw = yaw * Math.PI / 360;
            await exec(page, `sim.y = ${height}
sim.q = (${Math.cos(halfYaw)}, 0., ${Math.sin(halfYaw)}, 0.)
sim.vx = sim.vy = sim.vz = 0.
sim.time += .033
feedback.intensity = feedback.speed_mix = 0.`);
            await step(page);
            const state = await inspect(page, `{
              'ids': [id(scene.geometry), id(scene.surface_grid)],
              'camera': [scene.background.ex, scene.background.ey, scene.background.ez,
                         scene.background.fx, scene.background.fy, scene.background.fz,
                         scene.background.horizon_y],
              'triangles': scene.triangle_count,
              'capacity': scene.scene_triangle_capacity,
              'draws': _gc_draw_count
            }`);
            expect(state.ids, `${sceneName}/${view}/${height}/${yaw}: reusable native objects`).toEqual(ids);
            expect(state.camera.every(Number.isFinite), `${sceneName}/${view}/${height}/${yaw}: finite projection`).toBe(true);
            expect(state.triangles).toBeGreaterThan(0);
            expect(state.triangles).toBeLessThanOrEqual(state.capacity);
            expect(state.draws).toBe(rows.length + 1);
            rows.push({ scene: sceneName, view, height, yaw, ...state });
          }
        }
      }
    }
  } finally {
    await exec(page, 'scene.renderer = _gc_native_renderer');
  }
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  await writeFile(resolve(output, 'motion-ground-gc.json'), JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d. Test-only renderer proxy forces garbage collection after all scene submissions and immediately before native draw; geometry and renderer remain real native objects. Covers ground, flight, either side of the distant-grid cull and 5000m. No physical badge timing claim.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    rows,
  }, null, 2) + '\n');
});
