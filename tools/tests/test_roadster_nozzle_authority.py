"""Controller commands must follow the current physical vehicle specification.

Change mass, nozzle strength and mounting distances independently, then measure
force moments from the commanded jets. These checks guard controller behavior
against stale vehicle constants; they do not prescribe a particular layout.
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
sys.path.insert(0, str(APP))
import physics
import vehicle_spec


def load_controller(filename, solver, spec):
    source = importlib.util.spec_from_file_location('authority_'+filename, APP / (filename+'.py'))
    module = importlib.util.module_from_spec(source)
    with patch.dict(sys.modules, {'physics': solver, 'vehicle_spec': spec}):
        source.loader.exec_module(module)
    return module


def variant(force_scale, x_scale, y_scale, z_scale, mass_scale):
    solver = types.ModuleType('physics')
    solver.__dict__.update(physics.__dict__)
    solver.NOZZLES = tuple((name, (point[0]*x_scale, point[1]*y_scale, point[2]*z_scale),
                            direction, force*force_scale)
                           for name, point, direction, force in physics.NOZZLES)
    solver.INERTIA = tuple(value*mass_scale*1.15 for value in physics.INERTIA)
    spec = types.ModuleType('vehicle_spec')
    spec.__dict__.update(vehicle_spec.__dict__)
    spec.REFERENCE_MASS = vehicle_spec.REFERENCE_MASS*mass_scale
    return solver, spec


def moment(duties, nozzles):
    result = [0., 0., 0.]
    for duty, (_, anchor, direction, force) in zip(duties, nozzles):
        torque = physics.cross(anchor, physics.scale(direction, force*duty))
        for axis in range(3):
            result[axis] += torque[axis]
    return result


class NozzleAuthorityTests(unittest.TestCase):
    def test_clicky_acceleration_survives_independent_mass_and_mount_changes(self):
        for scaling in ((1., 1., 1., 1., 1.), (.7, 1.4, .8, .75, 1.2),
                        (1.6, .8, 1.2, 1.3, .8)):
            solver, spec = variant(*scaling)
            control = load_controller('badge_controls', solver, spec)
            sim = solver.Simulation('flat')
            sim.mass = spec.REFERENCE_MASS*.9
            for button, axis, sign in (('RIGHT', 2, 1.), ('LEFT', 2, -1.),
                                       ('UP', 0, -1.), ('DOWN', 0, 1.)):
                with self.subTest(scaling=scaling, button=button):
                    output = control.RotationControl().mix(.025, sim, manual=(button,))
                    torque = moment(output['nozzles'], solver.NOZZLES)[axis]
                    acceleration = torque*spec.REFERENCE_MASS/(solver.INERTIA[axis]*sim.mass)
                    self.assertAlmostEqual(acceleration*sign, control.RotationControl.ACCEL, places=7)
                    self.assertTrue(all(0. <= value <= 1. for value in output['nozzles']))

    def test_kickflip_balancing_uses_real_mounts_with_bounded_nozzles(self):
        for scaling in ((1., 1., 1., 1., 1.), (.7, 1.4, .8, .75, 1.2),
                        (1.6, .8, 1.2, 1.3, .8)):
            solver, spec = variant(*scaling)
            tricks = load_controller('tricks', solver, spec)
            previous = {}
            for command in (-1., -.5, .5, 1.):
                with self.subTest(scaling=scaling, command=command):
                    duties = [0.]*10
                    tricks.TrickController()._kickflip_nozzles(command, duties)
                    torque = moment(duties, solver.NOZZLES)
                    self.assertAlmostEqual(torque[0], 0., delta=1.e-8)
                    self.assertAlmostEqual(torque[1], 0., delta=1.e-8)
                    self.assertGreater(torque[2]*command, 0.)
                    self.assertTrue(all(-1.e-12 <= value <= 1.+1.e-12 for value in duties), duties)
                    previous[command] = torque[2]
            self.assertAlmostEqual(previous[-1.], previous[-.5]*2., places=7)
            self.assertAlmostEqual(previous[1.], previous[.5]*2., places=7)

    def test_tricks_use_current_air_supply_contract(self):
        solver, spec = variant(1., 1., 1., 1., 1.)
        requested = []
        def no_supply(air_mass):
            requested.append(air_mass)
            return 0.
        spec.supply_fraction = no_supply
        tricks = load_controller('tricks', solver, spec)
        sim = solver.Simulation('flat')
        sim.y = 10.
        sim.airborne = True
        controller = tricks.TrickController()
        self.assertTrue(controller.start_flick('right', sim))
        output = controller.update(sim.fixed_dt, sim)
        self.assertEqual(requested, [sim.air_mass])
        self.assertFalse(controller.active)
        self.assertEqual(output['nozzles'], [0.]*10)
        self.assertIn('Pressure exhausted', controller.message)


if __name__ == '__main__':
    unittest.main()
