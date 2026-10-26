"""Portable badge inputs to ordinary Roadster forces.

This mixer knows logical touch and clicky names and motion strength only. It
does not read hardware, schedule a trick, or change the car's pose or velocity.
SELECT is rear boost and BACK is lift. The app arms calibrated airborne motion;
MENU and calibration belong to the app. The fan command stays independent.
"""
from math import atan2, sqrt
from physics import (NOZZLES, INERTIA, GRAVITY, GROUND_Y, clamp, cross,
                     number, scale, q_inverse_rotate)
from vehicle_spec import REFERENCE_MASS, supply_fraction

SIDE_ASSIST = .12
REVERSE_BRAKE_SPEED = .5
_TORQUES = tuple(cross(n[1], scale(n[2], n[3])) for n in NOZZLES)
_ROLL_TORQUE = _TORQUES[4][2] + _TORQUES[6][2] + SIDE_ASSIST*_TORQUES[8][2]
_PITCH_TORQUE = -(_TORQUES[4][0] + _TORQUES[5][0])
_LIFT_FORCE = sum(n[3] for n in NOZZLES[4:8])
LIFT_CEILING = 16.  # practice hover, metres above resting ground center
LIFT_MAX_SPEED = 10.  # m/s; smoothly bounded target, actual force stays finite
LIFT_HEIGHT_GAIN = 1.  # near hover, each metre of error requests 1 m/s
LIFT_SPEED_GAIN = 4.  # critically damped near hover: 4 * LIFT_HEIGHT_GAIN


def lift_target_speed(height):
    """Held lift's world-up speed envelope, in metres and seconds.

    Smoothly bound a height-error controller, without bands or discontinuities.
    Near hover the position/velocity feedback is critically damped. Farther
    away it asks for a bounded speed instead of an ever-stronger acceleration.
    Gravity brakes an overshoot; other thrust and momentum remain physical.
    """
    error = LIFT_CEILING-height
    scale_height = LIFT_MAX_SPEED/LIFT_HEIGHT_GAIN
    return LIFT_MAX_SPEED*error/sqrt(error*error+scale_height*scale_height)


def held_lift_common(sim):
    """Limit only requested common lift through the existing four jets.

    No retained launch height, pose/velocity edits or extra force. The mixer
    keeps the differential torque required for rotation even at the ceiling.
    Sideways/downward jets retain their ordinary behavior; at up <= .25 even
    full lift cannot support the car's minimum mass against gravity.
    """
    up = sim.basis()[1][1]
    if up <= .25:
        return 1.
    factor = supply_fraction(sim.air_mass)
    if factor <= 0.:
        return 1.  # No pressure: the physics engine still delivers no force.
    acceleration = (lift_target_speed(sim.y-GROUND_Y)-sim.vy)*LIFT_SPEED_GAIN
    force = sim.mass*(GRAVITY+acceleration)/up+sim.fan_force
    return clamp(force/(_LIFT_FORCE*factor), 0., 1.)


def _unit(value):
    return clamp(number(value), 0., 1.)


NO_MOTION = (0., 0., 0., 0.)


def prepare_motion(motion=None, enabled=True):
    """Normalize one poll into tilt roll/pitch, then flick roll/pitch.

    The app determines when motion is armed, including free flight without a
    held thrust pad. Snapshots are copied into four numbers so fixed-tick rate
    feedback can reuse the poll without reparsing or retaining mutable dicts.
    """
    if (not enabled or not motion or not motion.get('calibrated', False) or
            not motion.get('armed', False) or not motion.get('tilt_valid', True) or
            motion.get('sensor_clipped', False)):
        return NO_MOTION
    mode = motion.get('mode', 'both')
    tilt_x = tilt_y = flick_x = flick_y = 0.
    if mode in ('both', 'tilt'):
        tilt_x = clamp(number(motion.get('tilt_x')), -1., 1.)
        tilt_y = clamp(number(motion.get('tilt_y')), -1., 1.)
        # Continuous tilt is authoritative now. The vertical recognizer may
        # delay an extra flick burst while resolving a preparatory reversal,
        # but must never swallow a visible pitch command in Both mode.
    if mode in ('both', 'flick'):
        flick_x = clamp(number(motion.get('flick_x')), -1., 1.)
        flick_y = clamp(number(motion.get('flick_y')), -1., 1.)
    return tilt_x, tilt_y, flick_x, flick_y


