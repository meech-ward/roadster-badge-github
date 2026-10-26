# Preview capabilities and limits

The preview is an app development profile, not complete emulation of the
RP2350 or Universe firmware. It uses pinned public platform source plus this
repository's explicit adapter. Application Python is staged unchanged.

| Surface | Implemented | Boundary |
| --- | --- | --- |
| Python | Actual MicroPython WASM | No ARM/Viper execution or RP2350 timing model |
| 2D/3D | Native PicoVector/pico3d, real meshes and pixels | Host single-core dispatch; browser RGBA differs from badge RGB565 |
| Display | 160×120 and 320×240, native frame copied to canvas | No panel timing, physical brightness or async display; async raises |
| Controls | Logical directions, SELECT/BACK/MENU/HOME; separate buttons/pad, stable edges | Synthetic inputs, not GPIO or touch electrodes; A/B/C unavailable |
| Laptop controller | Portable `set_controls` / `control_action` calls | Separate from hardware input mapping |
| IMU | Injected six-value accel/gyro, synthetic range scaling and clipping | No sensor noise, physical orientation transform or gesture feel |
| IMU registers | Address 0x6A, registers 0x10/0x11; stock 0x40 and wide 0x48 | Other I2C operations raise; not a general bus or sensor driver |
| Timing | Poll after each rendered frame, controlled millisecond steps | Deterministic app behavior, not hardware latency/FPS |
| Lifecycle | One `run(update)`, on_exit, isolated interpreter per boot, request deadline | Nested/sequential loops and launcher chaining unsupported |
| HOME | Physical-source HOME completes app; capacitive HOME is scene input | No physical interrupts or real launcher transition |
| Storage | Real WASM filesystem, read-only system/ROM and writable state | In-memory only; no flash capacity, durability or power-loss model |
| Network/peripherals | Explicit unsupported errors | No radios, battery, USB, light, LEDs, power/wake or hardware compatibility claim |

The IMU adapter takes stock-equivalent samples (16384 counts/g, .00875 dps/count).
The wide configuration returns 8192 counts/g and .035 dps/count, clamped to
signed 16-bit. Fresh boots start at stock settings. Orientation/autorotation
calls and raw touch-electrode queries are explicit gaps; tests needing a grip
orientation instrument app state and say so in their evidence.

Upstream Badgeware receives one anchored bootstrap patch: the external Tufty RTC
import is omitted because it is not part of the Universe profile. Host overrides
supply input, display and lifecycle. The upstream frozen `machine` stubs needed
for boot are not validated Universe APIs. The generated manifest records all
app/runtime hashes, which are verified before each boot.

A native preview pass establishes real interpreter/renderer behavior for its
synthetic inputs. Physical launch time, heap, frame rate, multitouch, sensor feel,
multicore rendering and display behavior need their own badge checks. See
[validation](../docs/VALIDATION.md) for evidence kept separate by environment.
