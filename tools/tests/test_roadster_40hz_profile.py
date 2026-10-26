"""Forty-Hz badge scheduling, impacts and player controls; host checks only."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
SOURCE = APP / 'physics.py'
spec = importlib.util.spec_from_file_location('profile40_physics', SOURCE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
spec = importlib.util.spec_from_file_location('profile40_controls', APP / 'badge_controls.py')
b = importlib.util.module_from_spec(spec)
previous = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    spec.loader.exec_module(b)
finally:
    if previous is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = previous

def body():
    sim = p.Simulation('flat')
    sim.set_step_hz(40)
    sim.max_substeps = 4
    sim.set_air_mode('unlimited')
    return sim

def advance(sim, seconds, controls=None, control_filter=None):
    for unused in range(round(seconds * sim.step_hz)):
        sim.step(sim.fixed_dt, controls, control_filter=control_filter)

def held_lift(dt, sim, values):
    return b.mix_controls(('BACK',), fan=values.get('fan', 0.),
                          lift_common=b.held_lift_common(sim))

def rotation_phase(sim, controller, seconds, signal=b.NO_MOTION):
    angle = [0., 0., 0.]
    maximum_force = 0.
    for unused in range(round(seconds * sim.step_hz)):
        before = p.q_inverse_rotate(sim.q, sim.omega)
        controls = controller.mix(sim.fixed_dt, sim, motion_signal=signal,
                                  level_enabled=True)
        sim.step(sim.fixed_dt, controls)
        after = p.q_inverse_rotate(sim.q, sim.omega)
        for axis in range(3):
            angle[axis] += .5 * (before[axis] + after[axis]) * sim.fixed_dt
        maximum_force = max(maximum_force, sum(sim.nozzle_forces))
    return angle, maximum_force

class FortyHzProfileTests(unittest.TestCase):
    def test_switch_preserves_pose_clock_pending_time_and_reset_profile(self):
        s=p.Simulation('flat')
        for _ in range(120):s.step(1/120,{'throttle':.4})
        s.step(.004)
        before=(s.x,s.y,s.z,s.q,s.vx,s.vy,s.vz,tuple(s.omega),s.time,s.total_steps,s._accumulator)
        s.set_step_hz(40)
        after=(s.x,s.y,s.z,s.q,s.vx,s.vy,s.vz,tuple(s.omega),s.time,s.total_steps,s._accumulator)
        self.assertEqual(before,after)
        s.step(1/40-s._accumulator)
        self.assertAlmostEqual(s.time,1.025,places=13)
        s.set_step_hz(60)
        s.step(1/60)
        self.assertAlmostEqual(s.time,1.025+1/60,places=13)
        s.set_step_hz(40)
        s.reset()
        self.assertEqual(s.step_hz,40)
        self.assertEqual(s.time,0.)

    def test_bounded_catchup_counts_actual_time_and_callbacks(self):
        s=body();filtered=[];observed=[]
        def controls(dt,sim,values):filtered.append((dt,sim.time));return values
        def observer(dt,sim,values):observed.append((dt,sim.time))
        self.assertEqual(s.step(.2,{},controls,observer),4)
        self.assertEqual(filtered,[(.025,i*.025) for i in range(4)])
        self.assertEqual(observed,[(.025,i*.025) for i in range(1,5)])
        self.assertAlmostEqual(s.time,.1,places=13)
        self.assertAlmostEqual(s.dropped_time,.1,places=13)

    def test_hard_impact_uses_six_240hz_steps_without_extra_elapsed_time(self):
        s=body();s.y=1.;s.vy=-15.;s.contact_count=0;s.chassis_contacts=0
        original=s._integrate_tick;dt=[]
        def integrate(*values):dt.append(s.fixed_dt);return original(*values)
        s._integrate_tick=integrate
        s.step(.025)
        self.assertEqual(s.last_refinements,1)
        self.assertEqual(dt,[1/240]*6)
        self.assertEqual(s.last_substeps,1)
        self.assertEqual(s.fixed_dt,.025)
        self.assertAlmostEqual(sum(dt),s.time,places=14)

    def test_external_render_rates_produce_identical_fixed_tick_state(self):
        states=[]
        for fps in (10,20,30,40,60,120):
            s=body()
            for _ in range(fps*2):s.step(1/fps,{'throttle':.6,'steer':.1,'fan':1.})
            self.assertEqual(s.total_steps,80)
            self.assertAlmostEqual(s.dropped_time,0.)
            states.append((s.x,s.y,s.z,s.q,s.vx,s.vy,s.vz,tuple(s.omega),s.time))
        self.assertTrue(all(value==states[0] for value in states))

    def test_idle_reverse_braking_and_inverted_roof_settle(self):
        s=body()
        for _ in range(160):s.step(.025)
        self.assertAlmostEqual(s.y,p.GROUND_Y,delta=.002)
        for _ in range(40):s.step(.025,{'throttle':-1.})
        self.assertLess(s.speed,-1.)
        for _ in range(80):s.step(.025,{'brake':1.})
        self.assertLess(abs(s.speed),.1)
        s=body();s.y=1.2;s.q=(0.,0.,0.,1.)
        for _ in range(120):s.step(.025)
        self.assertLess(s.basis()[1][1],-.99)
        self.assertAlmostEqual(s.y,(max(v[1] for v in p.HULL)+.001),delta=.015)
        self.assertGreater(s.chassis_contacts,0)
        start=s.z
        for _ in range(40):s.step(.025,{'nozzles':[1.]*4})
        self.assertGreater(abs(s.z-start),1.)

    def test_smooth_hover_release_landing_and_repeat_launch(self):
        sim = body()
        peak_climb = peak_height = 0.
        for unused in range(40 * 20):
            sim.step(sim.fixed_dt, control_filter=held_lift)
            peak_climb = max(peak_climb, sim.vy)
            peak_height = max(peak_height, sim.y - p.GROUND_Y)
        self.assertGreater(peak_climb, 7.)
        self.assertLess(peak_height, b.LIFT_CEILING + .15)
        self.assertAlmostEqual(sim.y - p.GROUND_Y, b.LIFT_CEILING, delta=.05)
        self.assertLess(abs(sim.vy), .02)
        sim.step(sim.fixed_dt, {})
        self.assertEqual(sim.nozzle_forces, [0.] * 10)
        advance(sim, 5.)
        self.assertEqual(sim.contact_count, 4)
        self.assertFalse(sim.airborne)
        self.assertAlmostEqual(sim.y, p.GROUND_Y, delta=.05)
        landed = sim.y
        advance(sim, 1., control_filter=held_lift)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, landed + 2.8)
        self.assertGreater(sim.vy, 2.)
        self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_parked_roof_launch_keeps_height_based_lift(self):
        sim = body()
        sim.set_obstacles([{'id': 'roof', 'x': 0., 'z': -14., 'yaw': 0.,
                            'collision_boxes': ((0., 2., 0., 3., 2., 4.),)}])
        sim.y = 4. + p.GROUND_Y
        advance(sim, 1.)
        self.assertEqual(sim.contact_count, 4)
        self.assertEqual(sim.surface, 'vehicle:roof')
        roof = sim.y
        advance(sim, 1., control_filter=held_lift)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, roof + 1.)
        self.assertGreater(sim.vy, 1.)
        ground = body()
        advance(ground, 1., control_filter=held_lift)
        self.assertLess(sim.vy, ground.vy)

    def test_momentum_can_cross_hover_without_position_or_velocity_clamp(self):
        sim = body()
        sim.y = p.GROUND_Y + b.LIFT_CEILING - .1
        sim.vy = 12.
        height, speed = sim.y, sim.vy
        sim.step(sim.fixed_dt, control_filter=held_lift)
        self.assertGreater(sim.y, height)
        # Gravity naturally removes more velocity in a 25 ms tick than in a
        # 60 Hz tick. Bound acceleration by elapsed time, not a fixed delta.
        self.assertGreater(sim.vy, speed - p.GRAVITY * sim.fixed_dt * 1.1)
        peak = sim.y
        for unused in range(40 * 25):
            sim.step(sim.fixed_dt, control_filter=held_lift)
            peak = max(peak, sim.y)
        self.assertGreater(peak - p.GROUND_Y, b.LIFT_CEILING + 3.)
        self.assertAlmostEqual(sim.y - p.GROUND_Y, b.LIFT_CEILING, delta=.05)
        self.assertLess(abs(sim.vy), .02)

    def test_fan_and_lift_forces_remain_independent(self):
        sim = body()
        advance(sim, 1., {'fan': 1.})
        advance(sim, 2., {'fan': 1.}, held_lift)
        self.assertFalse(sim.airborne)
        self.assertEqual(sim.contact_count, 4)
        self.assertGreater(sim.fan_force, sim.mass * p.GRAVITY)
        self.assertTrue(all(force > p.NOZZLES[i][3]*.99 for i, force in enumerate(sim.nozzle_forces[4:8], 4)))
        self.assertEqual(held_lift(0., sim, {'fan': 1.})['fan'], 1.)

    def test_four_directions_keep_rotating_and_center_recovers(self):
        for signal, axis, sign in (((1., 0., 0., 0.), 2, 1.),
                                   ((-1., 0., 0., 0.), 2, -1.),
                                   ((0., 1., 0., 0.), 0, -1.),
                                   ((0., -1., 0., 0.), 0, 1.)):
            with self.subTest(signal=signal):
                sim = body()
                sim.y = 1000.
                sim.airborne = True
                controller = b.RotationControl()
                angle, force = rotation_phase(sim, controller, 4., signal)
                self.assertGreater(angle[axis] * sign, math.tau)
                self.assertGreater(force, 100.)
                self.assertEqual(controller.level_state, 'tilt')
                rotation_phase(sim, controller, 3.)
                self.assertGreater(sim.basis()[1][1], math.cos(math.radians(2.)))
                rate = p.q_inverse_rotate(sim.q, sim.omega)
                self.assertLess(abs(rate[0]), .07)
                self.assertLess(abs(rate[2]), .07)
                rotation_phase(sim, controller, .3, signal)
                self.assertEqual(controller.level_state, 'tilt')
                self.assertGreater(p.q_inverse_rotate(sim.q, sim.omega)[axis] * sign, .6)

    def test_center_recovers_vertical_inverted_and_combined_tumbles(self):
        cases = (((1., 0., 0.), math.pi/2, (0., 0., 0.)),
                 ((0., 0., 1.), math.pi, (0., 0., 0.)),
                 ((1., 0., 0.), math.pi, (0., 0., 0.)),
                 ((1., 0., 1.), math.pi, (0., 0., 0.)),
                 ((1., 0., 1.), 2.7, (1., .3, 2.)),
                 ((0., 0., 1.), 1.2, (0., 0., 3.7)))
        for axis, angle, spin in cases:
            with self.subTest(axis=axis, angle=angle, spin=spin):
                sim = body()
                sim.y = 1000.
                sim.q = p.q_axis(axis, angle)
                sim.omega = list(p.q_rotate(sim.q, spin))
                sim.airborne = True
                controller = b.RotationControl()
                _, force = rotation_phase(sim, controller, 3.)
                self.assertGreater(force, 100.)
                self.assertGreater(sim.basis()[1][1], math.cos(math.radians(2.)))
                rate = p.q_inverse_rotate(sim.q, sim.omega)
                self.assertLess(abs(rate[0]), .07)
                self.assertLess(abs(rate[2]), .07)
                self.assertGreater(sim.y, 900.)

if __name__=='__main__':unittest.main()
