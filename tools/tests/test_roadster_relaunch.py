"""Repeat-launch diagnosis at the badge physics budget; host checks only.

These distinguish a landing lockout from finite tank pressure, fan suction and
the car's attitude. Native input/rendering and physical badge checks are separate.
"""
import importlib.util
from pathlib import Path
import unittest
from vehicle_spec import REGULATED_PRESSURE


SPEC = importlib.util.spec_from_file_location(
    'relaunch_physics', Path(__file__).resolve().parents[2] /
    'apps/roadster_sandbox/physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
LIFT = {'nozzles': [0.] * 4 + [1.] * 4 + [0.] * 2}
LIFT_CAPACITY = sum(nozzle[3] for nozzle in p.NOZZLES[4:8])


def car():
    sim = p.Simulation('flat')
    sim.set_step_hz(60)
    return sim


def advance(sim, seconds, controls=None):
    # Approximate the measured open-area render rate while retaining ordinary
    # fixed physics ticks, including more than one physics tick per frame.
    remaining = seconds
    while remaining > 1.e-10:
        dt = min(1. / 15., remaining)
        sim.step(dt, controls)
        remaining -= dt
    return sim


class RelaunchTests(unittest.TestCase):
    def test_short_hop_lands_and_relaunches_without_reset_or_refill(self):
        sim = advance(car(), .45, LIFT)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, 1.)
        air_after_launch = sim.air_mass
        advance(sim, 8.)
        self.assertEqual(sim.contact_count, 4)
        self.assertEqual(sim.air_mass, air_after_launch)
        self.assertEqual(sim.lift_status()['reason'], 'ready')
        before_time = sim.time
        advance(sim, .45, LIFT)
        self.assertGreater(sim.time, before_time)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, 1.)
        self.assertEqual(sum(sim.nozzle_forces), LIFT_CAPACITY)
        self.assertLess(sim.air_mass, air_after_launch)

    def test_subregulated_supply_remains_weak_after_landing_without_a_lockout(self):
        sim = car()
        for _ in range(180):
            sim.step(sim.fixed_dt, LIFT)
            if sim.lift_status()['ratio'] < .8:
                break
        self.assertGreater(sim.air_mass, p.TANK_RESIDUAL)
        self.assertLess(p.tank_pressure(sim.air_mass), REGULATED_PRESSURE)
        self.assertLess(sim.lift_status()['ratio'], 1.)
        self.assertEqual(sim.lift_status()['reason'], 'low_pressure')
        air_after_launch = sim.air_mass
        advance(sim, 30.)
        self.assertGreater(sim.contact_count + sim.chassis_contacts, 0)
        self.assertEqual(sim.air_mass, air_after_launch)
        # A never-launched body with the same tank state receives exactly the
        # same nozzle forces. Landing adds no throttle/jet lock or penalty.
        unlaunched = car()
        unlaunched.air_mass = sim.air_mass
        unlaunched.mass = p.DRY_MASS + sim.air_mass
        sim.step(sim.fixed_dt, LIFT)
        unlaunched.step(unlaunched.fixed_dt, LIFT)
        self.assertEqual(sim.nozzle_forces, unlaunched.nozzle_forces)
        self.assertGreater(sum(sim.nozzle_forces), 0.)
        self.assertLess(sum(sim.nozzle_forces), sim.mass * p.GRAVITY)

    def test_fan_seals_on_landing_but_held_lift_still_delivers_force(self):
        sim = advance(car(), .8, LIFT)
        advance(sim, 8., {'fan': 1.})
        self.assertEqual(sim.contact_count, 4)
        self.assertEqual(sim.lift_status()['reason'], 'fan')
        lift_and_fan = dict(LIFT, fan=1.)
        advance(sim, .2, lift_and_fan)
        self.assertGreater(sum(sim.nozzle_forces), 40000.)
        self.assertLess(sim.y, .6)
        # Turning off the fan releases its physical suction; no reset needed.
        advance(sim, .5)
        self.assertEqual(sim.lift_status()['reason'], 'ready')
        advance(sim, .6, LIFT)
        self.assertTrue(sim.airborne)
        self.assertGreater(sim.y, 1.)


