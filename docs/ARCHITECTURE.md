# Architecture

The deployable application is `apps/roadster_sandbox/`. Its Python modules
and procedural models travel together; it imports only local siblings,
MicroPython standard modules, and the firmware's `badgeware`/`pico3d` APIs.
Packaging, installation, model export and host tests do not need the preview.
See [provenance](PROVENANCE.md) for source attribution and platform boundaries.

## Module map

| Module | Responsibility |
| --- | --- |
| `__init__.py` | App lifecycle, input routing, menu/scene state, controller API, update loop and exit cleanup |
| `physics.py` | Rigid-body state, forces, suspension, fan, air tank, surface queries and contact impulses |
| `vehicle_spec.py` | Shared vehicle dimensions, wheel/nozzle geometry and game tuning used by models, physics and controls |
| `motion.py` | Portable IMU calibration, tilt estimation and flick recognition; no hardware or drawing |
| `imu_profile.py`, `badge_motion.py` | Reversible sensor setup, firmware orientation and playing-grip calibration |
| `badge_controls.py` | Touch/clicky mixing, angular-rate control, centered recovery and held-lift governor |
| `ground_camera.py` | Bounded, smoothed grounded camera offsets; no sensor access or vehicle forces |
| `tricks.py` | Force-driven named tricks and committed directional flicks |
| `world.py` | Parked-car placement, compound collision boxes and lineup start position |
| `roadster_model.py`, `fleet_models.py`, `distant_models.py` | Handwritten detailed and lightweight vehicle geometry |
| `model_assets.py`, `fleet_scene.py` | Native mesh conversion, static fleet transforms and bounded detail selection |
| `scene.py`, `scenery.py` | Camera, native scene submission, track/ground surfaces, horizon and background |
| `scene_palette.py` | Shared RGB colors chosen to remain distinct on the RGB565 display |
| `hud.py` | Top instruments, tilt/jet diagrams and temporary help/scene overlays |
| `speed_feedback.py`, `stunt_feedback.py` | Observers of actual motion and delivered force; never change physics |

Paths in this table are relative to `apps/roadster_sandbox/`. Tests belong in
`tools/tests/` or `playground/e2e/`, not in the deployable folder.

## A frame and a physics tick

The firmware launcher puts the app directory on `sys.path` and supplies the
badge globals. The app calls `run(update)` once. Its short, zero-argument
callback reads the previous input poll; `badge.ticks_delta` is milliseconds.

1. Read capacitive touch and physical clicky buttons separately. Consume menu
   edges before applying driving commands, so opening Settings cannot also
   select an unseen option.
2. Update the calibrated motion sample and decide whether it is allowed to
   control the car. A newly recognized flick may start one committed trick.
3. Convert elapsed time to seconds and call `Simulation.step`. Its fixed ticks
   mix current controls into motor, fan and nozzle forces. Active rotation,
   lift and trick controllers receive feedback on each tick, including catch-up
   ticks. Contacts can disarm airborne controls within that same frame.
4. Observe completed ticks for telemetry; render the world, instruments and
   any open overlay. Rendering and camera selection never move the car.

Position uses metres, mass kilograms and time seconds. Body axes are **+X
left, +Y up, +Z forward**; quaternion order is **(w, x, y, z)**. Nozzles apply
force at their body anchors, producing both translation and torque. Tricks
rotate, brake and settle using those forces; they do not assign a finished
pose. Impacts, inadequate pressure or manual takeover can interrupt a trick.

`vehicle_spec.py` is the shared design configuration. The handmade body supplies
the vehicle envelope; a small mass distribution supplies center of mass and
inertia. Suspension stiffness and damping follow ride-frequency targets. Wheel
and nozzle locations feed both the visual model and the force solver. Change
that configuration when altering the vehicle instead of copying a torque or
mass constant into a controller.

The optional limited-air mode uses an isothermal ideal-gas reservoir and a
pressure regulator. Requested nozzle impulse consumes exhaust mass; when a
tick reaches the reserve, all simultaneous nozzle forces scale together. The
unlimited practice mode uses the same rated nozzle forces without depleting the
reservoir. These are deliberate game approximations, not a model of a real
Tesla thruster system.

Badge activation selects **40 Hz** ordinary integration, at most four
catch-up ticks per displayed frame, and **240 Hz** refinement for selected
hard impacts. The full desktop profile uses 120 Hz. This is a deliberate
accuracy/responsiveness tradeoff, not identical numerical trajectories at
different rates. `dropped_time` records elapsed time discarded under overload.
Physics rate is not display frame rate.

