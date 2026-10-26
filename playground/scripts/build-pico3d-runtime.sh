#!/usr/bin/env bash
# Build the native pico3d runtime used by the Roadster preview.
# All source checkouts, compiler outputs and generated binaries stay under .cache/.
set -euo pipefail

TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_ROOT="$TASK_ROOT/.cache/pico3d-build"
SOURCE_ROOT="$BUILD_ROOT/source"
OUTPUT_ROOT="$TASK_ROOT/.cache/pico3d-runtime"
WASM_PIN=8cc5b05a1a07a823eb68ca1952b3603fe4d769bf
MICROPYTHON_PIN=4559a9a5a433f75ada6a01933c19878fd3a7dd81
PICOVECTOR_MP_PIN=01e0b85c7539b6f43310cdaabb288e2fad1198be
PICOVECTOR_PIN=bff41fa90c92b92da5b09ec0dd7be4aca3733a00

command -v emcc >/dev/null || { echo "Install/activate Emscripten (emcc) first." >&2; exit 1; }
command -v cmake >/dev/null || { echo "CMake is required." >&2; exit 1; }
command -v node >/dev/null || { echo "Node.js is required for the native runtime probe." >&2; exit 1; }
# CMake calls the SDK's compiler directly, bypassing Homebrew's wrapper that
# normally selects its Python. macOS's bundled Python 3.9 is too old for emcc.
if [ -z "${EMSDK_PYTHON:-}" ]; then
    for candidate in python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
            export EMSDK_PYTHON="$(command -v "$candidate")"
            break
        fi
    done
fi
"${EMSDK_PYTHON:-python3}" -c 'import sys; assert sys.version_info >= (3, 10), "Emscripten needs Python 3.10+"'
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
mkdir -p "$BUILD_ROOT" "$OUTPUT_ROOT"
# A failed build/probe must never leave a previous success receipt usable.
rm -f "$OUTPUT_ROOT/probe-result.json" "$OUTPUT_ROOT/probe-frame.rgba" "$OUTPUT_ROOT/probe-frame.png"

# Every checkout lives inside this repository's ignored cache. No sibling
# repositories, alternates or personal source locations are consulted.
checkout_pin() {
    local destination="$1" url="$2" pin="$3"
    if [ ! -e "$destination/.git" ]; then
        git init --quiet "$destination"
        git -C "$destination" remote add origin "$url"
    fi
    if [ -n "$(git -C "$destination" status --porcelain --untracked-files=no)" ]; then
        echo "Cached dependency has local changes: $destination" >&2
        exit 1
    fi
    if ! git -C "$destination" cat-file -e "$pin^{commit}" 2>/dev/null; then
        git -C "$destination" fetch --depth 1 origin "$pin"
    fi
    git -C "$destination" checkout --quiet --detach "$pin"
}

checkout_pin "$SOURCE_ROOT" https://github.com/pimoroni/badgeware-wasm.git "$WASM_PIN"
checkout_pin "$SOURCE_ROOT/micropython" https://github.com/pimoroni/micropython.git "$MICROPYTHON_PIN"
checkout_pin "$SOURCE_ROOT/picovector-micropython" https://github.com/pimoroni/picovector-micropython.git "$PICOVECTOR_MP_PIN"
checkout_pin "$SOURCE_ROOT/picovector-micropython/picovector" https://github.com/pimoroni/picovector.git "$PICOVECTOR_PIN"
git -C "$SOURCE_ROOT/micropython" submodule update --init --depth 1 lib/micropython-lib

make -C "$SOURCE_ROOT/micropython/mpy-cross" -j"$JOBS"
emcmake cmake -B "$BUILD_ROOT/build" -S "$SOURCE_ROOT/micropython/ports/webassembly" \
    -DMICROPY_VARIANT_DIR="$SOURCE_ROOT/variants/badgeware-tufty2350" \
    -DBADGEWARE_ASYNC=asyncify-fast -DPV_PICO3D=ON -DPV_DUAL_CORE=OFF
cmake --build "$BUILD_ROOT/build" -j"$JOBS"
cp "$BUILD_ROOT/build/micropython.mjs" "$BUILD_ROOT/build/micropython.wasm" "$OUTPUT_ROOT/"
mkdir -p "$OUTPUT_ROOT/licenses"
cp "$SOURCE_ROOT/LICENSE" "$OUTPUT_ROOT/licenses/badgeware-wasm-MIT.txt"
cp "$SOURCE_ROOT/micropython/LICENSE" "$OUTPUT_ROOT/licenses/MicroPython-MIT.txt"
cp "$SOURCE_ROOT/micropython/lib/micropython-lib/LICENSE" "$OUTPUT_ROOT/licenses/micropython-lib-MIT.txt"
cp "$SOURCE_ROOT/picovector-micropython/picovector/lib/qrcodegen/LICENSE" "$OUTPUT_ROOT/licenses/qrcodegen-MIT.txt"
cp "$SOURCE_ROOT/picovector-micropython/picovector/lib/pngdec/LICENSE" "$OUTPUT_ROOT/licenses/PNGDEC-JPEGDEC-Apache-2.0.txt"
# zlib's complete notice is embedded in its public header.
cp "$SOURCE_ROOT/picovector-micropython/picovector/lib/pngdec/zlib.h" "$OUTPUT_ROOT/licenses/zlib.h"
cat > "$OUTPUT_ROOT/THIRD_PARTY_NOTICES.txt" <<'NOTICES'
This optional runtime builds public source from the exact commits recorded in
provenance.json. Retain this directory's notices and licenses with copied output.