class UnlimitedAirTests(unittest.TestCase):
    def test_repeated_hops_keep_full_thrust_without_reset_or_refill(self):
        sim = car()
        sim.set_air_mode('unlimited')
        for duration in (.3, .5, .4, .5):
            before_time = sim.time
            advance(sim, duration, LIFT)
            self.assertGreater(sim.time, before_time)
            self.assertTrue(sim.airborne)
            self.assertGreater(sim.y, 1.)
            self.assertEqual(sum(sim.nozzle_forces), LIFT_CAPACITY)
            self.assertEqual(sim.air_mass, p.TANK_MASS)
            self.assertEqual(sim.air_fraction, 1.)
            self.assertEqual(sim.pressure, p.TANK_PRESSURE)
            advance(sim, 10.)
            self.assertEqual(sim.contact_count, 4)
            self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_hard_landing_does_not_reduce_unlimited_nozzle_force(self):
        sim = car()
        sim.set_air_mode('unlimited')
        advance(sim, 1., LIFT)
        advance(sim, 15.)
        self.assertGreater(sim.contact_count + sim.chassis_contacts, 0)
        # A hard landing is allowed to tumble the car. Lift remains body-local:
        # test delivered force, without demanding an automatic upright pose.
        sim.step(sim.fixed_dt, LIFT)
        self.assertEqual(sum(sim.nozzle_forces), LIFT_CAPACITY)
        self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_mode_change_preserves_motion_clock_and_fan_then_limited_consumes(self):
        sim = car()
        sim.y = 20.
        sim.q = p.q_axis((0., 0., 1.), .7)
        sim.vz = 4.
        advance(sim, .2, {'fan': 1., 'nozzles': [0.] * 4 + [1., 0., 1., 0.]})
        self.assertLess(sim.air_mass, p.TANK_MASS)
        self.assertGreater(abs(sim.omega[2]), 1.)
        self.assertGreater(sim.fan_speed, 0.)
        air_fields = ('air_mode', 'air_mass', 'air_fraction', 'pressure', 'mass')
        before = {name: value for name, value in vars(sim).items()
                  if name not in air_fields}
        sim.set_air_mode('unlimited')
        self.assertEqual(before, {name: value for name, value in vars(sim).items()
                                  if name not in air_fields})
        self.assertEqual(sim.air_mass, p.TANK_MASS)
        self.assertEqual(sim.mass, p.DRY_MASS + p.TANK_MASS)
        advance(sim, .2, {'fan': 1., 'nozzles': [1.] * 10})
        self.assertEqual(sim.air_mass, p.TANK_MASS)
        before = {name: value for name, value in vars(sim).items()
                  if name != 'air_mode'}
        sim.set_air_mode('limited')
        self.assertEqual(before, {name: value for name, value in vars(sim).items()
                                  if name != 'air_mode'})
        advance(sim, .2, LIFT)
        self.assertLess(sim.air_mass, p.TANK_MASS)
        # A regulator maintains rated force while pressure remains sufficient;
        # finite mode spends gas, rather than weakening immediately.
        self.assertGreater(p.tank_pressure(sim.air_mass), REGULATED_PRESSURE)
        self.assertEqual(sum(sim.nozzle_forces), LIFT_CAPACITY)
        remaining = sim.air_mass
        sim.set_air_mode('limited')
        advance(sim, 1.)
        self.assertEqual(sim.air_mass, remaining)

    def test_reset_preserves_explicit_supply_mode(self):
        sim = car()
        self.assertEqual(sim.air_mode, 'limited')
        for mode in ('unlimited', 'limited'):
            sim.set_air_mode(mode)
            advance(sim, .5, LIFT)
            sim.reset('ramp')
            self.assertEqual(sim.air_mode, mode)
            self.assertEqual(sim.scene_name, 'ramp')
            self.assertEqual(sim.time, 0.)
            sim.step(sim.fixed_dt, LIFT)
            if mode == 'unlimited':
                self.assertEqual(sim.air_mass, p.TANK_MASS)
            else:
                self.assertLess(sim.air_mass, p.TANK_MASS)

    def test_invalid_supply_does_not_mutate_a_running_car(self):
        sim = advance(car(), .3, LIFT)
        before = dict(vars(sim))
        with self.assertRaises(ValueError):
            sim.set_air_mode('automatic')
        self.assertEqual(vars(sim), before)


