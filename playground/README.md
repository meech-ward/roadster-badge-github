# Native browser preview

This optional developer tool runs the same app Python in a real MicroPython
WASM interpreter with source-built native pico3d. The game, installer and host
tests work independently of it.

## First setup

Use macOS or Linux, or Linux under WSL. Install Git, Node.js 22+, Python 3.10+,
CMake, Make, and Emscripten; activate its environment so `emcc` and `emcmake`
are on `PATH`. The native build uses those installed tools and pins all platform
source revisions in `scripts/build-pico3d-runtime.sh`.
The verified compiler series is Emscripten 6.0.13; the native CI workflow pins
that SDK version as well.

```sh
cd playground
npm ci
npm run build:runtime
npm run dev
```

Open `http://127.0.0.1:5186`. The first build downloads platform source into
this repository's ignored `.cache/`; nothing is read from sibling projects.
The runtime's ten-frame native drawing probe must pass before staging succeeds.
Generated platform binaries and fonts are local development output, excluded
from source/app releases. See [third-party notices](../THIRD_PARTY_NOTICES.md).

`npm run dev` stages current Python and starts the server. After editing Python,
run `npm run prepare-runtime` again, then press Boot; Vite alone does not restage
Python. `npm run build` makes a local static preview under `dist/`; it does not
publish it. The preview requires the pico3d runtime and refuses a 2D-only profile.

## Laptop controls

WASD/arrows drive; Space brakes; B boosts; J lifts; Q/E apply left/right bottom jets;
1–0 fire the ten individual nozzles; F toggles the fan; R resets; T refills.
H hops, K kickflips, L spins, I backflips and X cancels a named trick. The page
also exposes buttons, scene/camera selectors, parked-model inspection, settings
and telemetry. Focus loss releases held controls without pausing physics.

These are development controls, separate from the badge's physical layout.
See [controls](../docs/CONTROLS.md). Effects can be reduced without changing
physics. Only the Roadster is drivable; inspecting another vehicle does not
pause the simulation.

## Tests and inspection

```sh
npx playwright install chromium
npm test
```

The test configuration requires native pico3d and starts a fresh server that
restages source. Results go to `test-results/results.json`; per-spec screenshots
and diagnostics go to `test-results/evidence/`. To use an existing Chrome,
set `CHROME_PATH` to its executable. Use `PW_PORT=5187 npm test` when 5186 is busy.
Only deliberately reuse a restaged server with `PW_REUSE=1 npm test`.

Open `/?test=1&app=roadster_sandbox` for manual stepping without autoplay. The
page exposes the following development API:

```js
await badge.boot('roadster_sandbox');
await badge.appAction('scene', 'flat');
await badge.step(30, { dt: 33, appControls: { throttle: 0.5 } });
await badge.appAction('trick', 'kickflip');
await badge.tap('MENU', 'pad');
await badge.inspect('control_status()');
const png = badge.snapshot();
```

`step()` uses logical `buttons`, `pad` and `imu` inputs. `tap()` includes two
down/two up frames because the app reads the previous poll. IMU input is
`[ax, ay, az, gx, gy, gz]`, in stock-equivalent units of 16,384 counts/g and
0.00875 degrees/second/count. The synthetic adapter converts those values when
the app requests a wider sensor range; it does not simulate a sensor driver.

`badge.exec(code)` instruments the interpreter; keep test code out of the app.
`readFile`, `exportState`, `play` and `pause` are also available. A fresh boot
has empty `/state` unless supplied exported state. Playback speed is host
dependent and is never a measurement of badge frame rate.

## Code ownership

`src/main.js` owns UI/automation, `src/worker.js` isolates the VM and verifies
staged hashes, `compat/` adapts logical firmware APIs, and `e2e/` owns tests.
Canonical app code lives at `../apps/roadster_sandbox/`. Unsupported operations
raise explicit errors. Read [capabilities](CAPABILITIES.md) before interpreting
test evidence or extending an adapter.
