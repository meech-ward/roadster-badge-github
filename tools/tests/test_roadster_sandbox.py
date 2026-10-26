"""Behavioral checks for the continuous, portable Roadster rigid body.

These are host physics checks. Native MicroPython/render/control checks live in
playground/e2e and must be reported separately from hardware performance.
"""
import importlib.util
import math
from pathlib import Path
import random
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('sandbox_physics', ROOT / 'apps/roadster_sandbox/physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def advance(s, seconds, controls=None, fps=120):
    remaining = seconds
    while remaining > 1.e-10:
        dt = min(1./fps, remaining)
        s.step(dt, controls)
        remaining -= dt
    return s


def minimum_ground_clearance(s):
    return min(s.y+p.q_rotate(s.q, point)[1] for point in p.HULL)


class RigidBodyTests(unittest.TestCase):
    def test_idle_stable_and_clock_never_completes(self):
        s = p.Simulation('flat')
        advance(s, 4.)
        self.assertAlmostEqual(s.y, p.GROUND_Y, delta=.002)
        self.assertLess(abs(s.x), .001)
        self.assertLess(abs(s.z+14), .001)
        self.assertLess(max(abs(v) for v in s.omega), .001)
        self.assertEqual(s.time, 4.)
        self.assertFalse(hasattr(s, 'stage'))
        self.assertFalse(hasattr(s, 'paused'))

    def test_freefall_has_gravity_and_no_auto_level(self):
        s = p.Simulation('flat')
        s.y = 100.
        s.q = p.q_axis((0, 0, 1), 1.1)
        q = s.q
        advance(s, .5)
        self.assertAlmostEqual(s.vy, -p.GRAVITY*.5, delta=.01)
        self.assertAlmostEqual(s.y, 100.-.5*p.GRAVITY*.5**2, delta=.03)
        self.assertEqual(s.q, q)
        self.assertTrue(s.airborne)

    def test_roof_contact_stays_upside_down_and_responds_after_impact(self):
        s = p.Simulation('flat')
        s.y = 1.2
        s.q = (0., 0., 0., 1.)
        advance(s, 3.)
        self.assertLess(s.basis()[1][1], -.99)
        self.assertAlmostEqual(s.y, (max(v[1] for v in p.HULL)+.001), delta=.015)
        self.assertGreaterEqual(minimum_ground_clearance(s), -.01)
        self.assertEqual(s.contact_count, 0)
        self.assertGreater(s.chassis_contacts, 0)
        z = s.z
        advance(s, 1., {'nozzles': [1, 1, 1, 1]})
        self.assertGreater(abs(s.z-z), 1.)
        self.assertEqual(s.time, 4.)

    def test_side_contact_slides_then_settles_without_teleport(self):
        s = p.Simulation('flat')
        s.q = p.q_axis((0, 0, 1), math.pi/2)
        s.y = 1.5
        s.vz = 8.
        advance(s, 3.)
        self.assertGreater(s.z, -10.)
        self.assertGreaterEqual(minimum_ground_clearance(s), -.03)
        self.assertLess(s.y, 1.3)
        self.assertAlmostEqual(sum(v*v for v in s.q), 1., delta=1.e-8)

    def test_fixed_step_replay_is_render_rate_independent(self):
        states = []
        for fps in (15, 30, 60, 120):
            s = p.Simulation('flat')
            advance(s, 1., {'throttle': .5, 'steer': .2, 'fan': 1}, fps)
            advance(s, .5, {'nozzles': [1, 0, 0, 1]}, fps)
            advance(s, .5, {'brake': .4}, fps)
            states.append((s.x, s.y, s.z, s.vx, s.vy, s.vz, *s.q, *s.omega, s.air_mass))
        for state in states[1:]:
            for value, expected in zip(state, states[0]):
                self.assertAlmostEqual(value, expected, delta=1.e-9)

    def test_controller_pulses_are_sampled_on_fixed_ticks_at_every_render_rate(self):
        results = []
        for fps in (15, 30, 60, 120):
            s = p.Simulation('flat')
            samples = []

            def pulse(dt, body, manual):
                self.assertEqual(dt, p.FIXED_DT)
                samples.append(body.time)
                result = dict(manual)
                if .2 <= body.time < .6:
                    # Controller outputs use the same input clamping as keys.
                    result['nozzles'] = [0]*4+[2]*4
                    result['steer'] = float('nan')
                return result

            for _ in range(fps):
                s.step(1./fps, {'throttle': .2}, pulse)
            self.assertEqual(samples, [i*p.FIXED_DT for i in range(120)])
            self.assertGreater(s.y, 2.)
            results.append((s.x, s.y, s.z, s.vx, s.vy, s.vz,
                            *s.q, *s.omega, s.air_mass))
        for result in results[1:]:
            self.assertEqual(result, results[0])

    def test_invalid_and_large_dt_are_bounded(self):
        s = p.Simulation('flat')
        for dt in (0, -1, float('nan'), float('inf'), None):
            self.assertEqual(s.step(dt), 0)
        self.assertEqual(s.time, 0)
        self.assertEqual(s.step(10), p.MAX_SUBSTEPS)
        self.assertAlmostEqual(s.dropped_time, 10-p.MAX_SUBSTEPS*p.FIXED_DT)

    def test_reset_clears_transients_and_reproduces_a_fresh_simulation(self):
        s = advance(p.Simulation('flat'), 1.,
                    {'throttle': 1, 'steer': .5, 'fan': 1,
                     'nozzles': [1, 0, 0, 1, 1, 0, 0, 0]})
        self.assertTrue(any(s._f))
        self.assertTrue(any(s._t))
        s.reset()
        fresh = p.Simulation('flat')
        self.assertEqual(vars(s), vars(fresh))
        advance(s, .5)
        advance(fresh, .5)
        self.assertEqual(vars(s), vars(fresh))

    def test_mixed_nozzle_stress_is_finite_without_quaternion_drift(self):
        rng = random.Random(1844)
        s = p.Simulation('loop')
        for block in range(120):
            controls = {'throttle': rng.uniform(-1, 1), 'steer': rng.uniform(-1, 1),
                        'fan': rng.choice((0, 1)), 'brake': rng.choice((0, .5)),
                        'nozzles': [rng.choice((0, 0, 1)) for _ in range(10)]}
            advance(s, .5, controls)
            self.assertTrue(all(math.isfinite(v) for v in (s.x, s.y, s.z, s.vx, s.vy, s.vz, *s.q, *s.omega)))
            self.assertAlmostEqual(sum(v*v for v in s.q), 1., delta=1.e-8)
        self.assertEqual(s.time, 60.)
        self.assertGreaterEqual(s.air_mass, p.TANK_RESIDUAL-1.e-8)


class DriveTests(unittest.TestCase):
    def test_forward_reverse_brake_and_coast(self):
        forward = advance(p.Simulation('flat'), 1., {'throttle': 1})
        reverse = advance(p.Simulation('flat'), 1., {'throttle': -1})
        self.assertGreater(forward.speed, 10.)
        self.assertLess(reverse.speed, -4.)
        speed = forward.speed
        advance(forward, .5)
        self.assertGreater(forward.speed, .8*speed)
        self.assertLess(forward.speed, speed)
        advance(forward, 2., {'brake': 1})
        self.assertLess(abs(forward.speed), .1)

    def test_positive_steer_means_left_and_negative_is_mirrored(self):
        left = advance(p.Simulation('flat'), 1., {'throttle': .4, 'steer': 1})
        right = advance(p.Simulation('flat'), 1., {'throttle': .4, 'steer': -1})
        self.assertGreater(left.x, 1.)
        self.assertLess(right.x, -1.)
        self.assertAlmostEqual(left.x, -right.x, delta=.001)
        self.assertAlmostEqual(left.z, right.z, delta=.001)

    def test_fan_adds_load_and_grip_without_spending_air(self):
        a = advance(p.Simulation('flat'), 1., {'throttle': 1})
        b = advance(p.Simulation('flat'), 1., {'throttle': 1, 'fan': 1})
        self.assertGreater(b.speed, a.speed*1.25)
        self.assertGreater(sum(b.wheel_loads), sum(a.wheel_loads)*2.)
        self.assertGreater(b.fan_force, p.FAN_MAX_FORCE*.85)
        self.assertEqual(b.air_mass, p.TANK_MASS)

    def test_fan_idle_rolling_resistance_prevents_unpowered_creep(self):
        for scene in ('flat', 'loop'):
            s = p.Simulation(scene)
            z = s.z
            advance(s, 10., {'fan': 1})
            self.assertLess(abs(s.z-z), .02)
            self.assertLess(abs(s.speed), .002)
            self.assertEqual(s.air_mass, p.TANK_MASS)

    def test_throttle_is_not_air_control_or_a_velocity_clamp(self):
        s = p.Simulation('flat')
        s.y = 20.
        s.vz = 100.
        advance(s, .1, {'throttle': 1})
        self.assertGreater(s.vz, 99.)
        self.assertEqual(s.air_mass, p.TANK_MASS)


class ThrusterTests(unittest.TestCase):
    def test_each_nozzle_has_expected_body_force_and_torque(self):
        for index, nozzle in enumerate(p.NOZZLES):
            with self.subTest(nozzle=nozzle[0]):
                s = p.Simulation('flat')
                s.y = 100.
                duties = [0.]*10
                duties[index] = 1.
                s.step(p.FIXED_DT, {'nozzles': duties})
                force = p.scale(nozzle[2], nozzle[3])
                torque = p.cross(nozzle[1], force)
                velocity = (s.vx, s.vy+p.GRAVITY*p.FIXED_DT, s.vz)
                self.assertGreater(p.dot(velocity, force), 0.)
                self.assertGreater(p.dot(s.omega, torque), 0.)
                self.assertEqual(sum(f > 0 for f in s.nozzle_forces), 1)
                self.assertLess(s.air_mass, p.TANK_MASS)

    def test_four_lift_nozzles_jump_without_invented_attitude_command(self):
        s = advance(p.Simulation('flat'), .8, {'nozzles': [0]*4+[1]*4})
        self.assertGreater(s.y, 5.)
        self.assertGreater(s.vy, 8.)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertTrue(s.airborne)

    def test_left_lift_pair_causes_roll_from_mounting_points(self):
        s = p.Simulation('flat')
        s.y = 30.
        angle = 0.
        for _ in range(60):
            s.step(p.FIXED_DT, {'nozzles': [0, 0, 0, 0, 1, 0, 1, 0]})
            angle += p.q_inverse_rotate(s.q, s.omega)[2]*p.FIXED_DT
        self.assertGreater(s.omega[2], 5.)
        self.assertLess(abs(s.omega[0]), .01)
        self.assertGreater(angle, math.pi)

    def test_rotated_thrusters_rotate_force_not_world_up(self):
        s = p.Simulation('flat')
        s.y = 50.
        s.q = p.q_axis((0, 0, 1), math.pi/2)
        advance(s, .1, {'nozzles': [0]*4+[1]*4})
        self.assertLess(s.vx, -2.)
        self.assertLess(s.vy, -.8)

    def test_air_pressure_falls_and_empty_nozzles_have_no_force(self):
        s = p.Simulation('flat')
        s.y = 100.
        s.air_mass = p.TANK_RESIDUAL
        s.step(p.FIXED_DT, {'nozzles': [1.]*10})
        self.assertLess(max(s.nozzle_forces), 1.e-8)
        self.assertEqual(s.air_fraction, 0.)
        self.assertAlmostEqual(s.pressure, p.AMBIENT_PRESSURE, delta=1.e-7)
        s.reset()
        s.air_mass = p.TANK_MASS*.5
        s.y = 100.
        s.step(p.FIXED_DT, {'nozzles': [1.]})
        self.assertAlmostEqual(s.pressure/p.TANK_PRESSURE, .5)
        self.assertEqual(s.nozzle_forces[0], p.NOZZLES[0][3])  # regulator retains rated force

    def test_lift_telemetry_distinguishes_pressure_fan_and_orientation(self):
        s = p.Simulation('flat')
        self.assertEqual(s.lift_status()['reason'], 'ready')
        self.assertAlmostEqual(s.lift_status()['force'], sum(n[3] for n in p.NOZZLES[4:8]))
        # Below the regulator's supply range, positive thrust can no longer
        # carry the car. The remaining fraction is intentionally almost empty.
        s.air_mass = p.TANK_RESIDUAL+.01*(p.TANK_MASS-p.TANK_RESIDUAL)
        status = s.lift_status()
        self.assertEqual(status['reason'], 'low_pressure')
        self.assertLess(status['ratio'], 1.)
        self.assertGreater(status['force'], 0.)
        s.refill_air()
        advance(s, 2., {'fan': 1})
        status = s.lift_status()
        self.assertEqual(status['reason'], 'fan')
        self.assertGreater(status['ratio'], 1.)
        self.assertLess(status['vertical_ratio'], 1.)
        # Toggle-off does not erase residual suction instantaneously.
        advance(s, p.FIXED_DT)
        self.assertEqual(s.lift_status()['reason'], 'fan')
        advance(s, .5)
        self.assertEqual(s.lift_status()['reason'], 'ready')
        for angle in (math.pi/2, math.pi, 1.3):
            s.q = p.q_axis((0, 0, 1), angle)
            self.assertEqual(s.lift_status()['reason'], 'orientation')
            self.assertLess(s.lift_status()['vertical_ratio'], 1.)

    def test_pressure_limited_repeat_jump_works_again_after_in_place_refill(self):
        s = p.Simulation('flat')
        lift = {'nozzles': [0]*4+[1]*4}
        # Several short pulses still launch after landing; a finite reservoir
        # eventually empties. The regulator supplies rated force until then.
        for _ in range(3):
            self.assertEqual(s.lift_status()['reason'], 'ready')
            advance(s, .3, lift)
            self.assertTrue(s.airborne)
            advance(s, 8.)
            self.assertEqual(s.contact_count, 4)
        advance(s, 3., lift)
        advance(s, 20.)
        self.assertEqual(s.contact_count, 4)
        self.assertEqual(s.lift_status()['reason'], 'low_pressure')
        self.assertLess(s.air_fraction, .01)
        advance(s, .5, lift)
        self.assertLess(s.y, p.GROUND_Y+.1)
        advance(s, 2.)
        before = (s.x, s.y, s.z, s.vx, s.vy, s.vz, s.q, tuple(s.omega), s.time)
        s.refill_air()
        self.assertEqual(before, (s.x, s.y, s.z, s.vx, s.vy, s.vz, s.q, tuple(s.omega), s.time))
        self.assertEqual(s.lift_status()['reason'], 'ready')
        advance(s, .8, lift)
        self.assertGreater(s.y, 5.)
        self.assertTrue(s.airborne)

    def test_refill_does_not_reset_a_moving_tumbling_car_or_fan(self):
        s = p.Simulation('loop')
        s.y = 20.
        s.q = p.q_axis((0, 0, 1), .7)
        s.vz = 4.
        advance(s, .2, {'fan': 1, 'nozzles': [0, 0, 0, 0, 1, 0, 1, 0]})
        before = {k: v for k, v in vars(s).items()
                  if k not in ('air_mass', 'air_fraction', 'pressure', 'mass')}
        self.assertLess(s.air_mass, p.TANK_MASS)
        self.assertGreater(abs(s.omega[2]), 1.)
        s.refill_air()
        self.assertEqual(before, {k: v for k, v in vars(s).items()
                                 if k not in ('air_mass', 'air_fraction', 'pressure', 'mass')})
        self.assertEqual(s.air_mass, p.TANK_MASS)
        self.assertEqual(s.pressure, p.TANK_PRESSURE)
        self.assertEqual(s.air_fraction, 1.)
        self.assertEqual(s.mass, p.DRY_MASS+p.TANK_MASS)


class TrackAndFanTests(unittest.TestCase):
    def test_loop_and_ramp_have_real_distinct_collision_geometry(self):
        down = (0., -1., 0.)
        # The return arc exists in loop, not the open half-curl ramp.
        loop = p.raycast((0., 10., 4.), down, 20., 'loop')
        ramp = p.raycast((0., 10., 4.), down, 20., 'ramp')
        self.assertEqual(loop[2], 'track')
        self.assertEqual(ramp[2], 'ground')
        outer = p.raycast((0., 20., 10.), down, 30., 'loop')
        self.assertEqual(outer[2], 'track')
        self.assertGreater(outer[1][1], .99)
        ceiling = p.raycast((0., 15., 10.), (0., 1., 0.), 2., 'loop')
        self.assertLess(ceiling[1][1], -.99)
        side = p.raycast((5., 15., 10.), (0., 1., 0.), 2., 'loop')
        self.assertIsNone(side)

    def test_track_side_caps_resist_direct_sideways_entry(self):
        # The lower hull corners cross the narrow outer ribbon at the crown.
        s = p.Simulation('loop')
        s.x, s.y, s.z = 5., 16.25, p.LOOP_Z
        s.vx = -5.
        touched = False
        for _ in range(30):
            s.step(p.FIXED_DT)
            touched = touched or s.chassis_contacts > 0
        self.assertTrue(touched)
        self.assertGreater(s.vx, -4.)
        self.assertGreater(s.x, 4.)

    def test_loop_reset_is_inside_bottom_and_flat_is_outside(self):
        s = p.Simulation('loop')
        self.assertEqual(s.z, p.LOOP_Z)
        self.assertGreater(s.y, p.GROUND_Y)
        s.reset('flat')
        self.assertEqual(s.z, -14.)
        self.assertEqual(s.scene_name, 'flat')

    def test_moderate_power_can_drive_full_inversion_with_fan(self):
        on, off = p.Simulation('loop'), p.Simulation('loop')
        top_on, top_off = 0., 0.
        inverted_contact = False
        returned = False
        for _ in range(8*120):
            on.step(p.FIXED_DT, {'throttle': .4, 'fan': 1})
            off.step(p.FIXED_DT, {'throttle': .4})
            top_on, top_off = max(top_on, on.y), max(top_off, off.y)
            if on.y > 15. and on.basis()[1][1] < -.9 and on.contact_count == 4:
                inverted_contact = True
            if inverted_contact and on.y < 1. and on.z > p.LOOP_Z:
                returned = True
        self.assertTrue(inverted_contact)
        self.assertTrue(returned)
        self.assertGreater(top_on, 15.)
        self.assertLess(top_off, 12.)
        self.assertEqual(on.air_mass, p.TANK_MASS)

    def test_full_throttle_keeps_lapping_without_stop_or_path_attachment(self):
        s = p.Simulation('loop')
        angle, travel = 0., 0.
        for _ in range(8*120):
            s.step(p.FIXED_DT, {'throttle': 1, 'fan': 1})
            a = p._track_angle(s.y, s.z)
            travel += (a-angle+math.pi)%(2*math.pi)-math.pi
            angle = a
        self.assertGreater(travel, 4*2*math.pi)
        self.assertGreater(s.speed, 20.)
        self.assertEqual(s.time, 8.)
        # The same solver must allow driving off a track edge.
        s.x = p.LOOP_HALF_WIDTH+2.
        s.y = 12.
        s.z = 10.
        s.vx = s.vy = s.vz = 0.
        advance(s, .5, {'fan': 1})
        self.assertEqual(s.contact_count, 0)
        self.assertLess(s.y, 11.)

    def test_inverted_fan_hold_and_release_are_force_driven(self):
        s = advance(p.Simulation('flat'), 2., {'fan': 1})
        # Explicit scenario setup after spool-up; no simulator mounting helper.
        s.scene_name = 'loop'
        s.y, s.z = 15.44, p.LOOP_Z
        s.q = (0., 1., 0., 0.)
        s.vx = s.vy = s.vz = 0.
        s.omega = [0., 0., 0.]
        advance(s, 2.3, {'fan': 1, 'brake': 1})
        self.assertGreater(s.y, 15.3)
        self.assertEqual(s.contact_count, 4)
        self.assertGreater(s.fan_force, s.mass*p.GRAVITY)
        advance(s, 1.6, {'brake': 1})
        self.assertLess(s.y, 10.)
        self.assertEqual(s.contact_count, 0)
        self.assertLess(s.fan_force, 1.)

    def test_fan_does_not_levitate_away_from_a_surface(self):
        s = p.Simulation('flat')
        s.y = 20.
        advance(s, 1., {'fan': 1})
        self.assertLess(s.y, 16.)
        self.assertLess(s.fan_force, 30.)
        self.assertGreater(s.fan_speed, .9)

    def test_open_ramp_releases_into_uncontrolled_flight(self):
        s = p.Simulation('ramp')
        departed = False
        for _ in range(7*120):
            s.step(p.FIXED_DT, {'fan': 1, 'throttle': .4})
            if s.y > 13. and s.z < p.LOOP_Z-2 and s.airborne:
                departed = True
        self.assertTrue(departed)
        self.assertEqual(s.time, 7.)


if __name__ == '__main__':
    unittest.main()
