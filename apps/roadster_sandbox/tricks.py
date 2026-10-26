"""Optional nozzle-only stunt controller for the portable Roadster sandbox.

These presets are small feedback controllers, not animation paths. They only
return ordinary fan/nozzle commands; gravity, fuel use and impacts stay in the
rigid-body solver. Cancelling or finishing releases the nozzles immediately.
"""
import math
from physics import (INERTIA, GRAVITY, NOZZLES, clamp, cross, number,
                     q_inverse_rotate, scale)
from vehicle_spec import REFERENCE_MASS, supply_fraction

NAMES = ('hop', 'kickflip', 'spin', 'backflip')
_RESERVE = {'hop': .55, 'kickflip': .80, 'spin': .92, 'backflip': .80}
_LAUNCH_SPEED = {'hop': 5.0, 'kickflip': 10.5, 'spin': 14.0, 'backflip': 9.0}
_MAX_RATE = {'kickflip': 6.5, 'spin': 4.0, 'backflip': 6.0}
_KICKFLIP_SIDE_DUTY = .18
_NOZZLE_TORQUES = tuple(cross(n[1], scale(n[2], n[3])) for n in NOZZLES)
_PITCH_TORQUE = min(abs(_NOZZLE_TORQUES[4][0]+_NOZZLE_TORQUES[5][0]),
                    abs(_NOZZLE_TORQUES[6][0]+_NOZZLE_TORQUES[7][0]))
_YAW_TORQUE = min(abs(_NOZZLE_TORQUES[8][1]), abs(_NOZZLE_TORQUES[9][1]))


def _roll_authority(side, rear, front_lift, rear_lift):
    """Full roll authority with room for actual yaw/pitch counterthrust.

    The opposite rear jet balances side-jet yaw. Its pitch moment needs a
    small shift between front and rear lift. Reserve that shift before using
    the lift pair's full range, rather than relying on a frozen torque value.
    """
    rear_duty = abs(_NOZZLE_TORQUES[side][1] / _NOZZLE_TORQUES[rear][1])*_KICKFLIP_SIDE_DUTY
    pitch = abs(_NOZZLE_TORQUES[rear][0]*rear_duty /
                (_NOZZLE_TORQUES[rear_lift][0]-_NOZZLE_TORQUES[front_lift][0]))
    roll = abs(_NOZZLE_TORQUES[front_lift][2])+abs(_NOZZLE_TORQUES[rear_lift][2])
    return roll*max(0., 1.-pitch)+abs(_NOZZLE_TORQUES[side][2])*_KICKFLIP_SIDE_DUTY


_KICKFLIP_ROLL_TORQUE = min(_roll_authority(8, 0, 4, 6),
                            _roll_authority(9, 3, 5, 7))


