# Where the code and models come from

This repository contains the complete Roadster app source, procedural vehicle
models, packaging tools, installer, tests and instructions. The game has no
network dependency and reads no files from another project checkout.
Its external runtime dependency is the compatible Universe badge
firmware, which supplies MicroPython, badgeware and pico3d.

## Models

`roadster_model.py` builds the drivable Roadster from handwritten vertices,
surfaces, wheels and details. `fleet_models.py` builds the ten parked vehicles;
`distant_models.py` supplies their lighter display variants. The geometry is
generated locally from Python source. There are no downloaded meshes,
textures, photos or audio files in the app.

The shapes were authored by observing public Tesla photographs and choosing
rounded dimensions and proportions. They are visual approximations, not CAD
or measured production geometry. `vehicle_spec.py` supplies the drivable car's
shared wheel and nozzle geometry, so the visible model and applied forces use
the same local configuration.

The `REFERENCE_URLS` tuples in the two detailed model modules record visual
references. They are ordinary metadata strings: the modules never fetch them
and do not need a browser or internet access to build a model. The repository
can be used entirely offline after its optional development dependencies are
installed.

## Physics

`vehicle_spec.py` defines this game's mechanical design. Its body envelope and
mass distribution determine the center of mass, inertia, wheel placement and
nozzle positions. Suspension parameters follow ride-frequency and damping
targets; motor and jet capacity follow gameplay acceleration targets. These
values are documented design choices, not measured Tesla engineering data.

`physics.py` implements the rigid-body solver, surface queries, contact impulses
and force integration. The fan samples a rectangular plenum and fades suction
smoothly as its seal opens. The optional finite-air model is an isothermal
ideal-gas reservoir with regulated nozzle flow. Delivered impulse consumes
propellant mass, including a shared scale-down when a tick exhausts the supply.
The unlimited practice mode retains the same forces without depleting air.

Controllers derive torque authority from actual nozzle positions, directions
and capacity. They request ordinary nozzle duties; the solver determines the
resulting translation and rotation. Centered recovery and committed tricks do
not assign a completed pose. Gravity, momentum, quaternion transforms and
friction constraints use standard mathematical relationships. The solver is
an approximate game model, not a vehicle engineering simulator.

## Controls, scenes and tools

Badge input, motion recognition, committed flick tricks, camera behavior,
scene construction, HUD and display budgets are maintained in this repository.
The models, scenes and controls can be reused from this checkout. The USB
installer uses a local MicroPython serial transport and the separately
installed pyserial package; it does not import a personal script or require a
sibling checkout.

Installation targets the existing compatible Universe firmware. The optional
browser preview includes this project's UI and adapter, plus scripts that
fetch pinned platform source and build the native MicroPython/pico3d runtime.
The first preview setup needs those downloads and build tools; generated
runtime binaries, fonts and platform source stay ignored and are excluded
from repository release artifacts. No preview build is needed to package or
install the app, export its models, or run the portable host tests.

Firmware and native renderer binaries are not presented as app-owned code.
The pinned PicoVector bindings and core contain no separate top-level license
file in the inspected trees, so this repository does not redistribute their
compiled runtime or apply its own MIT notice to those components. Available
component notices remain with locally generated preview output. See
[installation](INSTALL.md) for device requirements and the supported workflow.

## Reuse

The [MIT license](../LICENSE) permits use, modification and redistribution
subject to its included notice. Keep that notice when redistributing source
or an app package. If you add an outside asset or library later, record its
source and license here before
shipping it. Names and likenesses of the vehicles are separate from the
software license.

The repository's [source inventory](../validation/provenance.json) records the
current app hashes, imports and dependencies. The separate
[release receipt](../validation/source-release.json) records the checks run on
the release. A static source audit is not a hardware test or a claim about the
real vehicles; a native preview test is not a measurement of badge performance.
