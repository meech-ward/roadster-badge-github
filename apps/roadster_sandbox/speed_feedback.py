"""Read-only speed cues and a measured, ground-only 0–60 mph timer.

Sample after each completed physics tick, never once per rendered frame.
The visual blend uses actual velocity and actual rear nozzle force. An empty
tank therefore cannot produce a boost cue just because its key is held.
"""
import math
from physics import GROUND_Y, NOZZLES

MPH_PER_MPS = 2.2369362920544
SIXTY_MPH = 26.8224
REAR_CAPACITY = sum(nozzle[3] for nozzle in NOZZLES[:4])
REST_SPEED = .2
REARM_TIME = .35


def _clamp(value):
    return max(0., min(1., value))


class SpeedFeedback:
    """Call reset(sim) after explicit resets and pass update as step's observer.

    speed_mph is horizontal speed; forward_mph is signed body-forward speed.
    run_time is live while running and retained on completion/invalidation.
    A completed or invalid result remains until the next stationary launch.
    Falling, reversing, leaving the ground or hitting bodywork cannot win a
    ground acceleration result. This measures this simulation, not a car spec.
    """
    def __init__(self):
        self.reset()

    def reset(self, sim=None):
        self.speed_mph = self.forward_mph = 0.
        self.rear_force = self.rear_fraction = 0.
        self.boost_mix = self.speed_mix = self.intensity = 0.
        self._blend_dt = None
        self._boost_blend = self._speed_blend = 0.
        self.run_time = None
        self.run_reason = "Launch from rest"
        self._start_time = 0.
        self._rest_time = 0.
        self._previous_forward = 0. if sim is None else sim.speed
        self._previous_horizontal = 0. if sim is None else math.sqrt(sim.vx**2+sim.vz**2)
        self._armed = self._previous_horizontal <= REST_SPEED
        if sim is not None:
            self._armed = (self._armed and abs(sim.vy) < .3 and
                           abs(sim.y-GROUND_Y) < .15 and sim.basis()[1][1] > .9)
        self.run_state = "ready" if self._armed else "waiting"
        if sim is not None:
            self.speed_mph = self._previous_horizontal*MPH_PER_MPS
            self.forward_mph = sim.speed*MPH_PER_MPS

    def update(self, dt, sim, values):
        """Observe one completed tick; values is physics' normalized tuple."""
        horizontal = math.sqrt(sim.vx**2+sim.vz**2)
        forward = sim.speed
        self.speed_mph = horizontal*MPH_PER_MPS
        self.forward_mph = forward*MPH_PER_MPS
        forces = sim.nozzle_forces
        self.rear_force = forces[0] + forces[1] + forces[2] + forces[3]
        self.rear_fraction = _clamp(self.rear_force/REAR_CAPACITY)
        # Exponential smoothing has the same response at every render rate.
        # These observers run at fixed physics ticks, including when several
        # ticks catch up in one frame. Reuse the exact exponential coefficients
        # until dt changes; changing physics rate still recomputes them.
        if dt != self._blend_dt:
            self._blend_dt = dt
            self._boost_blend = 1.-math.exp(-dt/.13)
            self._speed_blend = 1.-math.exp(-dt/.3)
        self.boost_mix += (self.rear_fraction-self.boost_mix)*self._boost_blend
        target_speed = _clamp((horizontal-8.)/37.)
        self.speed_mix += (target_speed-self.speed_mix)*self._speed_blend
        self.intensity = _clamp(self.speed_mix*(.35+.65*self.boost_mix)+.15*self.boost_mix)

        basis = sim.basis()
        grounded = (sim.contact_count >= 2 and not sim.airborne and
                    sim.chassis_contacts == 0 and sim.surface == "ground" and
                    basis[1][1] > .9 and abs(basis[2][1]) < .2)
        stationary = grounded and horizontal <= REST_SPEED and abs(sim.vy) < .3
        self._rest_time = self._rest_time+dt if stationary else 0.
        if self._rest_time >= REARM_TIME:
            self._armed = True
            if self.run_state == "waiting":
                self.run_state, self.run_reason = "ready", "Launch from rest"
        wants_forward = values[0] > 0. or self.rear_force > 1.
        launch = (self._armed and grounded and abs(sim.vy) < .3 and wants_forward and
                  self._previous_horizontal <= REST_SPEED and
                  forward > self._previous_forward+1.e-6)
        if launch and self.run_state != "running":
            self.run_state, self.run_reason = "running", "Ground acceleration"
            self._start_time = sim.time-dt
            self.run_time = 0.
            self._armed = False
            self._rest_time = 0.
        elif self._armed and horizontal > REST_SPEED and self.run_state != "running":
            # A push, sideways skid or reverse departure is not a zero start.
            self._armed = False
            if self.run_state == "ready":
                self.run_state, self.run_reason = "waiting", "Stop to arm 0-60"

        if self.run_state == "running":
            self.run_time = max(0., sim.time-self._start_time)
            reason = ""
            if sim.chassis_contacts:
                reason = "Impact interrupted run"
            elif not grounded:
                reason = "Left level ground"
            elif forward < -.1:
                reason = "Reverse interrupted run"
            elif self._rest_time >= REARM_TIME:
                reason = "Stopped before 60 mph"
            if reason:
                self.run_state, self.run_reason = "invalid", reason
            elif forward >= SIXTY_MPH and self._previous_forward < SIXTY_MPH:
                fraction = (SIXTY_MPH-self._previous_forward)/(forward-self._previous_forward)
                self.run_time = sim.time-dt+dt*fraction-self._start_time
                self.run_state, self.run_reason = "complete", "Measured 0-60 mph"
                self._armed = False
        self._previous_forward = forward
        self._previous_horizontal = horizontal
