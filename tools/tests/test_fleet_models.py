"""Portable geometry checks; native rendering is exercised by playground e2e."""
import hashlib
import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "fleet_models", ROOT / "apps" / "roadster_sandbox" / "fleet_models.py")
fleet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet)


def world_vertices(model):
    for _name, positions, _indices, _colors, pivot in model["parts"]:
        for i in range(0, len(positions), 3):
            yield tuple(positions[i + axis] + pivot[axis] for axis in range(3))


def shape_fingerprint(model):
    """Ignore paint and scale, so a fleet of recolored/scaled clones fails."""
    scale = (model["width"], model["height"], model["length"])
    points = sorted(tuple(round(point[axis] / scale[axis], 5)
                          for axis in range(3))
                    for point in world_vertices(model))
    return hashlib.sha256(repr(points).encode("ascii")).hexdigest()


class FleetModelTests(unittest.TestCase):
    def test_catalog_has_requested_ten_vehicles_and_collision_envelopes(self):
        self.assertEqual({entry["id"] for entry in fleet.CATALOG}, {
            "original_roadster", "model_s", "model_3", "model_x", "model_y",
            "model_y_l", "cybertruck", "semi", "cybercab", "robovan"})
        self.assertEqual(len(fleet.CATALOG), 10)
        for entry in fleet.CATALOG:
            with self.subTest(vehicle=entry["id"]):
                for key in ("length", "width", "height"):
                    self.assertGreater(entry[key], 0)
                self.assertTrue(entry["name"])
                self.assertGreaterEqual(len(entry["collision_boxes"]), 2)
                for x, y, z, hx, hy, hz in entry["collision_boxes"]:
                    self.assertTrue(all(math.isfinite(value)
                                        for value in (x,y,z,hx,hy,hz)))
                    self.assertTrue(all(value > 0 for value in (hx,hy,hz)))
                    self.assertGreaterEqual(y-hy, 0)
                    self.assertLessEqual(y+hy, entry["height"]+.01)
                    self.assertLessEqual(abs(x)+hx, entry["width"]*.5+.01)
                    self.assertLessEqual(abs(z)+hz, entry["length"]*.5+.01)

    def test_all_mesh_buffers_and_reported_counts_are_valid(self):
        for entry in fleet.CATALOG:
            for detail in ("high", "low"):
                with self.subTest(vehicle=entry["id"], detail=detail):
                    model = fleet.build_model(entry["id"], detail)
                    vertex_count = triangle_count = 0
                    names = []
                    for name, positions, indices, colors, pivot in model["parts"]:
                        names.append(name)
                        self.assertEqual(len(pivot), 3)
                        self.assertTrue(all(math.isfinite(value) for value in pivot))
                        self.assertEqual(len(positions) % 3, 0)
                        self.assertEqual(len(indices) % 3, 0)
                        self.assertEqual(len(colors), len(positions)//3)
                        self.assertTrue(all(math.isfinite(value) for value in positions))
                        self.assertTrue(all(isinstance(i, int) and 0 <= i < len(colors)
                                            for i in indices))
                        self.assertTrue(all(isinstance(c, int) and 0 <= c <= 0xffffff
                                            for c in colors))
                        self.assertTrue(indices)
                        vertex_count += len(colors)
                        triangle_count += len(indices)//3
                    self.assertEqual(vertex_count, model["vertices"])
                    self.assertEqual(triangle_count, model["triangles"])
                    self.assertEqual(len(names), len(set(names)))
                    self.assertLessEqual(len(names), 10)

    def test_meshes_stay_within_declared_dimensions_and_touch_ground(self):
        # Small tolerance allows the intentionally raised panel seams and lamps.
        tolerance = .03
        for entry in fleet.CATALOG:
            for detail in ("high", "low"):
                with self.subTest(vehicle=entry["id"], detail=detail):
                    model = fleet.build_model(entry["id"], detail)
                    points = list(world_vertices(model))
                    self.assertAlmostEqual(min(p[1] for p in points), 0., places=5)
                    self.assertLessEqual(max(p[1] for p in points), entry["height"]+tolerance)
                    self.assertGreaterEqual(max(p[1] for p in points), entry["height"]-.01)
                    self.assertLessEqual(max(abs(p[0]) for p in points), entry["width"]*.5+tolerance)
                    self.assertLessEqual(max(abs(p[2]) for p in points), entry["length"]*.5+tolerance)
                    self.assertGreater(max(p[2] for p in points)-min(p[2] for p in points), entry["length"]-.02)

    def test_wheel_pivots_are_local_and_semi_has_three_axles(self):
        for entry in fleet.CATALOG:
            for detail in ("high", "low"):
                with self.subTest(vehicle=entry["id"], detail=detail):
                    model = fleet.build_model(entry["id"], detail)
                    wheels = [part for part in model["parts"] if part[0].startswith("wheel_")]
                    self.assertEqual(len(wheels), 6 if entry["id"] == "semi" else 4)
                    axles = set()
                    for name, positions, _indices, _colors, pivot in model["parts"]:
                        if not name.startswith("wheel_"):
                            self.assertEqual(pivot, (0.,0.,0.))
                            continue
                        axles.add(pivot[2])
                        self.assertAlmostEqual(pivot[1], model["wheel_radius"])
                        self.assertLess(max(abs(x) for x in positions[0::3]), .19)
                        self.assertAlmostEqual(min(positions[1::3]), -model["wheel_radius"], places=5)
                        self.assertAlmostEqual(max(positions[1::3]), model["wheel_radius"], places=5)
                    self.assertEqual(len(axles), 3 if entry["id"] == "semi" else 2)

    def test_semi_front_glass_faces_outward_on_both_halves(self):
        # Mirroring vertices without reversing triangle winding hides one half
        # of the windshield in the native renderer's back-face culling pass.
        for detail in ("high", "low"):
            model = fleet.build_model("semi", detail)
            glass = next(part for part in model["parts"] if part[0] == "wraparound_glass")
            positions, indices = glass[1], glass[2]
            visible_halves = set()
            for i in range(0, len(indices), 3):
                a,b,c = [positions[index*3:index*3+3] for index in indices[i:i+3]]
                if min(point[2] for point in (a,b,c)) < 2.35:
                    continue
                if min(point[1] for point in (a,b,c)) < 2.4:
                    continue
                u = [b[j]-a[j] for j in range(3)]
                v = [c[j]-a[j] for j in range(3)]
                normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2],
                          u[0]*v[1]-u[1]*v[0])
                self.assertGreater(normal[1], 0)
                self.assertGreater(normal[2], 0)
                visible_halves.add(1 if sum(point[0] for point in (a,b,c)) > 0 else -1)
            self.assertEqual(visible_halves, {-1, 1})

    def test_vehicle_geometry_is_distinct_even_without_color_or_scale(self):
        for detail in ("high", "low"):
            fingerprints = [shape_fingerprint(fleet.build_model(entry["id"], detail))
                            for entry in fleet.CATALOG]
            self.assertEqual(len(set(fingerprints)), len(fleet.CATALOG))
        catalog = {entry["id"]: entry for entry in fleet.CATALOG}
        self.assertGreater(catalog["model_y_l"]["length"], catalog["model_y"]["length"])
        self.assertGreater(catalog["model_y_l"]["height"], catalog["model_y"]["height"])
        self.assertGreater(catalog["model_x"]["height"], catalog["model_s"]["height"])
        self.assertLess(catalog["original_roadster"]["height"], catalog["model_3"]["height"])

    def test_lod_has_bounded_cost_and_identical_collision_dimensions(self):
        total_low, upgrades = 0, []
        for entry in fleet.CATALOG:
            with self.subTest(vehicle=entry["id"]):
                high = fleet.build_model(entry["id"])
                low = fleet.build_model(entry["id"], "low")
                self.assertGreaterEqual(high["triangles"], 500)
                self.assertLessEqual(high["triangles"], 1000)
                self.assertLessEqual(low["triangles"], 200)
                self.assertLess(low["triangles"], high["triangles"]*.40)
                for key in ("length", "width", "height", "wheel_radius", "collision_boxes"):
                    self.assertEqual(high[key], low[key])
                total_low += low["triangles"]
                upgrades.append(high["triangles"]-low["triangles"])
        self.assertLessEqual(total_low+sum(sorted(upgrades, reverse=True)[:2]), 5000)

    def test_build_is_deterministic_and_does_not_reuse_mutable_buffers(self):
        first = fleet.build_model("cybertruck")
        second = fleet.build_model("cybertruck")
        self.assertEqual(first, second)
        first["parts"][0][1][0] = 999.
        self.assertNotEqual(first["parts"][0][1], second["parts"][0][1])
        self.assertNotEqual(first["parts"][0][1], fleet.build_model("cybertruck")["parts"][0][1])

    def test_unknown_models_or_lod_are_rejected(self):
        with self.assertRaises(ValueError):
            fleet.build_model("missing")
        with self.assertRaises(ValueError):
            fleet.build_model("model_s", "invalid")


if __name__ == "__main__":
    unittest.main()
