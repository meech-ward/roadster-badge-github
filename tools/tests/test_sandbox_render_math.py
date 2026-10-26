"""The native matrix builder must show the quaternion's physical orientation."""

import ast
import math
from pathlib import Path
import random
from types import SimpleNamespace
import unittest


SCENE = Path(__file__).resolve().parents[2] / "apps/roadster_sandbox/scene.py"


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


class Matrix:
    """Recorded public pico3d operations, evaluated as column-vector matrices."""

    def __init__(self):
        self.value = [[float(r == c) for c in range(4)] for r in range(4)]

    def translate(self, x, y, z):
        offset = Matrix().value
        for row, value in enumerate((x, y, z)):
            offset[row][3] = value
        self.value = multiply(self.value, offset)
        return self

    def rotate(self, axis, radians):
        result = Matrix().value
        a, b = ((1, 2), (2, 0), (0, 1))[axis]
        result[a][a] = result[b][b] = math.cos(radians)
        result[a][b], result[b][a] = -math.sin(radians), math.sin(radians)
        self.value = multiply(self.value, result)
        return self

    def rotate_x_radians(self, value):
        return self.rotate(0, value)

    def rotate_y_radians(self, value):
        return self.rotate(1, value)

    def rotate_z_radians(self, value):
        return self.rotate(2, value)


def hamilton(a, b):
    w, x, y, z = a
    s, u, v, t = b
    return (w*s-x*u-y*v-z*t, w*u+x*s+y*t-z*v,
            w*v-x*t+y*s+z*u, w*t+x*v-y*u+z*s)


def rotate_reference(q, vector):
    # Independent sandwich-product reference, not the scene's decomposition.
    w, x, y, z = q
    return hamilton(hamilton(q, (0,) + tuple(vector)), (w, -x, -y, -z))[1:]


namespace = {"math": math, "pico3d": SimpleNamespace(mat4=Matrix)}
source = ast.parse(SCENE.read_text())
body = next(node for node in source.body
            if isinstance(node, ast.FunctionDef) and node.name == "body_transform")
exec(compile(ast.Module(body=[body], type_ignores=[]), str(SCENE), "exec"), namespace)
body_transform = namespace["body_transform"]


class SandboxRenderMathTests(unittest.TestCase):
    def check_orientation(self, q):
        basis = tuple(rotate_reference(q, unit)
                      for unit in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        sim = SimpleNamespace(x=7.25, y=3.5, z=-21.0, basis=lambda: basis)
        transform = body_transform(sim).value
        # Non-axis-aligned points catch wrong multiplication order and signs.
        for local in ((.84, -.14, 1.36), (-.86, -.3, -1.72), (.3, .6, 2.1)):
            expected = rotate_reference(q, local)
            actual = [sum(transform[row][i] * local[i] for i in range(3))
                      + transform[row][3] for row in range(3)]
            for row, offset in enumerate((sim.x, sim.y, sim.z)):
                self.assertAlmostEqual(actual[row], expected[row] + offset, delta=1e-5)

    def test_random_free_body_rotations(self):
        randomizer = random.Random(91372)
        for _ in range(500):
            q = [randomizer.gauss(0, 1) for _ in range(4)]
            length = math.sqrt(sum(value * value for value in q))
            self.check_orientation(tuple(value / length for value in q))

    def test_vertical_and_near_vertical_with_yaw_and_roll(self):
        for pitch in (math.pi / 2, -math.pi / 2,
                      math.pi / 2 - 1e-7, -math.pi / 2 + 1e-7):
            for yaw in (-2.7, -.4, 0, 1.8):
                for roll in (-2.1, -.2, 0, 2.8):
                    qy = (math.cos(yaw / 2), 0, math.sin(yaw / 2), 0)
                    qx = (math.cos(pitch / 2), math.sin(pitch / 2), 0, 0)
                    qz = (math.cos(roll / 2), 0, 0, math.sin(roll / 2))
                    self.check_orientation(hamilton(hamilton(qy, qx), qz))

    def test_inverted_roof_and_side_poses(self):
        self.check_orientation((0, 1, 0, 0))
        self.check_orientation((0, 0, 0, 1))
        self.check_orientation((math.sqrt(.5), 0, 0, math.sqrt(.5)))


if __name__ == "__main__":
    unittest.main()
