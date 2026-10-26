"""Explicit Universe preview profile over upstream Badgeware and native 2D.

No emulated CPU, real sensor driver, or pico3d renderer is claimed here.
"""
import builtins
import json
import sys
import sim_host
import _input

active = None
app = None
finished = False
reason = None

class Run:
    def __init__(self, update=None, duration=None):
        global active
        if active is not None:
            raise NotImplementedError('nested/sequential run loops are not supported by this profile')
        self.update = update
        self.duration = duration
        self.start = badge.ticks
        self.result = None
        active = self
        builtins.loop = self

    def __call__(self, update):
        self.update = update

    @property
    def ticks(self):
        return badge.ticks - self.start

    @property
    def progress(self):
        return 0 if self.duration is None else self.ticks / self.duration

class Source:
    def __init__(self, name):
        self.name = name

    def read(self, index, button):
        value = _input.source_edges[self.name][index]
        return value if button is None else button in value

    def pressed(self, button=None): return self.read(0, button)
    def held(self, button=None): return self.read(1, button)
    def released(self, button=None): return self.read(2, button)
    def changed(self, button=None): return self.read(3, button)

def no_hardware(*args, **kwargs):
    raise NotImplementedError('This API needs the physical badge; see playground/CAPABILITIES.md')

class Unsupported:
    def __getattr__(self, key):
        return no_hardware()

class ImuRangePreview:
    """Synthetic bookkeeping for two verified IMU range settings, not I2C.

    Host samples keep stock-equivalent physical units. The register selection
    changes only the counts returned by badge.imu(); no driver, timing, sample
    noise or other sensor registers are emulated.
    """
    def __init__(self):
        self._registers = {0x10: 0x40, 0x11: 0x40}

    def _check(self, address, register, size, addrsize):
        if address != 0x6a or register not in self._registers or size != 1 or addrsize != 8:
            raise NotImplementedError('Preview supports only one-byte IMU range registers 0x6A:0x10/0x11; see playground/CAPABILITIES.md')

    def readfrom_mem(self, address, register, size, *, addrsize=8):
        self._check(address, register, size, addrsize)
        return bytes((self._registers[register],))

    def writeto_mem(self, address, register, data, *, addrsize=8):
        self._check(address, register, len(data), addrsize)
        if data[0] not in (0x40, 0x48):
            raise NotImplementedError('Preview supports only stock and Motion Lab wide IMU ranges; see playground/CAPABILITIES.md')
        self._registers[register] = data[0]

    def sample(self):
        accel_divisor = 2 if self._registers[0x10] == 0x48 else 1
        gyro_divisor = 4 if self._registers[0x11] == 0x48 else 1
        return tuple(max(-32768, min(32767, int(round(value /
                     (accel_divisor if index < 3 else gyro_divisor)))))
                     for index, value in enumerate(_input.imu_value))

    def __getattr__(self, key):
        return no_hardware()

def install():
    # Explicitly override frozen Tufty input/display modules before importing
    # upstream Badgeware. Remaining native graphics run unchanged in WASM.
    import badgeware
    for key in ('LEFT', 'RIGHT', 'SELECT', 'BACK', 'MENU', 'UP', 'DOWN', 'HOME'):
        setattr(builtins, 'BUTTON_' + key, key)
        setattr(badgeware, 'BUTTON_' + key, key)
    badge._internal_i2c = ImuRangePreview()
    badge.imu = badge._internal_i2c.sample
    badge.buttons = Source('buttons')
    badge.pad = Source('pad')
    badge.all = badge
    badge.autorotate = no_hardware
    badge.rotate_180 = no_hardware
    badge.upside_down = no_hardware
    badge.imu_upright = no_hardware
    # Firmware touched() returns opaque action objects; this extra API is hardware-only.
    badge.touched = no_hardware
    badge.sleep = no_hardware
    badge.wake_reason = no_hardware
    badge.disk_free = no_hardware
    for name in ('battery_voltage', 'battery_level', 'usb_connected', 'is_charging', 'light_level', 'caselights', 'woken_by_button', 'pressed_to_wake', 'woken_by_reset'):
        setattr(badge, name, no_hardware)
    for key in ('BUTTON_A', 'BUTTON_B', 'BUTTON_C'):
        if hasattr(builtins, key): delattr(builtins, key)
    builtins.run = Run
    badgeware.run = Run
    badgeware._run = Run
    builtins.launch = no_hardware
    badgeware.launch = no_hardware
    builtins.reset = no_hardware
    badgeware.reset = no_hardware
    # Keep screen in builtins so mode() can replace it. Exporting a screen
    # snapshot via `from badgeware import *` would break resolution changes.
    # These frozen upstream transports would otherwise silently use browser
    # networking. This profile deliberately reports unsupported operations.
    for name in ('pico3d', 'bluetooth', 'network', 'requests', 'urequests', 'wifi', 'fetch', 'aye_arr', 'sparkle', 'badgeware.link'):
        if name == 'pico3d' and sim_host.native_pico3d(): continue
        sys.modules[name] = Unsupported()
    badge.poll()

def launch_app(slug):
    global app
    import os
    path = '/system/apps/' + slug
    os.chdir(path)
    sys.path.insert(0, path)
    app = __import__(path)
    if active is None or active.update is None:
        raise RuntimeError('App must register run(update)')

def step():
    global finished, reason
    if finished:
        return
    # Like run(update): logic reads input latched by the previous end-of-frame poll.
    # The launcher's HOME/BOOT exit is a physical button. Recorded Universe
    # capacitive HOME events remain app input (Roadster uses it for scenes).
    if badge.buttons.held(BUTTON_HOME):
        on_exit = getattr(app, 'on_exit', None)
        if callable(on_exit): on_exit()
        finished, reason = True, 'home'
        return
    badge.clear()
    result = active.update()
    if result is not None:
        active.result = result
        finished, reason = True, 'result'
        on_exit = getattr(app, 'on_exit', None)
        if callable(on_exit): on_exit()
        return
    display.update()
    badge.poll()
    if active.duration is not None and active.ticks >= active.duration:
        finished, reason = True, 'duration'
        on_exit = getattr(app, 'on_exit', None)
        if callable(on_exit): on_exit()

def inspect(expression):
    if app is None: raise RuntimeError('no app')
    return json.dumps(eval(expression, app.__dict__))
