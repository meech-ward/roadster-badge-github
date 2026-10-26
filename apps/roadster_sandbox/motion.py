"""Portable neutral-grip tilt and proportional motion-jet experiment.

Default LSM6DS3TR-C conversion: 16384 accel counts/g and 0.00875 dps/count.
An app using a verified wider range supplies the matching conversion scales.
Positive X means right; positive Y means up. Directions describe requested
jets, never a scripted trick. Calibration builds tangent axes perpendicular to
gravity, so a comfortable upright grip remains sensitive to forward/back tilt.
The default +Z neutral sample maps accel +X to right, +Y to up, gyro -Y to
right and gyro +X to up. set_axes() changes raw-axis preference and signs.

All settings here are deliberately provisional for hands-on tuning: 4 degree
tilt dead zone, full tilt at 28 degrees. Horizontal flicks span 160--500 dps
or .75--1.60 g; vertical flicks span 180--360 dps or .80--1.50 g. Ordinary
forward/back positioning stays below the vertical onset; a deliberate stroke
requests a committed flip without changing continuous tilt sensitivity. The
vertical range remains narrower to accommodate forward/back wrist movement. Full
horizontal angular output requires a wider gyro range than the stock setting.
A flick requests a proportional 220 ms pulse,
followed by a quiet interval before another pulse. After settling, motion has
350 ms to cross the flick threshold, allowing a natural acceleration ramp.
Releasing the arm control clears pulses immediately. No hardware access,
rendering or game dependencies.

An app can opt into vertical_intent_ms (200 ms is a useful first trial). A
predominantly vertical stroke then waits before producing any flick force. One
quick, substantial reversal replaces that unfinished stroke, allowing down/up
preparation; the final stroke gets 80 ms to gather its peak. This is a bounded
heuristic, not intent detection: longer preparation still requires releasing
the arm control, and a strong recoil inside the window can look like a reversal.
Horizontal flicks and continuous tilt retain their immediate response.

At intervals above 150 ms there is too little timing evidence for gestures.
Those polls discard all flick history and never integrate the current gyro over
missing time. A gravity-like current accelerometer sample still supplies fresh
continuous tilt, including while the badge is moving. Quiet samples allow
.7--1.3 g; at 45 dps or above the sample must also remain within 15 percent of
the calibrated gravity magnitude. Clipping, freefall and implausible magnitudes
supply no control until a valid sample arrives. This is a plausibility filter,
not a complete separation of gravity and translation: acceleration with a
gravity-like magnitude can still resemble tilt. Slow calibration remains quiet
only, counts at most 100 ms per observation, and restarts after a gap above one
second.
"""

import math

ACCEL_SCALE = 1.0 / 16384.0
GYRO_SCALE = 0.00875
RAD = math.pi / 180.0
CALIBRATION_MS = 1000
MAX_DT_MS = 150
CALIBRATION_GAP_MS = 1000
SLOW_CALIBRATION_CREDIT_MS = 100
_INFINITY = float('inf')
TILT_DEAD_DEG = 4.0
TILT_FULL_DEG = 28.0
FLICK_DEAD_DPS = (160.0, 180.0)
FLICK_FULL_DPS = (500.0, 360.0)
LINEAR_DEAD_G = (0.75, 0.80)
LINEAR_FULL_G = (1.60, 1.50)
SENSOR_RAIL = 32760
BURST_MS = 220.0
PEAK_WINDOW_MS = 80.0
SETTLE_MS = 180.0
COOLDOWN_MS = 300.0
ONSET_MS = 350.0
REVERSE_PEAK_MS = 80.0
REVERSE_MIN = .2
REVERSE_RATIO = .75
# Regular-cadence update() expands the vector helpers inline. These bound
# names and precomputed ranges give bit-identical results to the helpers
# (the same operands in the same order) without per-poll lookups.
_sqrt = math.sqrt
_atan2 = math.atan2
_TILT_RANGE = TILT_FULL_DEG - TILT_DEAD_DEG
_FLICK_RANGE = (FLICK_FULL_DPS[0] - FLICK_DEAD_DPS[0], FLICK_FULL_DPS[1] - FLICK_DEAD_DPS[1])
_LINEAR_RANGE = (LINEAR_FULL_G[0] - LINEAR_DEAD_G[0], LINEAR_FULL_G[1] - LINEAR_DEAD_G[1])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    length = _length(a)
    return (a[0] / length, a[1] / length, a[2] / length) if length > 0.000001 else (0., 0., 1.)