def rotation_axes(buttons=(), motion=None, manual=()):
    roll = float(('RIGHT' in manual) - ('LEFT' in manual))
    pitch = float(('UP' in manual) - ('DOWN' in manual))
    if ('SELECT' in buttons or 'BACK' in buttons) and motion:
        channels = motion.get('channels', motion)
        if motion.get('calibrated', True) and motion.get('armed', True):
            roll += _unit(channels.get('right', 0.)) - _unit(channels.get('left', 0.))
            pitch += _unit(channels.get('up', 0.)) - _unit(channels.get('down', 0.))
    return roll, pitch


def mix_controls(buttons=(), motion=None, fan=0., speed=0., manual=(), axes=None,
                 lift_common=1.):
    """Return Simulation.step controls from normalized logical button names.

    ``motion`` accepts a MotionController snapshot or its four-channel dict.
    Positive right-minus-left requests right roll; up-minus-down requests nose
    up. Opposing channels cancel. ``speed`` is signed forward speed in m/s:
    DOWN brakes forward motion, then drives in reverse; both UP and DOWN brake.
    Uncalibrated/disarmed snapshots and released thrust buttons cannot fire
    stale motion. A thrust button itself remains usable during calibration.
    ``manual`` is the separate clicky directional input: LEFT/RIGHT roll and
    UP/DOWN pitch. It needs no motion calibration or held touch thrust button.
    """
    up, down = 'UP' in buttons, 'DOWN' in buttons
    boost, lift = 'SELECT' in buttons, 'BACK' in buttons
    throttle, brake = 0., 0.
    if up and down:
        brake = 1.
    elif up:
        throttle = 1.
    elif down:
        if number(speed) > REVERSE_BRAKE_SPEED:
            brake = 1.
        else:
            throttle = -1.

    roll, pitch = rotation_axes(buttons, motion, manual) if axes is None else axes

    # Share the finite lift-nozzle range between roll and pitch. At full
    # diagonal input both axes retain half their individual torque authority.
    # With lift held, reduce the opposing pair instead of hiding the motion
    # under four already-saturated jets. A climb governor may reduce only the
    # common duty; the minimum retains exactly the same roll/pitch torque.
    extent = abs(roll) + abs(pitch)
    divisor = max(1., extent)
    roll, pitch, extent = roll/divisor, pitch/divisor, extent/divisor
    common = clamp(lift_common, extent*.5, 1.-extent*.5) if lift else extent*.5
    duties = [1. if boost else 0.] * 4
    for difference in (roll+pitch, -roll+pitch, roll-pitch, -roll-pitch):
        duties.append(clamp(common+difference*.5, 0., 1.))
    duties.extend((0., 0.))

    if roll:
        # Lift on the left rotates about +body Z: the car rolls right. The
        # low-mounted leftward side jet contributes the same real roll torque.
        # Its forward mounting also makes yaw, so use rear-jet differential to
        # oppose that yaw. Boost can stay on without saturating this correction:
        # reduce the opposite rear jet when the rear group is already full.
        side, rear, opposite = (8, 0, 3) if roll > 0. else (9, 3, 0)
        duties[side] = SIDE_ASSIST * abs(roll)
        correction = abs(_TORQUES[side][1] / _TORQUES[rear][1]) * duties[side]
        if boost:
            duties[opposite] = max(0., duties[opposite]-correction)
        else:
            duties[rear] = min(1., correction)

    return {'throttle': throttle, 'brake': brake,
            'steer': float(('LEFT' in buttons) - ('RIGHT' in buttons)),
            'fan': _unit(fan), 'nozzles': duties}


