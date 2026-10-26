"""Read-only flight measurements and modest stunt callouts.

Sample after every completed physics tick. Quaternion deltas measure actual
body motion; commands, presets and gyro gestures cannot award a stunt. This
observer never supplies a force, changes a pose or ends the simulation.
"""
import math

TAU = 2. * math.pi
SETTLE_TIME = .2
MIN_AIRTIME = .15


def _body_rotation(previous, current):
    """Shortest quaternion increment, expressed in the previous body frame.

    q and -q are the same pose. Keeping the short hemisphere avoids a false
    full turn when a quaternion changes sign. Per-physics-tick sampling keeps
    ordinary rotations far below the half-turn ambiguity.
    """
    a, b, c, d = previous
    e, f, g, h = current
    w = a*e + b*f + c*g + d*h
    x = a*f - b*e - c*h + d*g
    y = a*g + b*h - c*e - d*f
    z = a*h - b*g + c*f - d*e
    if w < 0.:
        w, x, y, z = -w, -x, -y, -z
    length = math.sqrt(x*x + y*y + z*z)
    if length < 1.e-10:
        return 0., 0.
    scale = 2.*math.atan2(length, w)/length
    return x*scale, z*scale


class StuntFeedback:
    """Call reset(sim) with explicit car resets; update is a step observer.

    airtime counts ticks with no wheel or chassis contact, excluding the
    landing confirmation interval. height is current height above takeoff;
    peak_height, airtime and rotation counts retain the last flight on landing.
    flips/rolls count full signed body rotations, with partial reversals
    cancelling rather than accumulating wobble. Yaw is not a flip or roll.
    Mixed tumbles have no special name: only completed local pitch/roll turns
    earn those basic labels. No scores, saved records or landing assists.
    """
    def __init__(self):
        self.reset()

    def reset(self, sim=None):
        self.airborne = False if sim is None else bool(sim.airborne)
        self.airtime = self.height = self.peak_height = 0.
        self.rolls = self.flips = 0
        self.message = ''
        self._message_time = 0.
        self._flight = False
        # A reset in midair must not manufacture a new takeoff or continue a
        # previous rotation. Wait for supported rest before measuring again.
        self._ready = not self.airborne
        self._previous_q = None if sim is None else sim.q
        self._previous_y = None if sim is None else sim.y
        self._previous_time = None if sim is None else sim.time
        self._takeoff_y = 0.
        self._pitch = self._roll = 0.
        self._support_time = self._clean_time = 0.

    def _say(self, message, seconds):
        self.message = message
        self._message_time = seconds

    def _begin(self, sim):
        self._flight = True
        self._ready = False
        self._takeoff_y = sim.y if self._previous_y is None else self._previous_y
        self.airtime = self.height = self.peak_height = 0.
        self.rolls = self.flips = 0
        self._pitch = self._roll = 0.
        self._support_time = self._clean_time = 0.
        self.message = ''
        self._message_time = 0.

    def _rotation(self, sim):
        if self._previous_q is None:
            return
        pitch, roll = _body_rotation(self._previous_q, sim.q)
        self._pitch += pitch
        self._roll += roll
        # Removing a completed turn retains the signed unfinished rotation.
        # A 300-degree excursion followed by its reversal therefore earns
        # nothing; a complete turn followed by another earns two real turns.
        if abs(self._pitch) + 1.e-8 >= TAU:
            direction = 1 if self._pitch > 0. else -1
            self._pitch -= direction*TAU
            self.flips += 1
            self._say('FRONTFLIP' if direction > 0 else 'BACKFLIP', 1.4)
        if abs(self._roll) + 1.e-8 >= TAU:
            direction = 1 if self._roll > 0. else -1
            self._roll -= direction*TAU
            self.rolls += 1
            self._say('ROLL RIGHT' if direction > 0 else 'ROLL LEFT', 1.4)

    def update(self, dt, sim, values):
        """Observe actual physics; values is accepted but never interpreted."""
        if dt <= 0.:
            return
        if self._previous_time is not None and sim.time < self._previous_time:
            # Defensive clock-reset detection covers callers that reset the
            # simulation without explicitly resetting their telemetry.
            self.reset(sim)
            return
        self._message_time = max(0., self._message_time-dt)
        if self._message_time == 0.:
            self.message = ''
        self.airborne = bool(sim.airborne)
        if self.airborne and self._ready:
            self._begin(sim)

        if self._flight:
            self.height = max(0., sim.y-self._takeoff_y)
            self.peak_height = max(self.peak_height, self.height)
            if self.airborne:
                self.airtime += dt
                self._rotation(sim)

        # Contacts alone do not award a landing: a scrape, bounce, roof impact
        # or wheels briefly brushing the loop must not flash a success banner.
        supported = not self.airborne and (sim.contact_count or sim.chassis_contacts)
        rates = sim.omega
        settled = (supported and abs(sim.vy) < 1. and
                   max(abs(rates[0]), abs(rates[1]), abs(rates[2])) < 1.5)
        upright = sim.basis()[1][1] if supported else 0.
        clean = (settled and sim.contact_count >= 3 and
                 not sim.chassis_contacts and upright > .85)
        self._support_time = self._support_time+dt if settled else 0.
        self._clean_time = self._clean_time+dt if clean else 0.
        if self._clean_time + 1.e-8 >= SETTLE_TIME:
            if self._flight and self.airtime >= MIN_AIRTIME:
                self._say('LANDED %.1fS AIR' % self.airtime, 2.2)
            self._flight = False
            self._ready = True
            self.height = 0.
        elif ((self._support_time + 1.e-8 >= SETTLE_TIME and upright < .5) or
              (self._support_time >= .6 and sim.contact_count < 2)):
            # A settled crash can be followed by another free-physics attempt.
            # It gets no landing award and does not reset or right the car.
            self._flight = False
            self._ready = True
            self.height = 0.
        self._previous_q = sim.q
        self._previous_y = sim.y
        self._previous_time = sim.time

    def snapshot(self):
        return {'airborne': self.airborne, 'airtime': self.airtime,
                'height': self.height, 'peak_height': self.peak_height,
                'rolls': self.rolls, 'flips': self.flips,
                'message': self.message}