class TouchdownIntegrationTests(unittest.TestCase):
    def test_level_drops_settle_on_wheels_at_both_step_rates(self):
        for height in (5., 30., 50.):
            for hz in (60, 120):
                with self.subTest(height=height, hz=hz):
                    sim = car()
                    sim.set_step_hz(hz)
                    sim.y = height
                    advance(sim, 15.)
                    self.assertEqual(sim.contact_count, 4)
                    self.assertGreater(sim.basis()[1][1], .999)
                    self.assertAlmostEqual(sim.y, p.GROUND_Y, delta=.005)
                    self.assertEqual(sim.time, 15.)
                    if hz == 60:
                        self.assertGreater(sim.total_refinements, 0)
                        self.assertLess(sim.total_refinements, 120)
                    else:
                        self.assertEqual(sim.total_refinements, 0)

    def test_repeated_full_pressure_one_second_hops_land_upright(self):
        for hz in (60, 120):
            with self.subTest(hz=hz):
                sim = car()
                sim.set_step_hz(hz)
                sim.set_air_mode('unlimited')
                for _ in range(3):
                    advance(sim, 1., LIFT)
                    self.assertTrue(sim.airborne)
                    self.assertGreater(sim.y, 10.)
                    self.assertEqual(sum(sim.nozzle_forces), LIFT_CAPACITY)
                    advance(sim, 15.)
                    self.assertEqual(sim.contact_count, 4)
                    self.assertGreater(sim.basis()[1][1], .999)
                self.assertEqual(sim.time, 48.)
                self.assertLess(sim.total_refinements, 180)

    def test_fast_flat_driving_and_distant_fall_keep_normal_budget(self):
        coasting = car()
        coasting.vz = 80.
        advance(coasting, 2.)
        self.assertGreater(coasting.speed, 60.)
        self.assertEqual(coasting.total_refinements, 0)
        boosting = car()
        boosting.vz = 80.
        boosting.set_air_mode('unlimited')
        advance(boosting, 2., {'fan': 1., 'nozzles': [1.] * 4})
        self.assertGreater(boosting.speed, 80.)
        self.assertEqual(boosting.total_refinements, 0)
        turning = car()
        advance(turning, turning.fixed_dt)
        turning.omega[1] = 2.  # Yaw alone creates no vertical impact velocity.
        advance(turning, 2.)
        self.assertEqual(turning.contact_count, 4)
        self.assertEqual(turning.total_refinements, 0)
        falling = car()
        falling.y, falling.vy = 100., -20.
        advance(falling, 1.)
        self.assertLess(falling.vy, -29.)
        self.assertEqual(falling.total_refinements, 0)

    def test_refinement_preserves_public_ticks_callbacks_and_cached_rates(self):
        sim = car()
        sim.y, sim.vy = 2., -15.
        before = (sim.fixed_dt, sim.max_substeps, sim._fan_response,
                  sim._fan_release, sim._fan_fill)
        filtered, observed = [], []

        def control(dt, body, manual):
            filtered.append((dt, body.time, body.fixed_dt))
            return manual

        def observe(dt, body, values):
            observed.append((dt, body.time, body.fixed_dt))

        sim.step(1. / 30., {'fan': 1.}, control, observe)
        self.assertEqual(sim.last_substeps, 2)
        self.assertEqual(sim.last_refinements, 2)
        self.assertEqual(sim.total_refinements, 2)
        self.assertEqual(sim.total_steps, 2)
        self.assertEqual(sim.time, 1. / 30.)
        self.assertEqual(before, (sim.fixed_dt, sim.max_substeps, sim._fan_response,
                                  sim._fan_release, sim._fan_fill))
        self.assertEqual(filtered, [(1. / 60., i / 60., 1. / 60.) for i in range(2)])
        self.assertEqual(observed, [(1. / 60., i / 60., 1. / 60.) for i in (1, 2)])
        sim.step(0.)
        self.assertEqual(sim.last_refinements, 0)
        self.assertEqual(sim.total_refinements, 2)


if __name__ == '__main__':
    unittest.main()
