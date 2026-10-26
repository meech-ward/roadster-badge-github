"""Rear boost force, acceleration and propellant contracts; host checks only."""
from pathlib import Path
import importlib.util
import sys
import unittest

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'

def load(name, filename, physics=None):
    spec = importlib.util.spec_from_file_location(name, APP / filename)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get('physics')
    if physics is not None:
        sys.modules['physics'] = physics
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop('physics', None)
        else:
            sys.modules['physics'] = previous
    return module

new = load('boost_physics', 'physics.py')
new_controls = load('boost_controls', 'badge_controls.py', new)
feedback = load('boost_feedback', 'speed_feedback.py', new)

def launch(engine, hz, air, boost=True, fan=0.):
    sim = engine.Simulation('flat')
    sim.set_step_hz(hz)
    sim.set_air_mode(air)
    meter = feedback.SpeedFeedback()
    meter.reset(sim)
    air_ticks = impact_ticks = 0
    for unused in range(4 * hz):
        sim.step(sim.fixed_dt, {'throttle': 1., 'fan': fan,
                               'nozzles': [float(boost)]*4 + [0.]*6},
                 observer=meter.update)
        air_ticks += int(sim.airborne)
        impact_ticks += int(bool(sim.chassis_contacts))
        if meter.run_state == 'complete':
            break
    return {'hz': hz, 'air': air, 'boost': boost, 'fan': fan,
            'time': meter.run_time, 'state': meter.run_state,
            'reason': meter.run_reason, 'air_mass': sim.air_mass,
            'airborne_ticks': air_ticks, 'impact_ticks': impact_ticks}


def forces(engine, duties):
    return tuple(n[3]*d for n, d in zip(engine.NOZZLES, duties))


class BoostCalibrationTests(unittest.TestCase):
    def test_badge_select_delivers_all_four_rear_nozzles(self):
        command = new_controls.mix_controls(('UP', 'SELECT'))
        self.assertEqual(command['nozzles'], [1.]*4+[0.]*6)
        self.assertEqual(command['throttle'], 1.)
        self.assertEqual(sum(forces(new, command['nozzles'])),
                         sum(nozzle[3] for nozzle in new.NOZZLES[:4]))
        self.assertGreater(sum(forces(new, command['nozzles']))/new.REFERENCE_MASS, 27.)

    def test_launch_below_one_second_all_rates_and_air_modes(self):
        rows = []
        for hz in (40, 60, 120):
            for air in ('limited', 'unlimited'):
                for fan in (0., 1.):
                    row = launch(new, hz, air, fan=fan)
                    rows.append(row)
                    self.assertEqual(row['state'], 'complete', row)
                    self.assertLess(row['time'], 1., row)
                    self.assertGreater(row['time'], .4, row)
                    self.assertEqual(row['airborne_ticks'], 0, row)
                    self.assertEqual(row['impact_ticks'], 0, row)

    def test_ordinary_force_torque_and_propellant_accounting(self):
        for hz in (40, 60, 120):
            for nozzle in range(10):
                s = new.Simulation('flat')
                s.set_step_hz(hz)
                duties = [0.]*10
                duties[nozzle] = .6
                s._f = [0.]*3
                s._t = [0.]*3
                s._thrusters(duties)
                layout = new.NOZZLES[nozzle]
                expected = tuple(v*layout[3]*.6 for v in layout[2])
                expected_torque = new.cross(layout[1], expected)
                for actual, wanted in zip(s._f+s._t, expected+expected_torque):
                    self.assertAlmostEqual(actual, wanted, places=8)
                self.assertAlmostEqual(new.TANK_MASS-s.air_mass,
                                       layout[3]*.6/hz/new.EXHAUST_SPEED, places=10)

    def test_boost_is_faster_than_motor_only_without_leaving_the_ground(self):
        for hz in (40, 60, 120):
            ordinary = launch(new, hz, 'unlimited', boost=False)
            boosted = launch(new, hz, 'unlimited')
            self.assertEqual(ordinary['state'], 'complete')
            self.assertGreater(ordinary['time'], 2.)
            self.assertLess(ordinary['time'], 3.5)
            self.assertLess(boosted['time'], ordinary['time'] / 2.)
            self.assertEqual(boosted['airborne_ticks'], 0)

    def test_roll_assist_during_boost_has_no_unwanted_yaw_torque(self):
        for roll in (-1., -.5, .5, 1.):
            for pitch in (-1., 0., 1.):
                command = new_controls.mix_controls(('SELECT',), axes=(roll, pitch))
                torque = [0., 0., 0.]
                for nozzle, force in zip(new.NOZZLES, forces(new, command['nozzles'])):
                    moment = new.cross(nozzle[1], tuple(force * v for v in nozzle[2]))
                    torque = [a + b for a, b in zip(torque, moment)]
                self.assertAlmostEqual(torque[1], 0., places=9)
                self.assertTrue(all(0. <= duty <= 1. for duty in command['nozzles']))

if __name__ == '__main__':
    unittest.main()
