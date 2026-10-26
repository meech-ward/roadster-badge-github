# Roadster Sandbox

A continuous 3D driving and stunt playground for the GitHub Universe 2026
Badger 2350. This folder is the complete deployable app. Compatible firmware
must provide MicroPython, badgeware and native pico3d. No downloaded models,
textures, network access, browser code or other app folders are required.

## Install

Use the source repository's `tools/install_badge.py` with an explicit USB port.
It backs up and verifies only this app, then returns to the badge's normal menu.
A packaged app ZIP contains this folder and is accepted by the same installer.
The ZIP is not firmware and cannot be installed by copying it to a UF2 drive.

Advanced firmware tooling may copy this complete directory to
`/system/apps/roadster_sandbox/`; its entry point is `__init__.py`. Keep the
included LICENSE. Do not replace boot files or put tests in this directory.

## Play

Hold the badge facing you in the inverted gamepad grip. Tap the outer right
pad, hold still to center, and release the thrust controls before playing.

- Touch D-pad: drive, brake/reverse and steer.
- Outer right touch pad: hold rear boost. Inner right pad: hold lift.
- Clicky directions: controlled roll and pitch; release to counterthrust.
- Clicky center: toggle the fan, whose ON/OFF tile is beside it on screen.
- Left middle touch pad: Options, with Reset car first.
- Right middle touch pad: scene chooser. Outer right confirms; inner right cancels.
- Tilt: look around on the ground, or rotate in the air.
- Deliberate flick: attempt a full kickflip, backflip or frontflip with jets.

After centering, tilt left/right to orbit the grounded car and up/down to look
upward/downward. The view moves gently within a limited range, works
while driving with the D-pad, and returns when you center the badge. The gauge
says LOOK. Holding lift or boost, or taking off, returns tilt to the jets;
centered airborne tilt requests leveling. Clicky rotation takes priority over
camera control.

Motion defaults to Both (tilt plus flick), and air is unlimited for practice.
Vertical flicks need a brisk, deliberate stroke; gentle up/down tilt stays manual.
Jet control is active in flight; on a surface it needs a held lift or boost pad.
Clicky directions override a committed flip. A collision can interrupt it.
Recenter from Options whenever your grip changes. Menus keep physics running
and require release of thrust/clicky controls before they resume.

The top instruments show km/h, actual boost, fan, tilt and delivered jets.
Lift rises quickly and eases toward a practice hover. Fan and thrusters work
together; suction can hold the car down even with lift firing. Nothing resets
the car automatically after a crash or upside-down landing.

Scenes use dark pavement, wide ivory paint, warm land and a stable mountain
horizon. Ground seams stay fixed in the world, giving passing scenery a visible
scale while driving, rising and falling; distant survey lines continue beyond
the nearby geometry.
Scene colors are shared in `scene_palette.py`; all geometry remains local.

Scenes: Airfield, Open Ramp and Tesla Lineup. The ramp defaults to Directly
behind; the others share Follow Roadster framing. Options can switch cameras.
The ten parked cars remain fixed and collidable; only the new Roadster drives.
Vehicle shapes are handmade visual approximations, not manufacturer CAD.

## Code

`__init__.py` integrates the firmware loop; `vehicle_spec.py` defines shared
geometry and mechanical tuning; `physics.py` handles forces and contacts;
`motion.py` recognizes tilt/flick; `badge_controls.py`
arbitrates player input; `ground_camera.py` smooths grounded looking;
`scene.py` builds views; `hud.py` paints
instruments. Model and scene modules are ordinary sibling Python files.
The source repository contains architecture, controls, validation and reuse
guides, a native browser preview and host tests outside this app folder.

The game source and handmade models are MIT-licensed under the included LICENSE.
Vehicle dimensions and mechanical parameters are game design choices; they do
not describe a verified real-world thruster system. Firmware and native
rendering modules remain separately licensed platform dependencies.
