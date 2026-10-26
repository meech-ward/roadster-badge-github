"""Physical behavior at the explicit 60 Hz badge budget; host checks only."""
import importlib.util
import math
from pathlib import Path
import random
import unittest

SPEC = importlib.util.spec_from_file_location(
    'step_profile_physics', Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox/physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def body(scene='flat'):
    sim = p.Simulation(scene)
    sim.set_step_hz(60)
    return sim


def advance(sim, seconds, controls=None, fps=60):
    remaining = seconds
    while remaining > 1.e-10:
        dt = min(1. / fps, remaining)
        sim.step(dt, controls)
        remaining -= dt
    return sim


def state(sim):
    return (sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
            *sim.q, *sim.omega, sim.air_mass, sim.time)


class StepProfileTests(unittest.TestCase):
    def test_defaults_and_reset_keep_selected_profile(self):
        sim = p.Simulation('flat')
        self.assertEqual(sim.fixed_dt, p.FIXED_DT)
        self.assertEqual(sim.max_substeps, p.MAX_SUBSTEPS)
        sim.set_step_hz(60)
        advance(sim, 1., {'throttle': 1})
        sim.reset()
        self.assertEqual(sim.step_hz, 60)
        self.assertEqual(sim.time, 0.)
        self.assertEqual(vars(sim), vars(body()))
        with self.assertRaises(ValueError):
            sim.set_step_hz(30)

    def test_rate_switch_preserves_body_clock_and_pending_time(self):
        sim = advance(p.Simulation('flat'), 1., {'throttle': .5}, fps=120)
        sim.step(.004)
        before, pending, ticks = state(sim), sim._accumulator, sim.total_steps
        sim.set_step_hz(60)
        self.assertEqual(state(sim), before)
        self.assertEqual(sim._accumulator, pending)
        self.assertEqual(sim.total_steps, ticks)
        sim.step(1. / 60 - pending)
        self.assertEqual(sim.time, 1. + 1. / 60)
        self.assertEqual(sim.total_steps, ticks + 1)
        sim.set_step_hz(120)
        self.assertEqual(sim.time, 1. + 1. / 60)
        sim.step(1. / 120)
        self.assertAlmostEqual(sim.time, 1.025, places=14)

    def test_sixty_hz_catchup_keeps_same_elapsed_limit(self):
        sim = body()
        self.assertEqual(sim.step(10.), 8)
        self.assertAlmostEqual(sim.time, p.MAX_SUBSTEPS * p.FIXED_DT)
        self.assertAlmostEqual(sim.dropped_time, 10. - sim.time)

    def test_render_rates_produce_identical_fixed_tick_state(self):
        results = []
        for fps in (15, 30, 60, 120):
            sim = body()
            advance(sim, 1., {'throttle': .5, 'steer': .2, 'fan': 1}, fps)
            advance(sim, .5, {'nozzles': [1, 0, 0, 1]}, fps)
            advance(sim, .5, {'brake': .4}, fps)
            results.append(state(sim))
        self.assertTrue(all(result == results[0] for result in results))

    def test_filter_and_observer_receive_sixty_hz_ticks(self):
        sim = body()
        filtered, observed = [], []
        def control(dt, car, manual):
            filtered.append((dt, car.time))
            return manual
        def observe(dt, car, values):
            observed.append((dt, car.time))
        for _ in range(30):
            sim.step(1. / 30, {'throttle': .2}, control, observe)
        self.assertEqual(filtered, [(1. / 60, i * (1. / 60)) for i in range(60)])
        self.assertEqual(observed, [(1. / 60, i * (1. / 60)) for i in range(1, 61)])

    def test_idle_drive_brake_and_fan_are_stable(self):
        idle = advance(body(), 4.)
        self.assertAlmostEqual(idle.y, p.GROUND_Y, delta=.002)
        self.assertLess(abs(idle.z + 14), .001)
        plain = advance(body(), 1., {'throttle': 1})
        fan = advance(body(), 1., {'throttle': 1, 'fan': 1})
        self.assertGreater(fan.speed, plain.speed * 1.25)
        self.assertGreater(fan.fan_force, p.FAN_MAX_FORCE*.85)
        self.assertEqual(fan.air_mass, p.TANK_MASS)
        advance(plain, 2., {'brake': 1})
        self.assertLess(abs(plain.speed), .1)

    def test_lift_flies_then_lands_without_reset(self):
        sim = advance(body(), .8, {'nozzles': [0] * 4 + [1] * 4})
        self.assertGreater(sim.y, 5.)
        self.assertGreater(sim.vy, 8.)
        self.assertTrue(sim.airborne)
        advance(sim, 6.)
        self.assertLess(sim.y, .6)
        self.assertGreater(sim.contact_count, 0)
        self.assertLess(sim.air_mass, p.TANK_MASS)

    def test_roof_landing_keeps_inverted_body_and_thrusters_work(self):
        sim = body()
        sim.y, sim.q = 1.2, (0., 0., 0., 1.)
        advance(sim, 3.)
        self.assertLess(sim.basis()[1][1], -.99)
        self.assertAlmostEqual(sim.y, (max(v[1] for v in p.HULL)+.001), delta=.015)
        self.assertGreater(sim.chassis_contacts, 0)
        start = sim.z
        advance(sim, 1., {'nozzles': [1] * 4})
        self.assertGreater(abs(sim.z - start), 1.)

    def test_fan_can_complete_inverted_loop_and_release(self):
        sim = body('loop')
        inverted = returned = False
        for _ in range(8 * 60):
            sim.step(1. / 60, {'throttle': .4, 'fan': 1})
            if sim.y > 15. and sim.basis()[1][1] < -.9 and sim.contact_count == 4:
                inverted = True
            if inverted and sim.y < 1. and sim.z > p.LOOP_Z:
                returned = True
        self.assertTrue(inverted)
        self.assertTrue(returned)
        sim.x, sim.y, sim.z = p.LOOP_HALF_WIDTH + 2., 12., 10.
        sim.vx = sim.vy = sim.vz = 0.
        advance(sim, 1., {'fan': 1})
        self.assertEqual(sim.contact_count, 0)
        self.assertLess(sim.y, 9.5)

    def test_full_throttle_loop_stays_finite_for_multiple_laps(self):
        sim = body('loop')
        angle = travel = 0.
        # The integration budget may dissipate energy at contacts, but should
        # sustain four laps within a nine-second drive.
        for _ in range(9 * 60):
            sim.step(1. / 60, {'throttle': 1, 'fan': 1})
            current = p._track_angle(sim.y, sim.z)
            travel += (current - angle + math.pi) % (2 * math.pi) - math.pi
            angle = current
        self.assertGreater(travel, 4 * 2 * math.pi)
        self.assertGreater(sim.speed, 20.)
        self.assertTrue(all(math.isfinite(value) for value in state(sim)))
        self.assertAlmostEqual(sum(value * value for value in sim.q), 1., places=8)

    def test_flat_contact_broadphase_matches_full_sample_path(self):
        # A far obstacle disables the early floor bound but cannot collide.
        # Compare the real contact impulses/projection for random orientations.
        randomizer = random.Random(60)
        far = ({'id': 'far', 'x': 1000., 'z': 1000., 'yaw': 0.,
                'length': 2., 'width': 2., 'height': 2.},)
        for _ in range(150):
            a, b = body(), p.Simulation('flat', obstacles=far)
            b.set_step_hz(60)
            q = p.q_axis(tuple(randomizer.uniform(-1, 1) for _ in range(3)),
                         randomizer.uniform(-math.pi, math.pi))
            a.q = b.q = q
            a.y = b.y = randomizer.uniform(-.1, 3.)
            a.vy = b.vy = randomizer.uniform(-4., 2.)
            a.omega = [.3, -.4, .5]
            b.omega = [.3, -.4, .5]
            a._contacts((a.x, a.y, a.z), q)
            b._contacts((b.x, b.y, b.z), q)
            self.assertEqual(state(a), state(b))
            self.assertEqual(a.chassis_contacts, b.chassis_contacts)


if __name__ == '__main__':
    unittest.main()
