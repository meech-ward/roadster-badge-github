"""Held-pad altitude assistance through the real portable rigid-body solver.

These checks cover the player's launch/hover/release/relaunch sequence and
the existing forces that the common-lift governor must leave available.
They do not establish sensor feel or frame rate on the physical badge.
"""
import copy
import importlib.util
import math
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('lift_envelope_physics', APP / 'physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location('lift_envelope_controls', APP / 'badge_controls.py')
b = importlib.util.module_from_spec(SPEC)
_previous = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    SPEC.loader.exec_module(b)
finally:
    if _previous is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = _previous


def practice(hz=60):
    sim = p.Simulation('flat')
    sim.set_step_hz(hz)
    sim.set_air_mode('unlimited')
    return sim


def held_lift(dt, sim, values):
    return b.mix_controls(('BACK',), speed=sim.speed,
                          fan=values.get('fan', 0.),
                          lift_common=b.held_lift_common(sim))


def advance(sim, seconds, controls=None, control_filter=None):
    for _ in range(round(seconds * sim.step_hz)):
        sim.step(sim.fixed_dt, controls, control_filter=control_filter)


def nozzle_torque(controls):
    torque = [0., 0., 0.]
    for duty, nozzle in zip(controls['nozzles'], p.NOZZLES):
        moment = p.cross(nozzle[1], p.scale(nozzle[2], duty * nozzle[3]))
        for axis in range(3):
            torque[axis] += moment[axis]
    return torque


class LiftEnvelopeTests(unittest.TestCase):
    def test_target_is_smooth_monotonic_and_bounded_through_hover(self):
        heights = [b.LIFT_CEILING * index / 100. for index in range(201)]
        targets = [b.lift_target_speed(height) for height in heights]
        self.assertTrue(all(a > z for a, z in zip(targets, targets[1:])))
        self.assertTrue(all(abs(value) < b.LIFT_MAX_SPEED for value in targets))
        self.assertGreater(targets[0], 0.)
        self.assertLess(targets[-1], 0.)
        self.assertAlmostEqual(b.lift_target_speed(b.LIFT_CEILING), 0.)
        # A continuously varying slope avoids the palpable changes at fixed
        # altitude bands. Check both sides of many points, including hover.
        delta = .0001
        for height in heights[1:-1]:
            center = b.lift_target_speed(height)
            left = (center - b.lift_target_speed(height - delta)) / delta
            right = (b.lift_target_speed(height + delta) - center) / delta
            self.assertLess(left, 0.)
            self.assertLess(right, 0.)
            self.assertAlmostEqual(left, right, delta=.001)

    def test_long_hold_launches_quickly_then_settles_at_both_tick_rates(self):
        trajectories = []
        checkpoints = (.2, .5, .8)
        for hz in (60, 120):
            with self.subTest(hz=hz):
                sim = practice(hz)
                crossings = {}
                maximum = 0.
                for _ in range(hz * 30):
                    sim.step(sim.fixed_dt, control_filter=held_lift)
                    height = sim.y - p.GROUND_Y
                    maximum = max(maximum, height)
                    for fraction in checkpoints:
                        if fraction not in crossings and height >= fraction * b.LIFT_CEILING:
                            crossings[fraction] = (sim.time, sim.vy)
                self.assertEqual(set(crossings), set(checkpoints))
                self.assertLess(crossings[.2][0], 1.2)
                self.assertLess(crossings[.5][0], 2.)
                self.assertLess(crossings[.8][0], 4.)
                self.assertGreater(crossings[.2][1], crossings[.5][1])
                self.assertGreater(crossings[.5][1], crossings[.8][1])
                self.assertGreater(crossings[.2][1], b.LIFT_MAX_SPEED * .5)
                self.assertLess(crossings[.8][1], crossings[.2][1] * .8)
                self.assertLessEqual(maximum, b.LIFT_CEILING + .15)
                self.assertAlmostEqual(sim.y - p.GROUND_Y, b.LIFT_CEILING, delta=.05)
                self.assertLess(abs(sim.vy), .02)
                self.assertTrue(sim.airborne)
                self.assertEqual(sim.air_mass, p.TANK_MASS)
                trajectories.append(crossings)
        for fraction in checkpoints:
            self.assertAlmostEqual(trajectories[0][fraction][0],
                                   trajectories[1][fraction][0], delta=.04)
            self.assertAlmostEqual(trajectories[0][fraction][1],
                                   trajectories[1][fraction][1], delta=.1)

    def test_upward_momentum_reduces_thrust_before_reaching_hover(self):
        slow = practice()
        slow.y = p.GROUND_Y + b.LIFT_CEILING * .6
        fast = copy.deepcopy(slow)
        fast.vy = b.LIFT_MAX_SPEED * 2.
        slow_duty = b.held_lift_common(slow)
        fast_duty = b.held_lift_common(fast)
        self.assertLess(fast_duty, slow_duty)
        slow_before, fast_before = slow.vy, fast.vy
        slow.step(slow.fixed_dt, control_filter=held_lift)
        fast.step(fast.fixed_dt, control_filter=held_lift)
        self.assertGreater(slow.vy, slow_before)
        self.assertLess(fast.vy, fast_before)
        self.assertGreater(fast.vy, fast_before - p.GRAVITY * fast.fixed_dt * 1.1)
        self.assertLess(sum(fast.nozzle_forces[4:8]), sum(slow.nozzle_forces[4:8]))

    def test_release_falls_and_landing_restores_launch_without_reset(self):
        for hz in (60, 120):
            with self.subTest(hz=hz):
                sim = practice(hz)
                advance(sim, 8., control_filter=held_lift)
                release_height, release_speed = sim.y, sim.vy
                sim.step(sim.fixed_dt, b.mix_controls(()))
                self.assertEqual(sim.nozzle_forces, [0.] * 10)
                self.assertLess(sim.vy, release_speed)
                advance(sim, 5.)
                self.assertLess(sim.y, release_height - 5.)
                self.assertFalse(sim.airborne)
                self.assertEqual(sim.contact_count, 4)
                self.assertAlmostEqual(sim.y, p.GROUND_Y, delta=.05)
                landed_time, landed_height = sim.time, sim.y
                advance(sim, 1., control_filter=held_lift)
                self.assertGreater(sim.time, landed_time)
                self.assertTrue(sim.airborne)
                self.assertGreater(sim.y, landed_height + 2.8)
                self.assertGreater(sim.vy, 2.)

    def test_a_real_parked_vehicle_roof_can_launch_in_the_slower_band(self):
        sim = practice()
        sim.set_obstacles([{'id': 'roof', 'x': 0., 'z': -14., 'yaw': 0.,
                            'collision_boxes': ((0., 2., 0., 3., 2., 4.),)}])
        sim.y = 4. + p.GROUND_Y
        advance(sim, 1.)
        self.assertEqual(sim.surface, 'vehicle:roof')
        self.assertEqual(sim.contact_count, 4)
        starting_height = sim.y
        advance(sim, 1., control_filter=held_lift)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, starting_height + 1.)
        self.assertGreater(sim.vy, 1.)
        ground = practice()
        advance(ground, 1., control_filter=held_lift)
        self.assertLess(sim.vy, ground.vy)
        self.assertLess(sim.vy, b.LIFT_MAX_SPEED)

    def test_momentum_can_cross_the_band_and_returns_without_a_pose_clamp(self):
        sim = practice()
        sim.y = p.GROUND_Y + b.LIFT_CEILING - .1
        sim.vy = 12.
        before_height, before_speed = sim.y, sim.vy
        sim.step(sim.fixed_dt, control_filter=held_lift)
        self.assertGreater(sim.y, before_height)
        self.assertGreater(sim.vy, before_speed - .2)
        peak = sim.y
        for _ in range(sim.step_hz * 25):
            sim.step(sim.fixed_dt, control_filter=held_lift)
            peak = max(peak, sim.y)
        self.assertGreater(peak - p.GROUND_Y, b.LIFT_CEILING + 3.)
        self.assertAlmostEqual(sim.y - p.GROUND_Y, b.LIFT_CEILING, delta=.05)
        self.assertLess(abs(sim.vy), .02)

    def test_fan_suction_and_pressure_still_determine_actual_takeoff(self):
        sim = practice()
        advance(sim, 1., {'fan': 1.})
        advance(sim, 2., {'fan': 1.}, held_lift)
        self.assertFalse(sim.airborne)
        self.assertEqual(sim.contact_count, 4)
        self.assertGreater(sim.fan_force, sim.mass * p.GRAVITY)
        self.assertTrue(all(force >= p.NOZZLES[i][3]*.98
                            for i, force in enumerate(sim.nozzle_forces) if 4 <= i < 8))
        self.assertEqual(held_lift(0., sim, {'fan': 1.})['fan'], 1.)

        empty = p.Simulation('flat')
        empty.set_step_hz(60)
        empty.air_mass = p.TANK_RESIDUAL
        advance(empty, 2., control_filter=held_lift)
        self.assertFalse(empty.airborne)
        self.assertAlmostEqual(empty.y, p.GROUND_Y, delta=.05)
        self.assertLess(sum(empty.nozzle_forces), .001)
        self.assertLessEqual(empty.air_mass, p.TANK_RESIDUAL)
        empty.refill_air()
        advance(empty, 1., control_filter=held_lift)
        self.assertTrue(empty.airborne)
        self.assertGreater(empty.y, p.GROUND_Y + 2.)

    def test_sideways_and_inverted_loop_height_keep_ordinary_jet_forces(self):
        for body_up in (-1., 0., .2):
            with self.subTest(body_up=body_up):
                sim = practice()
                sim.scene_name = 'loop'
                sim.y = 16.
                sim.z = p.LOOP_Z
                sim.q = p.q_axis((0., 0., 1.), math.acos(body_up))
                baseline = copy.deepcopy(sim)
                common = b.held_lift_common(sim)
                self.assertEqual(common, 1.)
                governed = b.mix_controls(('BACK',), lift_common=common)
                ordinary = b.mix_controls(('BACK',))
                self.assertEqual(governed, ordinary)
                sim.step(sim.fixed_dt, governed)
                baseline.step(baseline.fixed_dt, ordinary)
                for field in ('x', 'y', 'z', 'vx', 'vy', 'vz', 'q', 'omega',
                              'nozzle_forces', 'fan_force', 'air_mass'):
                    self.assertEqual(getattr(sim, field), getattr(baseline, field), field)

    def test_common_reduction_preserves_torque_boost_fan_and_steering(self):
        for axes in ((.25, 0.), (-.4, 0.), (0., .3), (0., -.6),
                     (.3, .4), (-.8, .7), (1., 1.)):
            for common in (0., .15, .35):
                with self.subTest(axes=axes, common=common):
                    args = {'buttons': ('BACK', 'SELECT', 'UP', 'LEFT'),
                            'fan': 1., 'speed': 10., 'axes': axes}
                    ordinary = b.mix_controls(**args)
                    governed = b.mix_controls(**args, lift_common=common)
                    for before, after in zip(nozzle_torque(ordinary), nozzle_torque(governed)):
                        self.assertAlmostEqual(before, after, delta=1.e-8)
                    for key in ('throttle', 'brake', 'steer', 'fan'):
                        self.assertEqual(governed[key], ordinary[key])
                    self.assertEqual(governed['nozzles'][:4], ordinary['nozzles'][:4])
                    self.assertEqual(governed['nozzles'][8:], ordinary['nozzles'][8:])
                    self.assertTrue(all(0. <= duty <= 1. for duty in governed['nozzles']))
                    self.assertLessEqual(sum(governed['nozzles'][4:8]),
                                         sum(ordinary['nozzles'][4:8]) + 1.e-12)

    def test_centered_leveling_keeps_its_recovery_torque_at_the_ceiling(self):
        for axis in ((1., 0., 0.), (0., 0., 1.)):
            with self.subTest(axis=axis):
                sim = practice()
                sim.y = p.GROUND_Y + b.LIFT_CEILING
                sim.q = p.q_axis(axis, .6)
                sim.omega = [.2, 0., -.3]
                sim.airborne = True
                sim.contact_count = sim.chassis_contacts = 0
                common = b.held_lift_common(sim)
                regular, limited = b.RotationControl(), b.RotationControl()
                args = {'buttons': ('BACK',), 'motion_signal': b.NO_MOTION,
                        'level_enabled': True}
                normal = regular.mix(sim.fixed_dt, sim, **args)
                governed = limited.mix(sim.fixed_dt, sim, **args, lift_common=common)
                self.assertEqual(limited.level_state, 'level')
                self.assertEqual(limited.snapshot(), regular.snapshot())
                self.assertGreater(sum(abs(v) for v in nozzle_torque(governed)), 100.)
                for before, after in zip(nozzle_torque(normal), nozzle_torque(governed)):
                    self.assertAlmostEqual(before, after, delta=1.e-8)

    def test_governor_does_not_mutate_physics_or_arm_jets_without_lift(self):
        sim = practice()
        sim.y, sim.vy = 12., 3.
        sim.q = p.q_axis((1., 0., 0.), .2)
        sim.basis()  # Warm the existing read-only pose lookup's cache.
        before = copy.deepcopy(sim.__dict__)
        common = b.held_lift_common(sim)
        self.assertGreaterEqual(common, 0.)
        self.assertLess(common, 1.)
        self.assertEqual(sim.__dict__, before)
        for buttons in ((), ('SELECT',), ('UP', 'LEFT'), ('DOWN', 'RIGHT')):
            for axes in ((0., 0.), (.4, -.2), (-1., 1.)):
                with self.subTest(buttons=buttons, axes=axes):
                    self.assertEqual(b.mix_controls(buttons, axes=axes),
                                     b.mix_controls(buttons, axes=axes, lift_common=common))
        self.assertEqual(b.mix_controls((), lift_common=common)['nozzles'], [0.] * 10)


if __name__ == '__main__':
    unittest.main()