Badgeware WASM: Copyright (c) 2026 Pimoroni Ltd; MIT.
MicroPython: Copyright (c) 2013-2026 Damien P. George; MIT.
MicroPython-lib and QR Code generator: see licenses/ for upstream MIT notices.
PNGDEC and JPEGDEC: Copyright 2021/2020 BitBank Software, Inc.; Apache 2.0.
zlib: Copyright (C) 1995-2017 Jean-loup Gailly and Mark Adler; see licenses/zlib.h.

PicoVector bindings/core are pinned in provenance.json; the pinned checkouts
contain no separate top-level LICENSE file. Their source is retained under
.cache/pico3d-build/source/picovector-micropython for inspection.
NOTICES

"$EMSDK_PYTHON" - "$OUTPUT_ROOT" "$WASM_PIN" "$MICROPYTHON_PIN" "$PICOVECTOR_MP_PIN" "$PICOVECTOR_PIN" <<'PY'
import hashlib, json, pathlib, subprocess, sys
out = pathlib.Path(sys.argv[1])
data = {
    "kind": "optional-native-pico3d-wasm",
    "badgeware_wasm": sys.argv[2],
    "micropython": sys.argv[3],
    "picovector_micropython": sys.argv[4],
    "picovector": sys.argv[5],
    "backend": "asyncify-fast",
    "native_renderer": True,
    "render_cores": 1,
    "hardware_timing_model": False,
    "emscripten": subprocess.check_output(["emcc", "--version"], text=True).splitlines()[0],
    "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (out / "micropython.mjs", out / "micropython.wasm")},
}
(out / "provenance.json").write_text(json.dumps(data, indent=2) + "\n")
PY

# Exercise native bindings and changing pixels, rather than accepting an import
# alone. This is a host renderer check, never an estimate of badge frame rate.
node --input-type=module - "$OUTPUT_ROOT" <<'JS'
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const output = process.argv[2];
const {loadMicroPython} = await import(pathToFileURL(path.join(output, 'micropython.mjs')));
const mp = await loadMicroPython({heapsize: 8 * 1024 * 1024, pystack: 64 * 1024});
await mp.runPythonAsync(`
import array, gc, json, pico3d
from picovector import image, color
assert hasattr(pico3d, 'scene') and hasattr(pico3d.surface, 'draw')
assert hasattr(pico3d.engine, 'work_bands')
# The API remembers requested cores; host dispatch is still single-core.
pico3d.engine.cores(1)
assert pico3d.engine.core_count() == 1
pico3d.engine.work_bands(6)
target = image(320, 240)
surface = pico3d.surface(target)
mesh = pico3d.mesh(positions=array.array('f', [-.8,-.7,0, .8,-.7,0, 0,.8,0]), indices=array.array('H', [0,1,2]), quantise=True)
material = pico3d.material(color=color.rgb(235,60,40), shading=pico3d.material.UNLIT)
material.double_sided = True
camera = pico3d.mat4.perspective(55, 320/240, .1, 10) * pico3d.mat4.look_at(pico3d.vec3(0,0,3), pico3d.vec3(0,0,0), pico3d.vec3(0,1,0))
scene = pico3d.scene(surface, meshes=1, vertices=3, triangles=1)
checksums = []
for frame in range(10):
    target.pen = color.rgb(7,11,19)
    target.clear()
    scene.reset()
    assert scene.add(mesh, pico3d.mat4().rotate_y(frame * 3), camera, material)
    surface.clear_depth()
    assert surface.draw(scene) > 0
    checksums.append(sum(target.raw))
    raw = target.raw
    centre = (120*320+160)*4
    assert raw[centre] > raw[centre+1] and raw[centre] > raw[centre+2]
    gc.collect()
assert len(set(checksums)) > 1
with open('/native-probe.rgba', 'wb') as file:
    file.write(target.raw)
with open('/native-probe.json', 'w') as file:
    json.dump({'native_pico3d': True, 'scene_mesh_render_passed': True, 'frames': 10, 'dimensions': [320,240], 'frame_checksums': checksums, 'hardware_timing_validated': False}, file)
`);
const result = JSON.parse(new TextDecoder().decode(mp.FS.readFile('/native-probe.json')));
result.passed = true;
result.sha256 = Object.fromEntries(['micropython.mjs', 'micropython.wasm'].map(name => [name, createHash('sha256').update(fs.readFileSync(path.join(output, name))).digest('hex')]));
fs.writeFileSync(path.join(output, 'probe-result.json'), JSON.stringify(result, null, 2)+'\n');
fs.writeFileSync(path.join(output, 'probe-frame.rgba'), mp.FS.readFile('/native-probe.rgba'));
console.log('Native pico3d probe passed:', JSON.stringify(result));
JS
"$EMSDK_PYTHON" - "$OUTPUT_ROOT" <<'PY'
import json, pathlib, struct, sys, zlib
out = pathlib.Path(sys.argv[1])
w, h = json.loads((out / 'probe-result.json').read_text())['dimensions']
rgba = (out / 'probe-frame.rgba').read_bytes()
assert len(rgba) == w * h * 4
def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
rows = b''.join(b'\0' + rgba[y*w*4:(y+1)*w*4] for y in range(h))
png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
(out / 'probe-frame.png').write_bytes(png)
PY
echo "Roadster native runtime: $OUTPUT_ROOT"
