"""Compare cached jet-map drawing against an uncached force-based oracle.

These are host-only display-state checks. Native raster pixel and badge timing
checks remain separate, because recording drawing calls is not a renderer.
"""
import ast
import importlib.util
from pathlib import Path
import random
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'hud_test_physics', ROOT / 'apps/roadster_sandbox/physics.py')
physics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(physics)
from fixtures.jet_map_oracle import draw_map
CURRENT = ROOT / 'apps/roadster_sandbox/hud.py'


def load_map(path):
    calls = []
    scale = 17./max(abs(n[1][2]) for n in physics.NOZZLES)
    namespace = {'NOZZLES': physics.NOZZLES,
                 'NOZZLE_POINTS': tuple((int(290 - n[1][0] * 12),
                                         int(26 - n[1][2] * scale)) for n in physics.NOZZLES),
                 '_rect': lambda *values: calls.append(values)}
    for pen in ('BODY', 'INK', 'MUTED', 'WHITE', 'DIM', 'GOLD', 'CYAN', 'PINK'):
        namespace[pen] = pen
    functions = [node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.FunctionDef) and
                 node.name in ('_jet_pixels', '_jet_map')]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace, calls


class JetMapTests(unittest.TestCase):
    def setUp(self):
        self.before_calls = []
        self.after, self.after_calls = load_map(CURRENT)

    def assert_same_drawing(self, forces):
        self.before_calls.clear()
        self.after_calls.clear()
        colors = {key: key for key in ('BODY', 'INK', 'MUTED', 'WHITE', 'DIM', 'GOLD', 'CYAN', 'PINK')}
        active = draw_map(lambda *values: self.before_calls.append(values), forces, physics.NOZZLES, colors)
        pixels = self.after['_jet_pixels'](forces)
        self.after['_jet_map'](pixels)
        self.assertEqual(self.before_calls, self.after_calls)
        self.assertEqual(active, [i for i, length in enumerate(pixels) if length])
        return pixels

    def test_every_activation_and_line_length_boundary_preserves_drawing(self):
        for i, nozzle in enumerate(physics.NOZZLES):
            for boundary in (0., 1., nozzle[3] / 3., nozzle[3] * 2. / 3., nozzle[3]):
                for delta in (-1.e-6, 0., 1.e-6):
                    forces = [0.] * 10
                    forces[i] = boundary + delta
                    with self.subTest(nozzle=i, force=forces[i]):
                        self.assert_same_drawing(forces)

    def test_random_simultaneous_forces_preserve_drawing_and_active_telemetry(self):
        rng = random.Random(6427)
        for _ in range(1000):
            forces = [rng.uniform(-1., nozzle[3] * 1.2) for nozzle in physics.NOZZLES]
            self.assert_same_drawing(forces)

    def test_strength_changes_with_identical_pixels_reuse_the_cache_key(self):
        weak = [nozzle[3] * .04 for nozzle in physics.NOZZLES]
        stronger = [nozzle[3] * .25 for nozzle in physics.NOZZLES]
        stronger[4:8] = [nozzle[3] for nozzle in physics.NOZZLES[4:8]]
        self.assertEqual(self.assert_same_drawing(weak), self.assert_same_drawing(stronger))
        changed = stronger[:]
        changed[0] = physics.NOZZLES[0][3] * .34
        self.assertNotEqual(self.assert_same_drawing(stronger), self.assert_same_drawing(changed))
        changed = stronger[:]
        changed[5] = 1.
        self.assertNotEqual(self.assert_same_drawing(stronger), self.assert_same_drawing(changed))


if __name__ == '__main__':
    unittest.main()