`on_exit()` restores the sensor registers, orientation settings, renderer core
configuration and framework clear behavior. Preserve that cleanup when adding
new hardware state.

## Controls and extension points

The public controller API lives on the app module:

```python
set_controls({"throttle": 1.0, "steer": 0.0})
control_action("scene", "ramp")
control_action("camera", "behind")
control_action("air", "unlimited")
status = control_status()
```

`set_controls` supplies continuously held values; omitted values release.
`control_action` performs explicit events. `badge_snapshot()` provides richer
diagnostics. The preview calls these interfaces through its adapter; the app
contains no simulator imports. For player mappings, see [controls](CONTROLS.md).

Public scenes are `flat`, `ramp` and `collection`. Collection uses flat-ground
physics plus ten fixed compound bodies. All collision bodies remain active
when a vehicle mesh is culled. The lower-level engine retains closed-track
geometry for regression coverage and shared curved-track math; Full Loop is
not a playable scene or accepted public scene action.

## Rendering and performance constraints

The world uses a **320 × 190** viewport below the 50-pixel top HUD, with no
footer. HUD regions redraw only when their visible pixels change. Overlays
invalidate that cache before normal instruments resume.

The badge profile requests two native render cores and sizes the scene cache
for the active scene. The preview's native runtime is single-core and cannot
measure badge timing or its memory placement. Declared vertex/triangle
capacity matters: an oversized cache can push work into slower memory even
if few objects are visible.

The Roadster retains its detailed mesh. The fleet keeps lightweight geometry
resident and draws at most two nearby cars at its richer badge tier, within a
22-metre detail radius; planar distance culls cars beyond 145 metres. Desktop
inspection can build at most two full-detail parked meshes. Parked transforms
and small selection buffers are reused instead of rebuilt each frame.

Hot-path vector arithmetic is intentionally expanded into scalar expressions.
This avoids MicroPython call/tuple overhead and preserves operation order where
rounding matters. Contact queries also reuse conservative nearby-body caches,
and a stopped fan defers unnecessary surface queries. Do not replace these
with abstraction-heavy helpers just for stylistic uniformity: demonstrate
equivalent behavior and measure the target runtime before changing them.

Keep host correctness, actual MicroPython/WASM behavior, package identity and
physical badge performance as separate evidence. Browser FPS is never badge
FPS. A useful change includes a bounded regression for its behavior and a
targeted runtime check; a renderer change also needs visual inspection.

## Scene readability

Ground uses broad land values, charcoal pavement and wide ivory paint. The
shared palette separates useful boundaries in lightness as well as hue. Both
Airfield and Lineup paint is tessellated into the floor, preventing depth
flicker from near-coplanar layers. The open ramp retains its cyan edges.

An immutable 68-triangle ground-seam mesh uses an eight-metre grid with stronger
lines every 32 metres. It recycles without changing its world phase. A small
clip-space depth bias keeps the seams visible over the floor and beneath cars;
the mesh does not change terrain or collision geometry. Near the airfield and
lineup it adds one draw submission; offsite it replaces the old paint crosses.
The seam mesh has a bounded declared scene-cache cost. Above 200 metres camera
height, the seams cannot enter the view frustum and their
submission is skipped; the backdrop still supplies distant ground detail.

The backdrop uses a world-upright mountain silhouette, two haze bands and at
most 24 perspective survey lines at all heights, with spacing starting at
eight metres and increasing with altitude. Every fourth line is darker. These
lines remain fixed in world space. The fallback terrain recycles by a complete
color period around the nearest patch center, preserving both color phase and
at least 32 metres of ground margin in badge quality. These features reuse
geometry and scalar state rather than building per-frame meshes.

Grounded camera input is evaluated after physics, using current upright wheel
contact and a valid calibrated tilt sample. `ground_camera.py` eases scalar
yaw/elevation offsets within 20° and 1.3 metres; `scene.py` applies them only to
the eye position and keeps world-up. Airborne, thrust, manual-rotation and
overlay contexts release the camera toward neutral. Reset, view changes and
recentering clear it immediately. The existing air-control path is unchanged.

The scene-readability check captures all three scenes, offsite terrain and
flight poses in native MicroPython/pico3d. Its memory figures describe declared
cache size, not measured device heap or frame rate.
