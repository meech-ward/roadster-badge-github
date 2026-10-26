"""Playing-grip calibration and reversible sensor setup for the real controller."""
from badgeware import *
from motion import MotionController
from imu_profile import MotionImuProfile


class BadgeMotion:
    def __init__(self):
        self.phase = 'off'
        self.error = ''
        self.controller = None
        self.profile = None
        self.orientation_available = False
        self.original_auto = self.original_rotation = self.original_upright = None
        self.rotated = False
        self.model = {}

    def start(self):
        if self.phase != 'off':
            return
        try:
            try:
                self.original_auto = bool(badge.autorotate())
                self.original_rotation = bool(badge.rotate_180())
                badge.autorotate(True)
                self.rotated = bool(badge.rotate_180())
                self.orientation_available = True
            except NotImplementedError:
                # The preview explicitly cannot validate screen orientation.
                self.orientation_available = False
            self.profile = MotionImuProfile(badge._internal_i2c)
            self.profile.enable()
            if self.orientation_available:
                self.original_upright = badge.imu_upright()
                old_scale = (1. / 16384., 1. / 2048., 1. / 8192., 1. / 4096.)[
                    (self.profile.original_regs[0] >> 2) & 3]
                badge.imu_upright(tuple(v * old_scale / self.profile.accel_scale
                                       for v in self.original_upright))
            self.controller = MotionController(accel_scale=self.profile.accel_scale,
                gyro_scale=self.profile.gyro_scale, vertical_intent_ms=200)
            self.controller.set_mode('both')
            self.model = self.controller.snapshot()
            self.phase = 'center'
        except Exception as error:
            self.error = str(error)
            try:
                self.close()
            except Exception as cleanup_error:
                self.error += '; restore: ' + str(cleanup_error)
            self.phase = 'error'

    def center(self):
        self.start()
        if self.phase == 'error':
            return
        if self.orientation_available:
            badge.autorotate(True)
            self.rotated = bool(badge.rotate_180())
        sign = -1 if self.rotated else 1
        self.controller.set_axes(0, sign, 1, sign)
        self.controller.start_calibration()
        self.phase = 'calibrating'

    def update(self, dt, armed):
        if self.controller is None or self.phase in ('off', 'error', 'center'):
            return self.model
        if self.phase == 'calibrating' and self.orientation_available:
            current = bool(badge.rotate_180())
            if current != self.rotated:
                self.rotated = current
                sign = -1 if current else 1
                self.controller.set_axes(0, sign, 1, sign)
                self.controller.start_calibration()
        self.model = self.controller.update(tuple(badge.imu()), dt,
                                            armed=armed and self.phase == 'ready')
        if self.phase == 'calibrating' and self.model['calibrated']:
            self.phase = 'ready'
            if self.orientation_available:
                badge.autorotate(False)
        return self.model

    def set_mode(self, mode):
        self.start()
        if self.controller is not None:
            self.controller.set_mode(mode)
            self.model = self.controller.snapshot()

    def snapshot(self):
        return {'phase': self.phase, 'error': self.error, 'model': self.model,
                'orientation_available': self.orientation_available,
                'rotated': self.rotated,
                'sensor_profile': self.profile.snapshot() if self.profile else None}

    def close(self):
        try:
            if self.profile is not None:
                self.profile.restore()
        finally:
            if self.original_upright is not None:
                badge.imu_upright(self.original_upright)
            if self.orientation_available:
                badge.autorotate(self.original_auto)
                if not self.original_auto:
                    badge.rotate_180(self.original_rotation)
