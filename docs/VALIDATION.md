# Validation and remaining limits

The release evidence in [source-release.json](../validation/source-release.json)
identifies the app files by SHA-256 and records the host, native preview,
packaging and extracted-source checks performed for those exact bytes.
The [source inventory](../validation/provenance.json) records the app's imports,
model construction and platform dependencies. The optional native runtime's
build provenance is separate in [native-runtime.json](../validation/native-runtime.json).

The current vehicle configuration and force models have been rebuilt for this
project. Earlier trajectories, source hashes and badge measurements do not
validate this version. They are not included as current release evidence.

## What the checks establish

| Check | Establishes | Does not establish |
| --- | --- | --- |
| Host tests | Physical/control behavior, finite geometry, model interfaces, installer failure handling and deterministic packaging | Firmware import, rendered pixels or badge timing |
| Native preview | Actual MicroPython/WASM execution and native pico3d/PicoVector frames with synthetic controls and IMU input | RP2350 speed, physical touch/gyro behavior or the badge's multicore renderer |
| Package checks | App files, safe paths, source hashes, portable extraction and an offline install plan | USB transfer or a successful launch on a device |
| Physical badge checks | Only the specific transfer, input, display and performance observations recorded for that source | Performance of later changed source or long-session stability unless measured |

The source release is checked from a fresh extraction, including a path with
spaces, from outside the checkout and without a sibling repository or
`PYTHONPATH` setup. A rebuilt app ZIP must match the released app ZIP. Source
archives exclude `.git`, build caches, dependencies, generated platform
binaries, private backups and developer test output.

## Physics and controls

Tests cover individual nozzle force and torque, finite and unlimited air,
fan seal and release, ground driving, parked-body collisions, repeated landing
and takeoff, manual rotation, leveling and committed flick flips. Shared
vehicle geometry is checked against both visible wheels/nozzles and the
controller's measured torque authority. The force controller must remain valid
when mountings, capacity or mass change; it must not rely on a frozen torque
number from another configuration.

The regression suite also covers the 40 Hz badge integration budget, selected
240 Hz impact refinement, time lost under frame overload, sensor dropouts,
clipping and menu/input handover. Tests describe gameplay requirements and
basic physical invariants; they do not require matching an earlier engine's
numerical trajectory.

Ground camera tests exercise calibrated tilt in all four directions, centering,
clip handling, grounded contact gating and the transition back to air controls.
Forward/back flick recognition has a higher threshold than sideways flicks.
Synthetic sensor tests verify those thresholds; a player still needs to judge
the resulting physical gesture feel.

## Rendering and readable motion

Native checks inspect scene frames, camera framing, compact HUD state and
actual pixel changes across driving and flight poses. Fixed ground seams and
distant survey lines must move with the world, remain stationary when the car
stops, and stay behind the car. The renderer is also exercised across garbage
collections and high-altitude poses.

Airfield, Open Ramp and Tesla Lineup use bounded scene caches, a limited fleet
detail budget and reusable geometry. These are code-level cost limits, not a
claim that the badge holds a particular frame rate. Browser playback speed
cannot measure badge performance.

## Hardware status and remaining limits

This source replacement has not been installed or profiled on a physical badge.
The next hardware pass should check all three scenes, repeated takeoff/landing,
four-direction flicks, centered recovery, camera direction, multitouch,
launch time, free memory and heavy lineup collisions. Keep the resulting
receipt separate and identify the tested app hashes.

- Ordinary badge physics runs at 40 Hz with selective 240 Hz impact refinement;
  the desktop profile runs at 120 Hz. Numerical trajectories differ by design.
- Parked cars use fixed compound collision shapes. They do not deform or move,
  and the colliders do not follow every rendered detail.
- Gesture recognition is sampled. Strong translation, clipped samples or sparse
  frames can prevent recognition even when the tilt indicator moves.
- Flips and centered recovery use actual jets. Obstacles, inadequate altitude,
  orientation, fan suction and optional finite air can prevent a clean landing.
  There is no pose teleport or automatic crash reset.
- Compatible Universe firmware is required. Other Universe years, generic Pico
  boards and Tufty firmware are not supported by association.
- Automated checks do not establish battery behavior, every host's USB driver
  compatibility, physical display readability or long-session device stability.

Run the documented checks for any further source changes and record new
results. See [development](DEVELOPMENT.md) for commands and release steps.
