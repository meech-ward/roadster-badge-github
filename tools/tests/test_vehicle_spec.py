"""Conservation and geometry contracts for the project's vehicle design."""
import importlib.util
import math
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[2]/'apps/roadster_sandbox/vehicle_spec.py'
LOADER = importlib.util.spec_from_file_location('test_vehicle_design', PATH)
V = importlib.util.module_from_spec(LOADER)
LOADER.loader.exec_module(V)


class VehicleDesignTests(unittest.TestCase):
    def test_suspension_static_equilibrium_matches_rendered_wheels(self):
        load = V.REFERENCE_MASS*V.GRAVITY/4.
        for x, mount, z in V.WHEELS:
            compression = V.WHEEL_REACH-(V.GROUND_Y+mount)
            self.assertGreater(compression, 0.)
            self.assertLess(compression, V.SPRING_TRAVEL)
            self.assertAlmostEqual(V.SPRING*compression, load)
            hub = V.GROUND_Y+mount-V.WHEEL_REACH+V.WHEEL_RADIUS+compression
            self.assertAlmostEqual(hub, V.WHEEL_RADIUS)
        # Every physically realizable diagonal inertia obeys triangle bounds.
        self.assertTrue(all(math.isfinite(i) and i > 0 for i in V.INERTIA))
        for axis in range(3):
            self.assertLess(V.INERTIA[axis], sum(V.INERTIA)-V.INERTIA[axis])

    def test_symmetric_lift_has_no_pitch_yaw_or_roll_moment(self):
        force, torque = [0.]*3, [0.]*3
        for _, p, direction, capacity in V.NOZZLES[4:8]:
            f = tuple(d*capacity for d in direction)
            for axis in range(3):
                force[axis] += f[axis]
            torque[0] += p[1]*f[2]-p[2]*f[1]
            torque[1] += p[2]*f[0]-p[0]*f[2]
            torque[2] += p[0]*f[1]-p[1]*f[0]
        self.assertEqual(force[0], 0.)
        self.assertEqual(force[2], 0.)
        self.assertTrue(all(abs(t) < 1.e-9 for t in torque))
        self.assertGreater(force[1]/V.REFERENCE_MASS-V.GRAVITY, 20.)
        rear_acceleration = sum(n[3] for n in V.NOZZLES[:4])/V.REFERENCE_MASS
        self.assertLess(26.8224/rear_acceleration, 1.)

    def test_regulator_holds_thrust_then_fades_at_empty(self):
        self.assertAlmostEqual(V.tank_pressure(V.TANK_MASS), V.TANK_PRESSURE)
        self.assertAlmostEqual(V.tank_pressure(V.TANK_RESIDUAL), V.AMBIENT_PRESSURE)
        self.assertEqual(V.supply_fraction(V.TANK_MASS), 1.)
        self.assertEqual(V.supply_fraction(V.TANK_MASS*.5), 1.)
        self.assertEqual(V.supply_fraction(0.), 0.)
        self.assertEqual(V.supply_fraction(-1.), 0.)
        values = [V.supply_fraction(V.TANK_RESIDUAL+i*.1) for i in range(100)]
        self.assertEqual(values, sorted(values))
        self.assertTrue(any(0. < f < 1. for f in values))

    def test_shared_supply_conserves_mass_and_impulse_until_empty(self):
        forces = [0.]*10
        duties = [1., .3, 0., .7, .2, 1., .4, .8, .6, 0.]
        for start in (V.TANK_MASS, V.TANK_RESIDUAL+.002):
            gas = start
            impulse = 0.
            for _ in range(1000):
                used = V.meter_nozzles(duties, gas, .025, forces)
                self.assertGreaterEqual(used, 0.)
                self.assertLessEqual(used, gas-V.TANK_RESIDUAL+1.e-12)
                impulse += sum(forces)*.025
                gas -= used
            self.assertAlmostEqual(impulse, (start-gas)*V.EXHAUST_SPEED, places=7)
            self.assertGreaterEqual(gas, V.TANK_RESIDUAL-1.e-12)
            self.assertLess(gas-V.TANK_RESIDUAL, 1.e-8)

    def test_last_gas_is_shared_without_favoring_a_nozzle(self):
        duties = [1.]*10
        forces = [0.]*10
        start = V.TANK_RESIDUAL+.001
        used = V.meter_nozzles(duties, start, 100., forces)
        self.assertAlmostEqual(used, start-V.TANK_RESIDUAL)
        scale = forces[0]/V.NOZZLES[0][3]
        self.assertGreater(scale, 0.)
        for index in range(10):
            self.assertAlmostEqual(forces[index]/V.NOZZLES[index][3], scale)
        self.assertAlmostEqual(sum(forces)*100., used*V.EXHAUST_SPEED)

    def test_released_valves_clear_output_without_consuming_gas(self):
        forces = [100.]*10
        self.assertEqual(V.meter_nozzles([0.]*10, V.TANK_MASS, .025, forces), 0.)
        self.assertEqual(forces, [0.]*10)


if __name__ == '__main__':
    unittest.main()