class RotationControl:
    """Clicky, motion and opt-in airborne leveling through ordinary jets.

    A clicky hold requests a body rate and release briefly brakes. Tilt requests
    a gentler rate; neutral coasts unless leveling is enabled. Flick adds stronger
    torque, fading its authority near a soft rate ceiling. Motion takes its axis
    from clicky input, including pending release braking. There is no leveling
    target by default. With calibrated airborne leveling explicitly enabled,
    returning the badge to its center brakes a tumble and restores world-up;
    displaced tilt still requests continuous rotation. Heading stays free.
    Call on every fixed physics tick while needs_feedback() is true.
    """
    RATES = (3.141592653589793, 2.792526803190927)  # roll 180, pitch 160 deg/s
    ACCEL = 6.  # rad/s^2; comparable feel despite different body inertias
    GAIN = 8.
    STOP_RATE = .008
    RELEASE_SECONDS = 1.2
    TILT_RATES = (2.6179938779914944, 2.2689280275926285)  # 150 / 130 deg/s
    TILT_ACCEL = 4.5
    TILT_GAIN = 6.
    FLICK_ACCEL = 18.
    FLICK_RATES = (4.1887902047863905, 3.6651914291880923)  # 240 / 210 deg/s
    FLICK_SOFT_BAND = 1.0471975511965976  # final 60 deg/s of authority fades
    LEVEL_ENTER = 0.   # the sensor's four-degree dead zone is the centered grip
    LEVEL_EXIT = .025  # release by 4.6 degrees; visible lean owns rotation
    LEVEL_ANGLE_GAIN = 3.2
    LEVEL_RATE = 2.8
    LEVEL_GAIN = 8.
    LEVEL_ACCEL = 12.
    LEVEL_QUIET_ANGLE = .02617993877991494  # 1.5 degrees
    LEVEL_QUIET_RATE = .035

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = [False, False]
        self.release_age = [0., 0.]
        self.targets = [0., 0.]
        self.motion_targets = [0., 0.]
        self.motion_active = [False, False]
        self.level_centered = False
        self.level_strength = 0.
        self.level_error = 0.
        self.level_state = 'off'
        # An exact inverted pose has no unique shortest way up. Retain the
        # previous correction axis, or favor the current tumble if it has one.
        self.level_axis = (0., 1.)  # body X/Z; deterministic roll if stationary

    def snapshot(self):
        return {'active': tuple(self.active), 'targets': tuple(self.targets),
                'release_age': tuple(self.release_age),
                'motion_targets': tuple(self.motion_targets),
                'motion_active': tuple(self.motion_active),
                'level_state': self.level_state,
                'level_strength': self.level_strength,
                'level_error': self.level_error}

    def needs_feedback(self, motion_signal=NO_MOTION, level_enabled=False):
        return level_enabled or any(self.active) or any(motion_signal)

    def _level_blend(self, sim, signal, enabled, manual, motion):
        """Return center authority without retaining an old input's permission."""
        self.level_strength = 0.
        self.level_error = 0.
        self.level_state = 'off'
        if (not enabled or not sim.airborne or sim.contact_count or
                sim.chassis_contacts or manual or signal[2] or signal[3]):
            self.level_centered = False
            return 0.
        # A flick-only sensor mode still reports physical tilt. Its prepared
        # torque signal has zero tilt, which must not imply a centered grip.
        tx = number(motion.get('tilt_x', signal[0])) if motion else signal[0]
        ty = number(motion.get('tilt_y', signal[1])) if motion else signal[1]
        amount = max(abs(tx), abs(ty))
        if amount <= self.LEVEL_ENTER:
            self.level_centered = True
        elif amount >= self.LEVEL_EXIT:
            self.level_centered = False
        self.level_state = 'tilt'
        if not self.level_centered:
            return 0.
        self.level_strength = clamp((self.LEVEL_EXIT-amount) /
                                    (self.LEVEL_EXIT-self.LEVEL_ENTER), 0., 1.)
        self.level_state = 'level'
        return self.level_strength

    def _level_output(self, sim, rates):
        # World up expressed in car coordinates gives a shortest correction
        # around body X/Z without Euler-angle singularities or a yaw target.
        up = q_inverse_rotate(sim.q, (0., 1., 0.))
        length = sqrt(up[0]*up[0]+up[2]*up[2])
        self.level_error = atan2(length, up[1])
        if length > .0001:
            self.level_axis = (up[2]/length, -up[0]/length)
        elif up[1] < 0.:
            spin = sqrt(rates[0]*rates[0]+rates[2]*rates[2])
            if spin > .05:
                self.level_axis = (rates[0]/spin, rates[2]/spin)
        quiet = (self.level_error < self.LEVEL_QUIET_ANGLE and
                 abs(rates[0]) < self.LEVEL_QUIET_RATE and
                 abs(rates[2]) < self.LEVEL_QUIET_RATE)
        if quiet:
            self.level_state = 'steady'
            return (0., 0.)
        target = min(self.LEVEL_RATE, self.level_error*self.LEVEL_ANGLE_GAIN)
        x = clamp((self.level_axis[0]*target-rates[0])*self.LEVEL_GAIN,
                  -self.LEVEL_ACCEL, self.LEVEL_ACCEL)
        z = clamp((self.level_axis[1]*target-rates[2])*self.LEVEL_GAIN,
                  -self.LEVEL_ACCEL, self.LEVEL_ACCEL)
        # Finite ordinary nozzle authority: no pose, rate, pressure or gravity
        # edits. Depleted air can prevent the requested recovery.
        mass = sim.mass/REFERENCE_MASS
        return (clamp(z*INERTIA[2]*mass/_ROLL_TORQUE, -1., 1.),
                clamp(-x*INERTIA[0]*mass/_PITCH_TORQUE, -1., 1.))

    def mix(self, dt, sim, buttons=(), motion=None, fan=0., manual=(), motion_axes=None,
            motion_signal=None, level_enabled=False, lift_common=1.):
        # A caller can normalize one immutable sensor poll once, then reuse it
        # while angular-rate feedback still runs on every fixed physics tick.
        if motion_signal is not None:
            motion_axes = (0., 0.)
        elif motion_axes is None:
            motion_axes = rotation_axes(buttons, motion)
        # Motion was already normalized for this tick. Add the independent
        # clicky request without parsing the same four sensor channels twice.
        requested = (float(('RIGHT' in manual) - ('LEFT' in manual)) + motion_axes[0],
                     float(('UP' in manual) - ('DOWN' in manual)) + motion_axes[1])
        held = ('LEFT' in manual or 'RIGHT' in manual,
                'UP' in manual or 'DOWN' in manual)
        output = list(motion_axes)
        level = self._level_blend(sim, motion_signal or NO_MOTION,
                                  level_enabled, any(held), motion)
        rates = (q_inverse_rotate(sim.q, sim.omega)
                 if level or any(held) or any(self.active) or (motion_signal and any(motion_signal))
                 else (0., 0., 0.))
        for axis, body_axis, sign, torque in ((0, 2, 1., _ROLL_TORQUE), (1, 0, -1., _PITCH_TORQUE)):
            target = 0.
            tilt = motion_signal[axis] if motion_signal is not None else 0.
            flick = motion_signal[axis+2] if motion_signal is not None else 0.
            self.motion_targets[axis] = tilt*self.TILT_RATES[axis]
            self.motion_active[axis] = bool(tilt or flick)
            if self.motion_active[axis]:
                self.active[axis] = False
                self.release_age[axis] = 0.
                rate = rates[body_axis]*sign
                if flick:
                    # A brief flick owns this axis while its sensor pulse lasts.
                    # This also keeps its preparatory lean from opposing it.
                    # The guard reduces same-direction torque, never velocity.
                    direction = 1. if flick > 0. else -1.
                    guard = clamp((self.FLICK_RATES[axis]-rate*direction) /
                                  self.FLICK_SOFT_BAND, 0., 1.)
                    if rate*direction >= 0.:
                        # Diagonal torque can exchange rotation between body
                        # axes. Share one rate allowance so repeated diagonal
                        # bursts cannot keep pumping energy into that tumble.
                        combined_rate = abs(rates[0])+abs(rates[1])+abs(rates[2])
                        guard = min(guard, clamp((self.FLICK_RATES[0]-combined_rate) /
                                                 self.FLICK_SOFT_BAND, 0., 1.))
                    accel = flick*self.FLICK_ACCEL*guard
                else:
                    error = self.motion_targets[axis]-rate
                    accel = clamp(error*self.TILT_GAIN, -self.TILT_ACCEL, self.TILT_ACCEL)
                output[axis] = clamp(accel*INERTIA[body_axis]*(sim.mass/REFERENCE_MASS)/torque, -1., 1.)
            elif held[axis]:
                self.active[axis] = True
                self.release_age[axis] = 0.
                target = clamp(requested[axis], -1., 1.) * self.RATES[axis]
            elif motion_axes[axis]:
                # A new gesture takes this axis back immediately. Never brake
                # a motion pulse merely because a clicky was released earlier.
                self.active[axis] = False
            elif self.active[axis]:
                self.release_age[axis] += max(0., number(dt))
                if (self.release_age[axis] > self.RELEASE_SECONDS or
                        abs(rates[body_axis]) < self.STOP_RATE or
                        sim.contact_count or sim.chassis_contacts):
                    self.active[axis] = False
            self.targets[axis] = target
            if self.active[axis]:
                error = target - rates[body_axis]*sign
                accel = clamp(error*self.GAIN, -self.ACCEL, self.ACCEL)
                # Pressure still determines actual force. No fuel, momentum or
                # orientation is written by this controller, even if jets cannot
                # achieve the requested rate. No accumulated integral windup.
                output[axis] = clamp(accel*INERTIA[body_axis]*(sim.mass/REFERENCE_MASS)/torque, -1., 1.)
        if level:
            recovery = self._level_output(sim, rates)
            for axis in range(2):
                output[axis] = output[axis]*(1.-level)+recovery[axis]*level
        return mix_controls(buttons, fan=fan, speed=sim.speed, axes=output,
                            lift_common=lift_common)