def _scaled(value, dead, full):
    amount = min(1., max(0., (abs(value) - dead) / (full - dead)))
    return -amount if value < 0 else amount


class MotionController:
    def __init__(self, horizontal_axis=0, horizontal_sign=1,
                 vertical_axis=1, vertical_sign=1, *,
                 accel_scale=ACCEL_SCALE, gyro_scale=GYRO_SCALE,
                 vertical_intent_ms=0.):
        if not 0. < accel_scale < float('inf') or not 0. < gyro_scale < float('inf'):
            raise ValueError("Motion conversion scales must be finite and positive")
        if not 0. <= vertical_intent_ms <= 350.:
            raise ValueError("Vertical intent window must be 0 to 350 ms")
        self.accel_scale = float(accel_scale)
        self.gyro_scale = float(gyro_scale)
        self.vertical_intent_ms = float(vertical_intent_ms)
        self.sensor_clipped = False
        self.mode = "both"
        self.horizontal_axis = 0
        self.horizontal_sign = 1
        self.vertical_axis = 1
        self.vertical_sign = 1
        self.event_count = 0
        self.last_flick = {"x": 0., "y": 0., "kind": "none"}
        self.peak = 0.
        self.set_axes(horizontal_axis, horizontal_sign, vertical_axis, vertical_sign)

    def set_axes(self, horizontal_axis=0, horizontal_sign=1,
                 vertical_axis=1, vertical_sign=1):
        if horizontal_axis not in (0, 1, 2) or vertical_axis not in (0, 1, 2):
            raise ValueError("Motion axes must be 0, 1 or 2")
        if horizontal_axis == vertical_axis:
            raise ValueError("Motion axes must differ")
        if horizontal_sign not in (-1, 1) or vertical_sign not in (-1, 1):
            raise ValueError("Motion signs must be -1 or 1")
        self.horizontal_axis, self.horizontal_sign = horizontal_axis, horizontal_sign
        self.vertical_axis, self.vertical_sign = vertical_axis, vertical_sign
        self.start_calibration()

    def _clear_pulses(self):
        self._burst = [0., 0.]
        self._burst_age = BURST_MS
        self._quiet_ms = 0.
        self._since_flick_ms = COOLDOWN_MS
        self._flick_ready = False
        self._onset_ms = -1.
        self._pending_y = 0.
        self._pending_ms = 0.
        self._pending_reversed = False
        self._pending_kind = "none"
        self.flick_x = self.flick_y = 0.

    def _commit_flick(self, candidate, kind):
        self._burst = list(candidate)
        self._burst_age = self._since_flick_ms = self._quiet_ms = 0.
        self._flick_ready = False
        self._onset_ms = -1.
        self._pending_y = self._pending_ms = 0.
        self.event_count += 1
        self.last_flick = {"x": candidate[0], "y": candidate[1], "kind": kind}

    def start_calibration(self):
        self.calibrated = False
        self.calibration_ms = 0.
        self._cal_accel = [0., 0., 0.]
        self._cal_gyro = [0., 0., 0.]
        self._cal_reference = None
        self.neutral = (0., 0., 1.)
        self.gravity = self.neutral
        self.gravity_g = 1.
        self.gyro_bias = (0., 0., 0.)
        self.basis_x = (1., 0., 0.)
        self.basis_y = (0., 1., 0.)
        self.tilt_x = self.tilt_y = 0.
        self.angle_x = self.angle_y = 0.
        self.rate_x = self.rate_y = 0.
        self.linear_x = self.linear_y = 0.
        self.armed = False
        self.tilt_valid = False
        self.channels = {"left": 0., "right": 0., "up": 0., "down": 0.}
        self._clear_pulses()

    def set_mode(self, mode):
        if mode not in ("tilt", "flick", "both"):
            raise ValueError("Motion mode must be tilt, flick or both")
        self.mode = mode
        self._clear_pulses()
        self.channels = {"left": 0., "right": 0., "up": 0., "down": 0.}

    def _calibrate(self, accel, gyro, dt):
        magnitude = _length(accel)
        stable = .7 <= magnitude <= 1.3 and max(abs(value) for value in gyro) < 45.
        if self._cal_reference is not None:
            a0, w0 = self._cal_reference
            stable = stable and _length(tuple(accel[i] - a0[i] for i in range(3))) < .06
            stable = stable and max(abs(gyro[i] - w0[i]) for i in range(3)) < 8.
        if not stable:
            self.calibration_ms = 0.
            self._cal_accel = [0., 0., 0.]
            self._cal_gyro = [0., 0., 0.]
            self._cal_reference = None
            return
        if self._cal_reference is None:
            self._cal_reference = (accel, gyro)
        for i in range(3):
            self._cal_accel[i] += accel[i] * dt
            self._cal_gyro[i] += gyro[i] * dt
        self.calibration_ms += dt
        if self.calibration_ms < CALIBRATION_MS:
            return
        mean = tuple(value / self.calibration_ms for value in self._cal_accel)
        self.gravity_g = _length(mean)
        self.neutral = _unit(mean)
        self.gravity = mean
        self.gyro_bias = tuple(value / self.calibration_ms for value in self._cal_gyro)
        # Project the preferred horizontal sensor axis into the gravity tangent
        # plane. If that axis is vertical, use the least vertical sensor axis.
        axis = self.horizontal_axis
        if abs(self.neutral[axis]) > .94:
            axis = min(range(3), key=lambda i: abs(self.neutral[i]))
        horizontal = _unit(tuple((1. if i == axis else 0.) -
                                 self.neutral[axis] * self.neutral[i] for i in range(3)))
        vertical = _cross(self.neutral, horizontal)
        # Respect preferred vertical direction when observable. Near upright,
        # the cross product supplies the missing forward/back component.
        if vertical[self.vertical_axis] < -.2:
            vertical = tuple(-value for value in vertical)
        self.basis_x = tuple(value * self.horizontal_sign for value in horizontal)
        self.basis_y = tuple(value * self.vertical_sign for value in vertical)
        self.calibrated = True

    def update(self, imu, dt_ms, armed=False):
        # A near-rail sample cannot reveal how far the real motion exceeded
        # the sensor range. Expose that limitation; never invent its peak.
        self.sensor_clipped = (abs(imu[0]) >= SENSOR_RAIL or abs(imu[1]) >= SENSOR_RAIL or
                               abs(imu[2]) >= SENSOR_RAIL or abs(imu[3]) >= SENSOR_RAIL or
                               abs(imu[4]) >= SENSOR_RAIL or abs(imu[5]) >= SENSOR_RAIL)
        # These vectors always have three components. Direct expressions avoid
        # allocating a generator and its iteration state on every sensor poll.
        accel_scale, gyro_scale = self.accel_scale, self.gyro_scale
        accel = (float(imu[0]) * accel_scale, float(imu[1]) * accel_scale,
                 float(imu[2]) * accel_scale)
        gyro = (float(imu[3]) * gyro_scale, float(imu[4]) * gyro_scale,
                float(imu[5]) * gyro_scale)
        dt_ms = float(dt_ms)
        self.tilt_valid = False
        if self.sensor_clipped:
            # A clipped angular sample is not a measurable rotation. Do not
            # let it corrupt gravity or queue a pulse that becomes visible on
            # the next valid poll, after the clipping warning has disappeared.
            self._clear_pulses()
            self.armed = False
            self.channels = {"left": 0., "right": 0., "up": 0., "down": 0.}
            if not self.calibrated:
                self.start_calibration()
            return self.snapshot()
        if not 0. < dt_ms < _INFINITY:
            # Invalid timing cannot provide control or calibration evidence.
            self._clear_pulses()
            self.armed = False
            self.channels = {"left": 0., "right": 0., "up": 0., "down": 0.}
            if not self.calibrated:
                self.start_calibration()
            elif .5 < _length(accel) < 1.5:
                unit = _unit(accel)
                gravity_g = self.gravity_g
                self.gravity = (unit[0] * gravity_g, unit[1] * gravity_g,
                                unit[2] * gravity_g)
            return self.snapshot()
        if dt_ms > MAX_DT_MS:
            # The current sample may describe a held tilt, but cannot describe
            # all movement since the preceding poll. Never extrapolate a gyro
            # sample across that gap or replay an unfinished flick afterwards.
            self._clear_pulses()
            self.armed = False
            self.channels = {"left": 0., "right": 0., "up": 0., "down": 0.}
            if not self.calibrated:
                if dt_ms > CALIBRATION_GAP_MS:
                    self.start_calibration()
                else:
                    self._calibrate(accel, gyro, SLOW_CALIBRATION_CREDIT_MS)
                return self.snapshot()
            bias = self.gyro_bias
            omega = (gyro[0] - bias[0], gyro[1] - bias[1], gyro[2] - bias[2])
            motion = _cross(_unit(self.gravity), omega)
            self.rate_x = _dot(motion, self.basis_x)
            self.rate_y = _dot(motion, self.basis_y)
            self.linear_x = self.linear_y = 0.
            # A slow frame must not disable tilt just because the player is
            # moving. Use only the current gravity-like sample, with tighter
            # magnitude confidence during motion; never extrapolate its gyro
            # over missing time or restore a pending flick. Translation can
            # still imitate gravity, so this is deliberately a plausibility
            # bound rather than a claim that acceleration is fully separated.
            magnitude = _length(accel)
            moving = max(abs(omega[0]), abs(omega[1]), abs(omega[2])) >= 45.
            if (self.sensor_clipped or not .7 <= magnitude <= 1.3 or
                    (moving and not .85*self.gravity_g <= magnitude <= 1.15*self.gravity_g)):
                return self.snapshot()
            unit = _unit(accel)
            gravity_g = self.gravity_g
            self.gravity = (unit[0] * gravity_g, unit[1] * gravity_g,
                            unit[2] * gravity_g)
            along_neutral = _dot(unit, self.neutral)
            self.angle_x = math.atan2(_dot(unit, self.basis_x), along_neutral) / RAD
            self.angle_y = math.atan2(_dot(unit, self.basis_y), along_neutral) / RAD
            self.tilt_x = _scaled(self.angle_x, TILT_DEAD_DEG, TILT_FULL_DEG)
            self.tilt_y = _scaled(self.angle_y, TILT_DEAD_DEG, TILT_FULL_DEG)
            self.tilt_valid = True
            self.armed = bool(armed)
            if self.armed and self.mode != "flick":
                self.channels = {"left": max(0., -self.tilt_x),
                                 "right": max(0., self.tilt_x),
                                 "up": max(0., self.tilt_y),
                                 "down": max(0., -self.tilt_y)}
                self.peak = max(self.peak, max(self.channels.values()))
            return self.snapshot()
        if not self.calibrated:
            self._calibrate(accel, gyro, dt_ms)
            return self.snapshot()
        dt = dt_ms / 1000.
        # _unit, _cross, _dot, _length and _scaled are expanded inline here
        # with their exact operation order, so every result is bit-identical
        # while each poll avoids about thirty Python calls and their tuples.
        bias = self.gyro_bias
        wx, wy, wz = gyro[0] - bias[0], gyro[1] - bias[1], gyro[2] - bias[2]
        gx, gy, gz = self.gravity
        length = _sqrt(gx * gx + gy * gy + gz * gz)
        if length > 0.000001:
            gx, gy, gz = gx / length, gy / length, gz / length
        else:
            gx, gy, gz = 0., 0., 1.
        mx = gy * wz - gz * wy
        my = gz * wx - gx * wz
        mz = gx * wy - gy * wx
        bx = self.basis_x
        by = self.basis_y
        rate_x = mx * bx[0] + my * bx[1] + mz * bx[2]
        rate_y = mx * by[0] + my * by[1] + mz * by[2]
        self.rate_x = rate_x
        self.rate_y = rate_y
        px = gx + mx * RAD * dt
        py = gy + my * RAD * dt
        pz = gz + mz * RAD * dt
        length = _sqrt(px * px + py * py + pz * pz)
        if length > 0.000001:
            px, py, pz = px / length, py / length, pz / length
        else:
            px, py, pz = 0., 0., 1.
        gravity_g = self.gravity_g
        px, py, pz = px * gravity_g, py * gravity_g, pz * gravity_g
        ax, ay, az = accel
        rx, ry, rz = ax - px, ay - py, az - pz
        linear_x = rx * bx[0] + ry * bx[1] + rz * bx[2]
        linear_y = rx * by[0] + ry * by[1] + rz * by[2]
        self.linear_x = linear_x
        self.linear_y = linear_y
        # Gyro predicts gravity through a flick. Slow accelerometer correction
        # removes drift, but a large acceleration does not immediately become tilt.
        tau = .2 if _sqrt(rx * rx + ry * ry + rz * rz) < .22 else .9
        blend = dt / (tau + dt) if .5 < _sqrt(ax * ax + ay * ay + az * az) < 1.5 else 0.
        cx = px + rx * blend
        cy = py + ry * blend
        cz = pz + rz * blend
        length = _sqrt(cx * cx + cy * cy + cz * cz)
        if length > 0.000001:
            cx, cy, cz = cx / length, cy / length, cz / length
        else:
            cx, cy, cz = 0., 0., 1.
        gx, gy, gz = cx * gravity_g, cy * gravity_g, cz * gravity_g
        self.gravity = (gx, gy, gz)
        length = _sqrt(gx * gx + gy * gy + gz * gz)
        if length > 0.000001:
            gx, gy, gz = gx / length, gy / length, gz / length
        else:
            gx, gy, gz = 0., 0., 1.
        neutral = self.neutral
        along_neutral = gx * neutral[0] + gy * neutral[1] + gz * neutral[2]
        angle = _atan2(gx * bx[0] + gy * bx[1] + gz * bx[2], along_neutral) / RAD
        self.angle_x = angle
        amount = min(1., max(0., (abs(angle) - TILT_DEAD_DEG) / _TILT_RANGE))
        self.tilt_x = -amount if angle < 0 else amount
        angle = _atan2(gx * by[0] + gy * by[1] + gz * by[2], along_neutral) / RAD
        self.angle_y = angle
        amount = min(1., max(0., (abs(angle) - TILT_DEAD_DEG) / _TILT_RANGE))
        self.tilt_y = -amount if angle < 0 else amount
        self.tilt_valid = True

        was_armed = self.armed
        self.armed = bool(armed)
        if not self.armed or not was_armed:
            self._clear_pulses()
        amount = min(1., max(0., (abs(rate_x) - FLICK_DEAD_DPS[0]) / _FLICK_RANGE[0]))
        angular_x = -amount if rate_x < 0 else amount
        amount = min(1., max(0., (abs(rate_y) - FLICK_DEAD_DPS[1]) / _FLICK_RANGE[1]))
        angular_y = -amount if rate_y < 0 else amount
        amount = min(1., max(0., (abs(linear_x) - LINEAR_DEAD_G[0]) / _LINEAR_RANGE[0]))
        scaled_x = -amount if linear_x < 0 else amount
        amount = min(1., max(0., (abs(linear_y) - LINEAR_DEAD_G[1]) / _LINEAR_RANGE[1]))
        scaled_y = -amount if linear_y < 0 else amount
        angular = (angular_x, angular_y)
        linear = (scaled_x, scaled_y)
        candidate = (angular[0] if abs(angular[0]) >= abs(linear[0]) else linear[0],
                     angular[1] if abs(angular[1]) >= abs(linear[1]) else linear[1])
        if self.armed:
            self._burst_age += dt_ms
            self._since_flick_ms += dt_ms
            quiet = max(abs(self.rate_x), abs(self.rate_y)) < 25.
            quiet = quiet and max(abs(self.linear_x), abs(self.linear_y)) < .12
            if quiet:
                self._quiet_ms += dt_ms
            else:
                self._quiet_ms = 0.
            if (not self._pending_y and self._quiet_ms >= SETTLE_MS and
                    self._since_flick_ms >= COOLDOWN_MS):
                self._flick_ready = True
                self._onset_ms = -1.
            # Latch readiness across the acceleration from quiet to a flick.
            # Resetting it on the first non-quiet sample loses ordinary flicks
            # that pass through the quiet-to-flick gap before triggering.
            # An onset window still rejects a long gradual movement, and a
            # triggered pulse consumes readiness until another quiet interval.
            if self._flick_ready and not quiet and self._onset_ms < 0:
                self._onset_ms = 0.
            if self._flick_ready and self._onset_ms >= 0:
                self._onset_ms += dt_ms
                if self._onset_ms > ONSET_MS:
                    self._flick_ready = False
            if self._pending_y:
                self._pending_ms -= dt_ms
                vertical = candidate[1]
                if vertical * self._pending_y > 0 and abs(vertical) > abs(self._pending_y):
                    self._pending_y = vertical
                    self._pending_kind = "angular" if abs(angular[1]) >= abs(linear[1]) else "linear"
                elif (not self._pending_reversed and vertical * self._pending_y < 0 and
                      abs(vertical) >= max(REVERSE_MIN, abs(self._pending_y) * REVERSE_RATIO)):
                    # No force has been applied yet, so replacing this stroke
                    # does not require undoing physics. Accept just one reversal
                    # and commit after a fresh, short peak collection window.
                    self._pending_y = vertical
                    self._pending_reversed = True
                    self._pending_ms = REVERSE_PEAK_MS
                    self._pending_kind = "angular" if abs(angular[1]) >= abs(linear[1]) else "linear"
                if self._pending_ms <= 0:
                    self._commit_flick((0., self._pending_y), self._pending_kind)
            elif self._flick_ready and max(abs(candidate[0]), abs(candidate[1])) > 0:
                kind = ("angular" if max(abs(angular[0]), abs(angular[1])) >=
                        max(abs(linear[0]), abs(linear[1])) else "linear")
                if self.vertical_intent_ms and abs(candidate[1]) > abs(candidate[0]):
                    self._pending_y = candidate[1]
                    self._pending_ms = self.vertical_intent_ms
                    self._pending_reversed = False
                    self._pending_kind = "angular" if abs(angular[1]) >= abs(linear[1]) else "linear"
                    self._flick_ready = False
                    self._onset_ms = -1.
                    self._quiet_ms = 0.
                else:
                    self._commit_flick(candidate, kind)
            elif self._burst_age < PEAK_WINDOW_MS:
                for i in range(2):
                    if candidate[i] * self._burst[i] > 0 and abs(candidate[i]) > abs(self._burst[i]):
                        self._burst[i] = candidate[i]
                        self.last_flick["x" if i == 0 else "y"] = candidate[i]
            decay = max(0., 1. - self._burst_age / BURST_MS)
            self.flick_x, self.flick_y = (self._burst[0] * decay, self._burst[1] * decay)

        # Independent directional requests: opposing inputs may request opposing
        # jets; eventual vehicle physics decides their combined force/torque.
        if self.armed:
            mode = self.mode
            tilt_x = self.tilt_x if mode != "flick" else 0.
            tilt_y = self.tilt_y if mode != "flick" else 0.
            flick_x = self.flick_x if mode != "tilt" else 0.
            flick_y = self.flick_y if mode != "tilt" else 0.
            left = max(0., -tilt_x, -flick_x)
            right = max(0., tilt_x, flick_x)
            up = max(0., tilt_y, flick_y)
            down = max(0., -tilt_y, -flick_y)
        else:
            left = right = up = down = 0.
        self.channels = {"left": left, "right": right, "up": up, "down": down}
        self.peak = max(self.peak, max(left, right, up, down))
        return self.snapshot()

    def snapshot(self):
        return {
            "calibrated": self.calibrated,
            "accel_scale": self.accel_scale, "gyro_scale": self.gyro_scale,
            "sensor_clipped": self.sensor_clipped,
            "calibration_progress": min(1., self.calibration_ms / CALIBRATION_MS),
            "mode": self.mode, "armed": self.armed,
            "tilt_valid": self.tilt_valid,
            "tilt_x": self.tilt_x, "tilt_y": self.tilt_y,
            "angle_x": self.angle_x, "angle_y": self.angle_y,
            "rate_x": self.rate_x, "rate_y": self.rate_y,
            "linear_x": self.linear_x, "linear_y": self.linear_y,
            "flick_x": self.flick_x, "flick_y": self.flick_y,
            "flick_ready": self._flick_ready,
            "flick_active": bool(self.flick_x or self.flick_y),
            "flick_pending": bool(self._pending_y),
            "pending_flick_y": self._pending_y,
            "pending_flick_ms": self._pending_ms,
            "flick_cooldown_ms": max(0., COOLDOWN_MS - self._since_flick_ms),
            "channels": self.channels.copy(), "event_count": self.event_count,
            "flick_count": self.event_count, "last_flick": self.last_flick.copy(),
            "peak": self.peak, "basis_x": self.basis_x, "basis_y": self.basis_y,
            "neutral": self.neutral, "gyro_bias": self.gyro_bias,
        }
