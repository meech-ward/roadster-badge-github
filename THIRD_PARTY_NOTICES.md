# Attribution and platform dependencies

The application and its models are included in this repository. Running,
packaging or modifying them does not require downloaded vehicle assets or a
connection to an asset service. The application, procedural models and project
tools are covered by the repository's [MIT license](LICENSE). The components
below have their own terms and are not relicensed as project-owned code.

## Platform and development tools

The app uses the Universe badge's installed **MicroPython**, **badgeware** and
native **pico3d** modules. They are platform dependencies, not implementations
vendored into this repository. Their firmware retains its own licenses and
notices. The app-only package does not replace or redistribute firmware.

The optional USB installer uses **pyserial 3.5**, installed separately through
pip. It is not copied into the game or distributed in the app ZIP. Python and
pyserial retain their own license terms. The local installer and its transport
are repository source; they do not import a helper from another checkout.

The optional browser preview includes this project's adapter and user
interface, with scripts to fetch pinned platform source and build a native
MicroPython/pico3d WebAssembly runtime. Downloaded platform source, fonts and
generated binaries are ignored and excluded from repository release artifacts.
They are not required to package, install or modify the badge app.

The preview bootstrap records its exact source revisions and retains available
notices from Badgeware WASM, Badgeware Web Simulator, MicroPython and their
component libraries. The pinned PicoVector core and bindings have no separate
top-level license file in the inspected source trees; this project does not
claim that its MIT license covers them or distribute their compiled runtime.
See [provenance](docs/PROVENANCE.md) for the distinction between app source and
optional platform tooling.

## Vehicle names and appearance

The vehicle meshes are handwritten visual approximations. Public Tesla images
were used as visual reference; no photographs or imported vehicle models are
shipped or required. The models are not Tesla CAD, and gameplay parameters are
not verified vehicle specifications.

Tesla and the vehicle names identify the subjects of this unofficial fan
project. The project is not affiliated with or endorsed by Tesla, Inc. or
GitHub. The software license does not grant rights to third-party trademarks.
