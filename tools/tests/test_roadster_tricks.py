"""Physical trajectories and cancellation for the optional nozzle presets.

Host checks only. The app's WASM/browser integration is tested separately.
"""
import importlib.util
import math
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('tricks_test_physics', APP / 'physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location('sandbox_tricks', APP / 'tricks.py')
t = importlib.util.module_from_spec(SPEC)
old_physics = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    SPEC.loader.exec_module(t)
finally:
    if old_physics is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = old_physics


def state(s):
    return (s.x, s.y, s.z, s.vx, s.vy, s.vz, *s.q, *s.omega,
            s.air_mass, s.time, s.fan_force)


def run_trick(name, fps=120, seconds=8., reserve=1.):
    s, controller = p.Simulation('flat'), t.TrickController()
    s.air_mass = p.TANK_RESIDUAL+reserve*(p.TANK_MASS-p.TANK_RESIDUAL)
    s.mass = p.DRY_MASS+s.air_mass
    s.air_fraction = reserve
    assert controller.start(name, s)
    peak = s.y
    inverted = False
    finish = None
    for _ in range(round(seconds*fps)):
        s.step(1./fps, {}, controller.update)
        peak = max(peak, s.y)
        inverted = inverted or s.basis()[1][1] < -.9
        if not controller.active and finish is None:
            finish = (s.time, s.y, s.basis()[1][1], controller.rotation,
                      max(abs(v) for v in s.omega))
    return s, controller, peak, inverted, finish


class PhysicalPresetTests(unittest.TestCase):
    def test_hop_uses_finite_lift_pulse_then_free_flight(self):
        s, c, peak, inverted, finish = run_trick('hop')
        self.assertGreater(peak, 2.)
        self.assertLess(peak, 3.)
        self.assertFalse(inverted)
        self.assertLess(finish[0], .4)
        self.assertGreater(s.air_fraction, .5)
        self.assertLess(s.air_fraction, .95)
        self.assertEqual(s.time, 8.)
        self.assertGreater(s.basis()[1][1], .99)

    def test_kickflip_completes_airborne_roll_and_brakes_with_opposite_pair(self):
        s, c, peak, inverted, finish = run_trick('kickflip')
        self.assertTrue(inverted)
        self.assertGreater(peak, 10.)
        self.assertGreater(finish[1], 8.)
        self.assertGreater(finish[2], .99)
        self.assertAlmostEqual(finish[3], 2.*math.pi, delta=.03)
        self.assertLess(finish[4], .15)
        self.assertGreater(s.air_fraction, .25)
        self.assertLess(s.air_fraction, .8)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertEqual(s.time, 8.)

    def test_spin_completes_airborne_yaw_without_barrel_roll(self):
        s, c, peak, inverted, finish = run_trick('spin')
        self.assertFalse(inverted)
        self.assertGreater(peak, 12.)
        self.assertGreater(finish[1], 3.)
        self.assertGreater(finish[2], .99)
        self.assertAlmostEqual(finish[3], 2.*math.pi, delta=.03)
        self.assertLess(finish[4], .15)
        self.assertGreater(s.air_fraction, .1)
        self.assertLess(s.air_fraction, .8)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertEqual(s.time, 8.)

    def test_backflip_completes_airborne_nose_up_rotation_and_lands_upright(self):
        s, c, peak, inverted, finish = run_trick('backflip')
        self.assertTrue(inverted)
        self.assertGreater(peak, 8.)
        self.assertLess(peak, 12.)
        self.assertGreater(finish[1], 8.)
        self.assertGreater(finish[2], .99)
        self.assertAlmostEqual(finish[3], 2.*math.pi, delta=.03)
        self.assertLess(finish[4], .15)
        self.assertGreater(s.air_fraction, .25)
        self.assertLess(s.air_fraction, .8)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertEqual(s.time, 8.)

    def test_backflip_front_pair_raises_nose_before_rear_pair_brakes(self):
        s, c = p.Simulation('flat'), t.TrickController()
        self.assertTrue(c.start('backflip', s))
        front_time = rear_time = quarter_forward = None
        for _ in range(960):
            s.step(p.FIXED_DT, {}, c.update)
            if c.phase in ('rotate', 'counter pulse'):
                front, rear = sum(s.nozzle_forces[4:6]), sum(s.nozzle_forces[6:8])
                if front > 100. and rear == 0. and front_time is None:
                    front_time = s.time
                    self.assertLess(p.q_inverse_rotate(s.q, s.omega)[0], 0.)
                if rear > 100. and front == 0. and rear_time is None:
                    rear_time = s.time
                if quarter_forward is None and c.rotation >= math.pi/2.:
                    quarter_forward = s.basis()[2]
                    self.assertLess(p.q_inverse_rotate(s.q, s.omega)[0], -1.)
            if not c.active:
                break
        self.assertIsNotNone(front_time)
        self.assertIsNotNone(rear_time)
        self.assertLess(front_time, rear_time)
        self.assertIsNotNone(quarter_forward)
        self.assertGreater(quarter_forward[1], .98)
        self.assertLess(abs(quarter_forward[0]), .01)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertEqual(s.nozzle_forces, [0.]*10)

    def test_backflip_minimum_reserve_replays_identically_at_all_render_rates(self):
        reference = run_trick('backflip', 120, reserve=.8)
        self.assertAlmostEqual(reference[4][3], 2.*math.pi, delta=.03)
        self.assertGreater(reference[4][1], 8.)
        for fps in (15, 30, 60):
            with self.subTest(fps=fps):
                for actual, expected in zip(state(run_trick('backflip', fps, reserve=.8)[0]),
                                            state(reference[0])):
                    self.assertAlmostEqual(actual, expected, delta=1.e-9)

    def test_backflip_below_reserve_is_rejected_without_physics_changes(self):
        s, c = p.Simulation('flat'), t.TrickController()
        s.air_mass = p.TANK_RESIDUAL+.799*(p.TANK_MASS-p.TANK_RESIDUAL)
        s.mass = p.DRY_MASS+s.air_mass
        s.air_fraction = .799
        before = state(s)
        self.assertFalse(c.start('backflip', s))
        self.assertIn('80% air reserve', c.message)
        self.assertEqual(state(s), before)
        self.assertFalse(c.active)

    def test_backflip_cancel_releases_jets_without_stopping_rotation(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('backflip', s)
        while c.rotation < 1.:
            s.step(p.FIXED_DT, {}, c.update)
            self.assertLess(s.time, 3.)
        before = state(s)
        c.cancel()
        self.assertEqual(state(s), before)
        old_q = s.q
        s.step(.1, {}, c.update)
        self.assertEqual(s.nozzle_forces, [0.]*10)
        self.assertNotEqual(s.q, old_q)
        self.assertLess(p.q_inverse_rotate(s.q, s.omega)[0], -1.)
        self.assertGreater(s.time, before[-2])

    def test_presets_are_identical_at_different_render_rates(self):
        for name in t.NAMES:
            with self.subTest(name=name):
                reference = state(run_trick(name, 120)[0])
                for fps in (15, 30, 60):
                    for actual, expected in zip(state(run_trick(name, fps)[0]), reference):
                        self.assertAlmostEqual(actual, expected, delta=1.e-9)

    def test_kickflip_side_jets_assist_roll_and_leveling_without_yaw_flick(self):
        for reserve in (1., .8):
            with self.subTest(reserve=reserve):
                s, c = p.Simulation('flat'), t.TrickController()
                s.air_mass = p.TANK_RESIDUAL+reserve*(p.TANK_MASS-p.TANK_RESIDUAL)
                s.mass = p.DRY_MASS+s.air_mass
                s.air_fraction = reserve
                self.assertTrue(c.start('kickflip', s))
                side_peak, max_heading = [0., 0.], 0.
                assists = brakes = levels = 0
                for _ in range(960):
                    s.step(p.FIXED_DT, {}, c.update)
                    if c.phase == 'lift':
                        self.assertEqual(s.nozzle_forces[:4], [0.]*4)
                        self.assertEqual(s.nozzle_forces[8:], [0.]*2)
                    self.assertNotEqual(c.phase, 'side counter')
                    for i in range(2):
                        side_peak[i] = max(side_peak[i], s.nozzle_forces[8+i])
                    torques = [p.cross(n[1], p.scale(n[2], force))
                               for n, force in zip(p.NOZZLES, s.nozzle_forces)]
                    total = [sum(torque[axis] for torque in torques) for axis in range(3)]
                    if s.nozzle_forces[8] > 1.:
                        assists += 1
                        self.assertEqual(c.phase, 'rotate')
                        self.assertGreater(total[2], torques[8][2])
                        self.assertGreater(torques[8][2], 0.)
                        self.assertEqual(s.nozzle_forces[9], 0.)
                    if s.nozzle_forces[9] > 1.:
                        brakes += 1
                        self.assertEqual(c.phase, 'counter pulse')
                        self.assertLess(total[2], torques[9][2])
                        self.assertLess(torques[9][2], 0.)
                        self.assertEqual(s.nozzle_forces[8], 0.)
                        if c.rotation > 5.8 and s.basis()[1][1] > .8:
                            levels += 1
                    # Check actual mountings/forces: the compensating rear and
                    # lift nozzles remove pitch/yaw torque, retaining side roll.
                    self.assertAlmostEqual(total[0], 0., delta=1.e-8)
                    self.assertAlmostEqual(total[1], 0., delta=1.e-8)
                    forward = s.basis()[2]
                    max_heading = max(max_heading, abs(math.atan2(forward[0], forward[2])))
                    if not c.active:
                        break
                self.assertFalse(c.active)
                self.assertTrue(all(force > 100. for force in side_peak))
                self.assertGreater(assists, 10)
                self.assertGreater(brakes, 10)
                self.assertGreater(levels, 10)
                self.assertLess(max_heading, .01)
                self.assertLess(abs(s.x), 1.)
                self.assertAlmostEqual(c.rotation, 2.*math.pi, delta=.03)
                self.assertGreater(s.basis()[1][1], .99)
                self.assertLess(max(abs(v) for v in s.omega), .15)
                self.assertGreater(s.y, 1.)
                self.assertEqual(s.nozzle_forces, [0.]*10)

    def test_kickflip_at_minimum_reserve_is_identical_across_render_rates(self):
        reference = state(run_trick('kickflip', 120, reserve=.8)[0])
        for fps in (15, 30, 60):
            with self.subTest(fps=fps):
                for actual, expected in zip(state(run_trick('kickflip', fps, reserve=.8)[0]), reference):
                    self.assertAlmostEqual(actual, expected, delta=1.e-9)

    def test_update_never_mutates_pose_motion_air_or_manual_dictionary(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('kickflip', s)
        manual = {'throttle': .5, 'steer': .2, 'brake': .3,
                  'fan': 1., 'nozzles': [0.]*10}
        for _ in range(360):
            before = state(s)
            output = c.update(p.FIXED_DT, s, manual)
            self.assertEqual(state(s), before)
            self.assertEqual(manual['nozzles'], [0.]*10)
            self.assertEqual(manual['fan'], 1.)
            self.assertEqual(output['fan'], 1.)
            self.assertEqual(output['throttle'], .5)
            self.assertEqual(output['steer'], .2)
            self.assertEqual(output['brake'], .3)
            self.assertTrue(all(0. <= v <= 1. for v in output['nozzles']))
            s.step(p.FIXED_DT, output)

    def test_every_manual_nozzle_fires_with_fan_on_and_spends_air(self):
        for nozzle in range(10):
            with self.subTest(nozzle=nozzle):
                s = p.Simulation('flat')
                for _ in range(240):
                    s.step(p.FIXED_DT, {'fan': 1.})
                duties = [0.]*10
                duties[nozzle] = 1.
                s.step(.05, {'fan': 1., 'nozzles': duties})
                self.assertGreater(s.nozzle_forces[nozzle], 1000.)
                self.assertLess(s.air_mass, p.TANK_MASS)
                self.assertGreater(s.fan_force, s.mass*p.GRAVITY*2.)
                self.assertGreater(s.fan_speed, .99)

    def test_presets_fire_against_fan_but_wait_for_actual_liftoff(self):
        for name in t.NAMES:
            with self.subTest(name=name):
                s, c = p.Simulation('flat'), t.TrickController()
                for _ in range(240):
                    s.step(p.FIXED_DT, {'fan': 1.})
                self.assertTrue(c.start(name, s))
                s.step(.1, {'fan': 1.}, c.update)
                self.assertIn('jets firing; fan holds car down', c.message)
                self.assertTrue(all(force > 10000. for force in s.nozzle_forces[4:8]))
                for _ in range(264):
                    s.step(p.FIXED_DT, {'fan': 1.}, c.update)
                self.assertEqual(c.active, name)
                self.assertEqual(c.phase, 'lift')
                self.assertEqual(c.rotation, 0.)
                self.assertFalse(s.airborne)
                self.assertLess(s.air_fraction, .6)
                self.assertGreater(s.fan_force, s.mass*p.GRAVITY*2.)
                self.assertTrue(all(force > 0. for force in s.nozzle_forces[4:8]))
                c.cancel()
                s.step(.1, {'fan': 1.}, c.update)
                self.assertEqual(s.nozzle_forces, [0.]*10)
                self.assertGreater(s.fan_force, s.mass*p.GRAVITY*2.)

    def test_turning_fan_off_allows_waiting_kickflip_to_launch_at_any_render_rate(self):
        reference = None
        for fps in (120, 15, 30, 60):
            with self.subTest(fps=fps):
                s, c = p.Simulation('flat'), t.TrickController()
                for _ in range(2*fps):
                    s.step(1./fps, {'fan': 1.})
                c.start('kickflip', s)
                for _ in range(round(.2*fps)):
                    s.step(1./fps, {'fan': 1.}, c.update)
                completed_height = None
                for _ in range(4*fps):
                    s.step(1./fps, {'fan': 0.}, c.update)
                    if not c.active and completed_height is None:
                        completed_height = s.y
                self.assertIsNotNone(completed_height)
                self.assertGreater(completed_height, 2.)
                self.assertFalse(c.active)
                self.assertAlmostEqual(c.rotation, 2.*math.pi, delta=.03)
                self.assertGreater(s.basis()[1][1], .99)
                self.assertLess(max(abs(v) for v in s.omega), .15)
                if reference is None:
                    reference = state(s)
                else:
                    for actual, expected in zip(state(s), reference):
                        self.assertAlmostEqual(actual, expected, delta=1.e-9)

    def test_fan_can_be_enabled_during_airborne_preset_without_cancelling(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('kickflip', s)
        for _ in range(120):
            s.step(p.FIXED_DT, {'fan': 0.}, c.update)
        self.assertTrue(s.airborne)
        self.assertEqual(c.active, 'kickflip')
        s.step(.1, {'fan': 1.}, c.update)
        self.assertEqual(c.active, 'kickflip')
        for _ in range(240):
            s.step(p.FIXED_DT, {'fan': 1.}, c.update)
        self.assertFalse(c.active)
        self.assertAlmostEqual(c.rotation, 2.*math.pi, delta=.03)
        self.assertGreater(s.basis()[1][1], .99)
        self.assertGreater(s.fan_speed, .99)
        self.assertEqual(s.nozzle_forces, [0.]*10)

    def test_guard_messages_explain_inverted_tumbling_and_low_pressure_starts(self):
        s, c = p.Simulation('flat'), t.TrickController()
        s.q = (0., 0., 0., 1.)
        before = state(s)
        self.assertFalse(c.start('kickflip', s))
        self.assertIn('upright', c.message)
        self.assertEqual(state(s), before)
        s.reset()
        s.omega[2] = 2.
        self.assertFalse(c.start('spin', s))
        self.assertIn('settle', c.message)
        s.reset()
        s.air_fraction = .3
        self.assertFalse(c.start('hop', s))
        self.assertIn('T refills air in place', c.message)
        self.assertFalse(c.active)

    def test_failed_new_request_disarms_previous_preset_without_changing_physics(self):
        s, c = p.Simulation('flat'), t.TrickController()
        self.assertTrue(c.start('hop', s))
        before = state(s)
        self.assertFalse(c.start('unknown', s))
        self.assertFalse(c.active)
        self.assertEqual(state(s), before)
        self.assertEqual(c.update(p.FIXED_DT, s, {}), {})

    def test_minimum_reserves_produce_recognizable_airborne_tricks(self):
        for name, reserve in (('hop', .55), ('kickflip', .80), ('spin', .92), ('backflip', .80)):
            with self.subTest(name=name):
                s, c = p.Simulation('flat'), t.TrickController()
                s.air_mass = p.TANK_RESIDUAL+reserve*(p.TANK_MASS-p.TANK_RESIDUAL)
                s.mass = p.DRY_MASS+s.air_mass
                s.air_fraction = reserve
                self.assertTrue(c.start(name, s))
                for _ in range(960):
                    s.step(p.FIXED_DT, {}, c.update)
                    if not c.active:
                        break
                self.assertFalse(c.active)
                self.assertGreater(s.y, 1.)
                self.assertGreaterEqual(s.air_mass, p.TANK_RESIDUAL)
                self.assertLess(s.air_fraction, reserve)
                if name != 'hop':
                    self.assertAlmostEqual(c.rotation, 2.*math.pi, delta=.03)
                    self.assertGreater(s.basis()[1][1], .99)

    def test_pressure_loss_releases_nozzles_without_overspending_or_stopping(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('kickflip', s)
        for _ in range(100):
            s.step(p.FIXED_DT, {}, c.update)
        self.assertTrue(c.active)
        # Scenario setup: the remaining charge is lost during the maneuver.
        s.air_mass = p.TANK_RESIDUAL
        s.mass = p.DRY_MASS+s.air_mass
        previous_time, previous_y = s.time, s.y
        s.step(.1, {}, c.update)
        self.assertFalse(c.active)
        self.assertIn('Pressure exhausted', c.message)
        self.assertEqual(s.air_mass, p.TANK_RESIDUAL)
        self.assertEqual(s.nozzle_forces, [0.]*10)
        self.assertGreater(s.time, previous_time)
        self.assertNotEqual(s.y, previous_y)

    def test_manual_nozzles_cancel_immediately_and_are_not_swallowed(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('kickflip', s)
        s.step(.1, {}, c.update)
        before = state(s)
        manual = {'fan': 1., 'nozzles': [1., 0., 0., 0.]}
        self.assertEqual(c.update(p.FIXED_DT, s, manual), manual)
        self.assertFalse(c.active)
        self.assertEqual(state(s), before)
        s.step(p.FIXED_DT, manual, c.update)
        self.assertGreater(s.nozzle_forces[0], 0.)
        self.assertEqual(sum(s.nozzle_forces[1:]), 0.)

    def test_cancel_releases_forces_without_freezing_freefall(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('spin', s)
        for _ in range(120):
            s.step(p.FIXED_DT, {}, c.update)
        before = state(s)
        c.cancel()
        self.assertEqual(state(s), before)
        y, vy = s.y, s.vy
        s.step(.1, {}, c.update)
        self.assertEqual(s.nozzle_forces, [0.]*10)
        self.assertGreater(s.y, y)
        self.assertLess(s.vy, vy)
        self.assertEqual(s.time, 1.1)

    def test_restart_repeats_and_external_reset_disarms_pending_sequence(self):
        s, c = p.Simulation('flat'), t.TrickController()
        outcomes = []
        for _ in range(2):
            s.reset()
            c.reset()
            self.assertTrue(c.start('kickflip', s))
            for _ in range(360):
                s.step(p.FIXED_DT, {}, c.update)
            outcomes.append(state(s))
        self.assertEqual(outcomes[0], outcomes[1])
        s.reset()
        c.reset()
        c.start('spin', s)
        s.step(.1, {}, c.update)
        s.reset()
        self.assertEqual(c.update(p.FIXED_DT, s, {}), {})
        self.assertFalse(c.active)

    def test_impact_releases_preset_and_never_rights_the_car(self):
        s, c = p.Simulation('flat'), t.TrickController()
        c.start('kickflip', s)
        for _ in range(100):
            s.step(p.FIXED_DT, {}, c.update)
        self.assertTrue(c.active)
        # Explicit scenario setup: the car strikes a surface while rolling.
        s.airborne = False
        before = state(s)
        output = c.update(p.FIXED_DT, s, {})
        self.assertFalse(c.active)
        self.assertEqual(state(s), before)
        self.assertEqual(output['nozzles'], [0.]*10)
        self.assertIn('contact', c.message)


if __name__ == '__main__':
    unittest.main()
