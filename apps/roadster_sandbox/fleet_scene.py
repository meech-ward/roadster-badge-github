"""Bounded native rendering of the parked collection; collision is independent."""
import gc
import pico3d
from fleet_models import build_model
from distant_models import build_distant_model
from model_assets import static_native
from world import PARKED

# Only two nearby vehicles keep their full detail in memory at a time. The
# small silhouettes remain resident so distant cars do not disappear at a
# detail transition. Physics keeps every parked vehicle regardless of view.
MAX_FLEET_TRIANGLES = 4000
MAX_FLEET_VERTICES = 10000
low_models = {}
distant_models = {}
high_models = {}
detailed_ids = ()
submitted_vertices = 0
visible_ids = ()
submitted_triangles = 0
quality = "full"
LOW_FLEET_VERTICES = 0
_parked_render = []
for item in PARKED:
    model = build_model(item["id"], detail="low")
    low_models[item["id"]] = (static_native(model), model["triangles"])
    LOW_FLEET_VERTICES += model["vertices"]
    low_vertices = model["vertices"]
    distant = build_distant_model(item["id"])
    distant_models[item["id"]] = (static_native(distant), distant["triangles"],
                                  distant["vertices"], low_vertices)
    del distant
    # Parked poses never change. Retain their native matrices instead of
    # allocating and rebuilding all ten on every displayed frame.
    transform = (pico3d.mat4().translate(item["x"], 0., item["z"])
                 .rotate_y_radians(item["yaw"]))
    _parked_render.append((item["id"], item["x"], item["z"], transform))
    del model
_parked_render = tuple(_parked_render)
# The selection pass overwrites every distance before the submission pass uses
# it. Retain the ten slots instead of allocating/growing a list every frame.
_planar_distances = [0.] * len(_parked_render)
# Both badge tiers and immutable poses can be resolved once. Desktop may still
# build the separate inspection meshes dynamically.
_badge_render = tuple((entry[0], entry[1], entry[2], entry[3],
                       low_models[entry[0]], distant_models[entry[0]])
                      for entry in _parked_render)
gc.collect()
LOW_FLEET_TRIANGLES = sum(value[1] for value in low_models.values())
# All ten silhouettes plus at most two close meshes. Tight declared capacities
# matter on the badge: exceeding the native SRAM arena sends the entire scene's
# vertex/triangle caches to slower PSRAM, even if most meshes are off screen.
DISTANT_FLEET_TRIANGLES = sum(value[1] for value in distant_models.values())
DISTANT_FLEET_VERTICES = sum(value[2] for value in distant_models.values())
BADGE_FLEET_TRIANGLES = DISTANT_FLEET_TRIANGLES + sum(sorted(
    low_models[slug][1]-value[1] for slug,value in distant_models.items())[-2:])
BADGE_FLEET_VERTICES = DISTANT_FLEET_VERTICES + sum(sorted(
    value[3]-value[2] for value in distant_models.values())[-2:])


def set_quality(value):
    """Choose a portable render budget; every parked collision stays active."""
    global quality
    if value not in ("full", "badge"):
        raise ValueError("Unknown fleet render quality")
    quality = value
    if value == "badge":
        high_models.clear()
        gc.collect()


def draw(sim, vp, add, inspect_target=None):
    global visible_ids, submitted_triangles, submitted_vertices, detailed_ids
    focus_x, focus_z = sim.x, sim.z
    if inspect_target:
        focus_x, focus_z = inspect_target["x"], inspect_target["z"]
    entries = _parked_render
    detailed_ids = ()
    seen = []
    triangles = vertices = 0
    if quality == "badge":
        # Fixed ten-entry scan, with no sorting, model builds or GC in motion.
        # Height contributes so a high flight does not retain close geometry
        # for cars that have become tiny on screen beneath the Roadster.
        # Each planar distance is kept for the visibility cull below rather
        # than being recomputed for every entry.
        vertical = 0. if inspect_target else max(0., sim.y - 2.) ** 2
        first = second = None
        # Beyond 22 metres from the pad every car is already outside the detail
        # sphere. Submit the same silhouettes in one pass, without a nearest-
        # pair scan or a temporary distance buffer. Culling remains planar so
        # the collection is still visible beneath high flight.
        if vertical >= 484.:
            for slug,x,z,transform,low,distant in _badge_render:
                dx = x-focus_x
                dz = z-focus_z
                if dx*dx + dz*dz > 21025:
                    continue
                mesh,count,distant_vertices,low_vertices = distant
                vertices += distant_vertices
                triangles += count
                add(mesh, transform, vp)
                seen.append(slug)
        else:
            first_d = second_d = 484.
            planar = _planar_distances
            for i in range(len(entries)):
                slug,x,z,transform = entries[i]
                dx = x-focus_x
                dz = z-focus_z
                flat = dx*dx + dz*dz
                planar[i] = flat
                distance = flat + vertical
                if distance < first_d:
                    second,second_d = first,first_d
                    first,first_d = slug,distance
                elif distance < second_d:
                    second,second_d = slug,distance
            for i in range(len(entries)):
                if planar[i] > 21025:
                    continue
                slug,x,z,transform,low,distant = _badge_render[i]
                if slug == first or slug == second:
                    mesh, count = high_models.get(slug, low)
                    vertices += distant[3]
                else:
                    mesh,count,distant_vertices,low_vertices = distant
                    vertices += distant_vertices
                triangles += count
                add(mesh, transform, vp)
                seen.append(slug)
        detailed_ids = (first,second)
    else:
        # Only desktop detail selection needs a nearest-first sort. Badge
        # keeps every silhouette resident and can submit the fixed order.
        nearby = sorted(((entry[1]-focus_x)**2 + (entry[2]-focus_z)**2, entry)
                        for entry in entries)
        detailed = tuple(row[1][0] for row in nearby[:2] if row[0] < 24**2)
        for slug in tuple(high_models):
            if slug not in detailed:
                del high_models[slug]
        missing = tuple(slug for slug in detailed if slug not in high_models)
        if missing:
            gc.collect()
            for slug in missing:
                model = build_model(slug)
                high_models[slug] = (static_native(model), model["triangles"])
                del model
            gc.collect()
        for distance, entry in nearby:
            if distance > 145**2:
                continue
            slug, x, z, transform = entry
            mesh, count = high_models.get(slug, low_models[slug])
            # Desktop high-detail vertex counts are not used for badge capacity.
            vertices += distant_models[slug][3]
            triangles += count
            add(mesh, transform, vp)
            seen.append(slug)
    submitted_triangles = triangles
    submitted_vertices = vertices
    visible_ids = tuple(seen)