class TrickController:
    """start(name, sim), then update(dt, sim, manual_controls) before step().

    The simulation clock measures progress, so a stalled renderer never burns
    through an entire sequence. Manual nozzle input cancels a preset; driving
    and steering remain available. The user's fan command stays independent:
    suction and nozzle forces combine, and rotation waits for actual liftoff.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.committed = False
        self.active = ''
        self.phase = ''
        self.message = ''
        self.rotation = 0.
        self._last_time = self._phase_time = self._start_time = 0.
        self._previous_rate = 0.
        self._was_airborne = False
        self._flick_axis = 2
        self._flick_direction = 1.
        self._target_rotation = 2.*math.pi

    def cancel(self):
        if self.active:
            self.message = 'Preset cancelled; manual control'
        self.active = self.phase = ''
        self.committed = False

    def start(self, name, sim):
        # A new request replaces the previous intent even if this start is
        # rejected. It must not silently leave the old nozzle sequence firing.
        self.active = self.phase = ''
        self.committed = False
        if name not in NAMES:
            self.message = 'Unknown trick'
            return False
        if sim.basis()[1][1] < .85:
            self.message = 'Start upright to use a trick'
            return False
        if max(abs(v) for v in sim.omega) > 1.:
            self.message = 'Let the car settle before a trick'
            return False
        if sim.air_fraction < _RESERVE[name] or sim.lift_status()['ratio'] < 1.:
            self.message = '%s needs %d%% air reserve; T refills air in place' % (name, int(_RESERVE[name]*100))
            return False
        self.active = name
        self.phase = 'lift'
        self.message = 'Preparing ' + name + '; fan stays under your control'
        self.rotation = 0.
        self._last_time = self._phase_time = self._start_time = sim.time
        self._previous_rate = 0.
        self._was_airborne = False
        return True

    def start_flick(self, direction, sim):
        """Commit one directional flip; another flick cannot stack or restart it.

        The caller consumes gesture events and keeps continuous tilt/centering
        out of this controller until it releases. Explicit manual nozzles still
        cancel. Only real nozzle forces change motion, including the short
        clearance pulse when a low car needs more airtime.
        """
        if self.committed and self.active:
            return False
        if direction not in ('left', 'right', 'forward', 'back'):
            return False
        if not sim.airborne and sim.basis()[1][1] < .85:
            self.message = 'Start upright to launch a flip'
            return False
        # Keep a real maneuver reserve in limited-air mode. The minimum for
        # takeoff alone cannot also power a complete turn and its counterpulse.
        if sim.air_fraction < .80 or sim.lift_status()['ratio'] < 1.:
            self.message = 'Flip needs more air; refill or use unlimited air'
            return False
        self._flick_axis = 2 if direction in ('left', 'right') else 0
        self._flick_direction = 1. if direction in ('right', 'forward') else -1.
        self.active = ('kickflip' if self._flick_axis == 2 else
                       'backflip' if direction == 'back' else 'frontflip')
        self.committed = True
        self.phase = 'lift'
        self.message = self.active + ': committed'
        self.rotation = 0.
        self._last_time = self._phase_time = self._start_time = sim.time
        self._previous_rate = 0.
        self._was_airborne = sim.airborne
        self._target_rotation = 2.*math.pi
        return True

    def _phase(self, phase, sim):
        self.phase = phase
        self._phase_time = sim.time

    def _finish(self, message):
        self.active = self.phase = ''
        self.committed = False
        self.message = message

    def _flick_mix(self, pitch, roll, duties, lift=False):
        # Positive commands are physical +body X/+body Z. Share finite lift
        # authority rather than saturating one axis and silently losing another.
        extent = abs(pitch)+abs(roll)
        divisor = max(1., extent)
        pitch, roll = pitch/divisor, roll/divisor
        extent = min(1., extent)
        common = 1.-extent*.5 if lift else extent*.5
        for i, difference in enumerate((-pitch+roll, -pitch-roll,
                                         pitch+roll, pitch-roll)):
            duties[i+4] = common+difference*.5
        if roll:
            side, rear = (8, 0) if roll > 0. else (9, 3)
            duties[side] = _KICKFLIP_SIDE_DUTY*abs(roll)
            duties[rear] = (abs(_NOZZLE_TORQUES[side][1]/_NOZZLE_TORQUES[rear][1])
                            *duties[side])
            # Compensate rear exhaust pitch. Keep the requested differential
            # even when the lift preparation saturates its common component.
            correction = -_NOZZLE_TORQUES[rear][0]*duties[rear]/(2.*_PITCH_TORQUE)
            if lift:
                for i in range(4, 8):
                    duties[i] = max(0., duties[i]-correction)
            duties[4] = max(0., duties[4]-correction)
            duties[5] = max(0., duties[5]-correction)
            duties[6] = min(1., duties[6]+correction)
            duties[7] = min(1., duties[7]+correction)

    def _update_flick(self, sim, elapsed, duties):
        axis, direction = self._flick_axis, self._flick_direction
        rates = q_inverse_rotate(sim.q, sim.omega)
        factor = supply_fraction(sim.air_mass)
        mass = sim.mass/REFERENCE_MASS
        acceleration = (_PITCH_TORQUE if axis == 0 else _KICKFLIP_ROLL_TORQUE)*factor/(INERTIA[axis]*mass)
        if acceleration < .1:
            self._finish('Pressure exhausted; manual control')
            return
        up = q_inverse_rotate(sim.q, (0., 1., 0.))
        tilt_length = math.sqrt(up[0]*up[0]+up[2]*up[2])
        tilt_angle = math.atan2(tilt_length, up[1])
        level_x = up[2]/tilt_length if tilt_length > .0001 else 0.
        level_z = -up[0]/tilt_length if tilt_length > .0001 else 0.
        pitch_accel = _PITCH_TORQUE*factor/(INERTIA[0]*mass)
        roll_accel = _KICKFLIP_ROLL_TORQUE*factor/(INERTIA[2]*mass)
        if self.phase == 'lift':
            # Predict clearance after the ~1.8 s rotation/braking window. An
            # already high/upward-moving car rotates immediately: it does not
            # repeat the desktop preset's fixed 10 m/s launch every gesture.
            flight = 1.8
            needed_vy = max(0., (3.-sim.y+.5*GRAVITY*flight*flight)/flight)
            if (not sim.airborne or sim.vy < needed_vy) and sim.time-self._phase_time < 1.2:
                target = min(2., tilt_angle*5.)
                pitch = clamp((level_x*target-rates[0])*9./pitch_accel, -.5, .5)
                roll = clamp((level_z*target-rates[2])*9./roll_accel, -.5, .5)
                self._flick_mix(pitch, roll, duties, True)
                self.message = self.active + ': lift clearance'
                return
            if not sim.airborne:
                self._finish('Flip held by contact or fan; lift first')
                return
            # Include preparatory lean in the requested revolution, ending at
            # world upright rather than at the same tilted starting attitude.
            angle = math.atan2(-up[2], up[1]) if axis == 0 else math.atan2(up[0], up[1])
            self._target_rotation = 2.*math.pi-angle*direction
            self._previous_rate = rates[axis]*direction
            self._phase('rotate', sim)
            elapsed = 0.
        if self.phase == 'level':
            target = min(3., tilt_angle*5.)
            pitch = clamp((level_x*target-rates[0])*10./pitch_accel, -1., 1.)
            roll = clamp((level_z*target-rates[2])*10./roll_accel, -1., 1.)
            if tilt_angle < .035 and abs(rates[0]) < .14 and abs(rates[2]) < .14:
                self._finish(self.active + ' complete; continue driving')
                return
            self._flick_mix(pitch, roll, duties)
            self.message = self.active + ': leveling'
            return
        rate = rates[axis]*direction
        self.rotation += .5*(rate+self._previous_rate)*elapsed
        self._previous_rate = rate
        remaining = self._target_rotation-self.rotation
        desired = min(6.5 if axis == 2 else 6., abs(remaining)*3.,
                      math.sqrt(1.25*acceleration*abs(remaining)))
        if remaining < 0.:
            desired = -desired
        command = clamp((desired-rate)*9./acceleration, -1., 1.)
        self.phase = 'counter pulse' if command < 0. else 'rotate'
        self.message = self.active + ': ' + self.phase
        if abs(remaining) < .03 and abs(rate) < .12:
            self._phase('level', sim)
            return
        if axis == 0:
            pitch = command*direction
            roll = clamp(-rates[2]*9./roll_accel, -.35, .35)
        else:
            pitch = clamp(-rates[0]*9./pitch_accel, -.35, .35)
            roll = command*direction
        self._flick_mix(pitch, roll, duties)

    def _kickflip_nozzles(self, command, duties):
        # The side jets' vertical mounting offset gives a real roll moment.
        # Y-L helps start the roll; Y-R opposes it during braking/leveling.
        side, rear, front_lift, rear_lift = ((8, 0, 4, 6) if command > 0.
                                           else (9, 3, 5, 7))
        duties[side] = _KICKFLIP_SIDE_DUTY*abs(command)
        # The same side jet also has a much larger yaw moment. An outer rear
        # jet cancels that torque through its actual lateral mounting offset.
        duties[rear] = abs(_NOZZLE_TORQUES[side][1]/_NOZZLE_TORQUES[rear][1])*duties[side]
        lift_torque = abs(_NOZZLE_TORQUES[front_lift][2])
        lift = (_KICKFLIP_ROLL_TORQUE*abs(command)
                - abs(_NOZZLE_TORQUES[side][2])*duties[side])/(2.*lift_torque)
        # Rear exhaust adds nose-up torque. Shift a little lift from the front
        # nozzle to the rear nozzle to oppose it, without imposing orientation.
        pitch = (-_NOZZLE_TORQUES[rear][0]*duties[rear]
                 / (2.*abs(_NOZZLE_TORQUES[front_lift][0])))
        duties[front_lift] = clamp(lift-pitch, 0., 1.)
        duties[rear_lift] = clamp(lift+pitch, 0., 1.)

    def update(self, dt, sim, manual_controls=None):
        controls = dict(manual_controls or {})
        if not self.active:
            return controls
        if any(number(v) > .01 for v in controls.get('nozzles', ())):
            self.cancel()
            return controls
        # A reset outside the app's normal action handler must not leave a
        # delayed firing sequence armed against a new simulation clock.
        if sim.time < self._last_time:
            self._finish('Preset cleared after reset')
            return controls
        elapsed = max(0., sim.time-self._last_time)
        self._last_time = sim.time
        duties = [0.] * 10
        controls['nozzles'] = duties
        if sim.time-self._start_time > 8.:
            self._finish('Preset ended; continue driving')
            return controls
        self._was_airborne = self._was_airborne or sim.airborne
        if self._was_airborne and not sim.airborne:
            self._finish('Preset released on contact')
            return controls
        if self.committed:
            self._update_flick(sim, elapsed, duties)
            return controls
        if self.phase == 'lift':
            # A grounded car may be held down by fan suction or low pressure.
            # Fire real lift jets, but do not turn a grounded launch timeout
            # into a roll/spin. The airborne lift window starts at liftoff.
            if not sim.airborne:
                self._phase_time = sim.time
            if (not sim.airborne or
                    (sim.vy < _LAUNCH_SPEED[self.active] and sim.time-self._phase_time < 1.8)):
                for i in range(4, 8):
                    duties[i] = 1.
                lift = sim.lift_status()
                if not sim.airborne and lift['reason'] == 'fan':
                    self.message = self.active + ': jets firing; fan holds car down (F toggles, X cancels)'
                elif not sim.airborne and lift['reason'] == 'low_pressure':
                    self.message = 'Lift pressure too low; T refills air, X cancels'
                else:
                    self.message = self.active + ': lift pulse'
                return controls
            self._was_airborne = sim.airborne
            if self.active == 'hop':
                self._finish('Hop released; gravity takes over')
                return controls
            self._phase('rotate', sim)
        axis = 2 if self.active == 'kickflip' else (0 if self.active == 'backflip' else 1)
        # Forward is +Z, so a nose-up backflip rotates about negative body X.
        # Track positive progress for every trick without changing the actual
        # torque or angular velocity applied by the rigid-body solver.
        direction = -1. if axis == 0 else 1.
        body_rate = q_inverse_rotate(sim.q, sim.omega)
        rate = body_rate[axis]*direction
        self.rotation += .5*(rate+self._previous_rate)*elapsed
        self._previous_rate = rate
        remaining = 2.*math.pi-self.rotation
        factor = supply_fraction(sim.air_mass)
        torque = _KICKFLIP_ROLL_TORQUE if axis == 2 else (_PITCH_TORQUE if axis == 0 else _YAW_TORQUE)
        acceleration = torque*factor / (INERTIA[axis]*sim.mass/REFERENCE_MASS)
        if acceleration < .1:
            self._finish('Pressure exhausted; manual control')
            return controls
        # A conservative stopping-distance envelope starts the opposing pulse
        # early; rate feedback tapers its duty rather than snapping rotation.
        desired_rate = min(_MAX_RATE[self.active], abs(remaining)*3.,
                           math.sqrt(1.25*acceleration*abs(remaining)))
        if remaining < 0.:
            desired_rate = -desired_rate
        command = clamp((desired_rate-rate)*9./acceleration, -1., 1.)
        self.phase = 'counter pulse' if command < 0. else 'rotate'
        self.message = self.active + ': ' + self.phase
        if abs(remaining) < .03 and abs(rate) < .12:
            self._finish(self.active + ' released; continue driving')
            return controls
        if axis == 0:
            # Front lift jets pitch the nose up; the rear pair brakes that
            # rotation. Both are the same ordinary body-mounted lift nozzles.
            pair = (4, 5) if command > 0. else (6, 7)
            for i in pair:
                duties[i] = abs(command)
        elif axis == 2:
            self._kickflip_nozzles(command, duties)
        else:
            duties[8 if command > 0. else 9] = abs(command)
            # Counter the active side jet's roll with the opposite lift pair.
            # Both the required moment and available authority come from the
            # same physical nozzle layout used by the solver.
            side = 8 if command > 0. else 9
            roll_moment = _NOZZLE_TORQUES[side][2]*abs(command)
            pair = (5, 7) if roll_moment > 0. else (4, 6)
            authority = abs(_NOZZLE_TORQUES[pair[0]][2]+_NOZZLE_TORQUES[pair[1]][2])
            for i in pair:
                duties[i] = abs(roll_moment)/authority
        return controls
