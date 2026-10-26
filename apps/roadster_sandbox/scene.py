"""Native pico3d view of the free Roadster body and analytic track surfaces.

The meshes are pictures of the surfaces in physics.py. They do not constrain
the car, and the camera never changes the body's orientation or motion.
"""

from array import array
import gc
import math
import pico3d
from badgeware import *
from roadster_model import build_model
from model_assets import native_parts
from vehicle_spec import FAN_WIDTH, FAN_LENGTH, FAN_PLANE_Y
from physics import (LOOP_RADIUS, LOOP_Z, LOOP_HALF_WIDTH, NOZZLES,
                     GROUND_Y, WHEELS, WHEEL_REACH, WHEEL_RADIUS)
from world import (PARKED, find_vehicle, ROW_START, ROW_END, RUNUP_X,
                   PAD_LEFT, PAD_RIGHT, PAD_HALF_DEPTH)
import fleet_scene
from scenery import Background
import scene_palette as palette


def rgb(r, g, b):
    return r | (g << 8) | (b << 16)


def faces_mesh(faces):
    positions, indices, colours = array("f"), array("H"), array("I")
    for points, colour in faces:
        base = len(positions) // 3
        for point in points:
            positions.extend(point)
            colours.append(colour)
        indices.extend((base, base + 1, base + 2))
        if len(points) == 4:
            indices.extend((base, base + 2, base + 3))
    return pico3d.mesh(positions=positions, indices=indices, colors=colours,
                       quantise=True)


def floor_rect(x1, z1, x2, z2, y, colour):
    return (((x1, y, z1), (x1, y, z2), (x2, y, z2), (x2, y, z1)), colour)


# This finite visible patch follows the body in two-tile increments. The
# physics ground is infinite; there is no boundary or reset outside this mesh.
def ground_mesh(tiles=10, size=8):
    faces = []
    for row in range(tiles):
        for column in range(tiles):
            x, z = -tiles * size / 2 + column * size, -tiles * size / 2 + row * size
            shade = rgb(*palette.FIELD) if (row + column) % 2 else rgb(*palette.EARTH)
            faces.append(floor_rect(x, z, x + size, z + size, 0, shade))
    return faces_mesh(faces), 2 * len(faces)


ground, GROUND_TRIANGLES = ground_mesh()

def surface_grid_mesh():
    """Reusable eight-metre surface seams, including featureless paved areas.

    The thin grid is a depth-tested decal. Its clip-space bias avoids coplanar
    shimmer without lifting the paint above the tyres or painting over cars.
    Major lines repeat every 32 metres, matching the recycling translation.
    """
    faces = []
    shade = rgb(*palette.PAINT)
    for i in range(-8, 9):
        p = i * 8.
        half = .10 if i % 4 == 0 else .05
        faces.append(floor_rect(p-half, -64., p+half, 64., 0., shade))
        faces.append(floor_rect(-64., p-half, 64., p+half, 0., shade))
    return faces_mesh(faces), len(faces)*2


surface_grid, SURFACE_GRID_TRIANGLES = surface_grid_mesh()


# The airport floor is a tessellation: asphalt, grass and paint meet at
# shared edges instead of stacking almost-coplanar meshes. The native 16-bit
# depth buffer cannot reliably separate millimetre-high paint at a distance.
def _cut_floor(rectangles, cut):
    result = []
    cx1,cz1,cx2,cz2 = cut
    for x1,z1,x2,z2,shade in rectangles:
        ix1,iz1,ix2,iz2 = max(x1,cx1),max(z1,cz1),min(x2,cx2),min(z2,cz2)
        if ix1 >= ix2 or iz1 >= iz2:
            result.append((x1,z1,x2,z2,shade))
            continue
        for a,b,c,d in ((x1,z1,x2,iz1),(x1,iz2,x2,z2),
                        (x1,iz1,ix1,iz2),(ix2,iz1,x2,iz2)):
            if a < c and b < d:
                result.append((a,b,c,d,shade))
    return result


def airfield_mesh(track=False):
    asphalt = rgb(*palette.ASPHALT)
    edge = center = threshold = rgb(*palette.PAINT)
    earth,field,shoulder = rgb(*palette.EARTH),rgb(*palette.FIELD),rgb(*palette.SHOULDER)
    rectangles = [(-1000.,-1000.,1000.,-180.,earth),
                  (-1000.,180.,1000.,1000.,field),
                  (-1000.,-180.,-14.,180.,field),
                  (14.,-180.,1000.,180.,earth),
                  (-14.,-180.,-7.5,180.,shoulder),
                  (7.5,-180.,14.,180.,shoulder)]
    # Taxiway branches replace grass; their paint has no collision side effects.
    for branch in ((-48.,-44.,-7.5,-38.),(7.5,54.,48.,60.)):
        rectangles = _cut_floor(rectangles,branch)
        rectangles.append(branch+(asphalt,))
    for x1,x2,shade in ((-7.5,-7.2,asphalt),(-7.2,-6.88,edge),
                        (6.88,7.2,edge),(7.2,7.5,asphalt)):
        rectangles.append((x1,-180.,x2,180.,shade))
    # Split the broad asphalt only where six threshold bars need their own
    # faces. The remaining side lanes stay broad quads, not a dense tile grid.
    for x1,x2,bars in ((-6.88,-.18,(-5.8,-4.3,-2.8)),
                        (.18,6.88,(2.3,3.8,5.3))):
        rectangles.append((x1,-180.,x2,-20.,asphalt))
        rectangles.append((x1,-14.,x2,180.,asphalt))
        previous=x1
        for x in bars:
            rectangles.append((previous,-20.,x,-14.,asphalt))
            rectangles.append((x,-20.,x+.5,-14.,threshold))
            previous=x+.5
        rectangles.append((previous,-20.,x2,-14.,asphalt))
    previous=-180.
    for z in range(-96,97,12):
        if 4 < z < 44:
            continue
        rectangles.append((-.18,previous,.18,float(z),asphalt))
        rectangles.append((-.18,float(z),.18,z+4.,center))
        previous=z+4.
    rectangles.append((-.18,previous,.18,180.,asphalt))
    if track:
        # The curved track reaches the physics ground at its base. Leave that
        # narrow footprint clear so asphalt never competes with the ribbon's
        # first faces; the approach and both outside lanes stay continuous.
        rectangles=_cut_floor(rectangles,(-LOOP_HALF_WIDTH,LOOP_Z-1.,
                                         LOOP_HALF_WIDTH,LOOP_Z+1.))
    faces=[floor_rect(x1,z1,x2,z2,0.,shade) for x1,z1,x2,z2,shade in rectangles]
    return faces_mesh(faces),len(faces)*2


runway,RUNWAY_TRIANGLES=airfield_mesh()
track_runway,TRACK_RUNWAY_TRIANGLES=airfield_mesh(True)

def track_mesh(segments, half_segments=24):
    """48 segments close the loop; 24 leave an open half-circle ramp."""
    faces = []
    width, radius = LOOP_HALF_WIDTH, LOOP_RADIUS
    for i in range(segments):
        a, b = i * math.pi / half_segments, (i + 1) * math.pi / half_segments
        ya, za = radius * (1 - math.cos(a)), LOOP_Z + radius * math.sin(a)
        yb, zb = radius * (1 - math.cos(b)), LOOP_Z + radius * math.sin(b)
        yoa = radius - (radius + .16) * math.cos(a)
        zob = LOOP_Z + (radius + .16) * math.sin(b)
        yob = radius - (radius + .16) * math.cos(b)
        zoa = LOOP_Z + (radius + .16) * math.sin(a)
        shade = rgb(83, 112, 130) if i % 2 else rgb(65, 92, 113)
        for x1, x2, colour in ((-width, -width + .28, rgb(52, 217, 209)),
                               (-width + .28, width - .28, shade),
                               (width - .28, width, rgb(52, 217, 209))):
            faces.append((((x1, ya, za), (x1, yb, zb),
                           (x2, yb, zb), (x2, ya, za)), colour))
        faces.append((((-width, yoa, zoa), (width, yoa, zoa),
                       (width, yob, zob), (-width, yob, zob)), rgb(35, 64, 82)))
        for x in (-width, width):
            faces.append((((x, ya, za), (x, yoa, zoa),
                           (x, yob, zob), (x, yb, zb)), rgb(50, 155, 164)))
    if segments == half_segments:
        # The exposed top edge makes the launch lip readable from either side.
        faces.append((((-width, radius * 2, LOOP_Z),
                       (width, radius * 2, LOOP_Z),
                       (width, radius * 2 + .16, LOOP_Z),
                       (-width, radius * 2 + .16, LOOP_Z)), rgb(80, 230, 218)))
    return faces_mesh(faces), len(faces) * 2


loop, LOOP_TRIANGLES = track_mesh(48)
ramp, RAMP_TRIANGLES = track_mesh(24)


def plume_mesh(position, direction):
    """The exhaust points opposite to the actual body-local thrust vector."""
    x, y, z = position
    dx, dy, dz = direction
    # Each nozzle direction is axial, so the diamond's perpendicular basis is
    # exact and needs no per-frame cross products or allocation-heavy geometry.
    if abs(dy) > .5:
        a, b = (.12, 0, 0), (0, 0, .12)
    elif abs(dx) > .5:
        a, b = (0, .12, 0), (0, 0, .12)
    else:
        a, b = (.12, 0, 0), (0, .12, 0)
    x, y, z = x - dx * .15, y - dy * .15, z - dz * .15
    ring = ((x + a[0], y + a[1], z + a[2]),
            (x + b[0], y + b[1], z + b[2]),
            (x - a[0], y - a[1], z - a[2]),
            (x - b[0], y - b[1], z - b[2]))
    tip = (x - dx * 1.15, y - dy * 1.15, z - dz * 1.15)
    faces = []
    for i in range(4):
        faces.append(((ring[i], ring[(i + 1) % 4], tip),
                      rgb(143, 229, 250) if i % 2 else rgb(239, 249, 252)))
    return faces_mesh(faces)


plumes = tuple(plume_mesh(nozzle[1], nozzle[2]) for nozzle in NOZZLES)
PLUME_TRIANGLES = 4 * len(plumes)
PLUME_COUNT = len(plumes)

# A thin cyan underfloor outline indicates an electrically running fan. The
# HUD reports its actual suction, including loss of seal while airborne.
_FAN_X, _FAN_Z = FAN_WIDTH*.5, FAN_LENGTH*.5
_FAN_Y, _FAN_EDGE = FAN_PLANE_Y-.008, .055
fan = faces_mesh((
    floor_rect(-_FAN_X, -_FAN_Z, -_FAN_X+_FAN_EDGE, _FAN_Z, _FAN_Y, rgb(40, 226, 184)),
    floor_rect(_FAN_X-_FAN_EDGE, -_FAN_Z, _FAN_X, _FAN_Z, _FAN_Y, rgb(40, 226, 184)),
    floor_rect(-_FAN_X+_FAN_EDGE, -_FAN_Z, _FAN_X-_FAN_EDGE, -_FAN_Z+_FAN_EDGE,
               _FAN_Y, rgb(40, 226, 184)),
    floor_rect(-_FAN_X+_FAN_EDGE, _FAN_Z-_FAN_EDGE, _FAN_X-_FAN_EDGE, _FAN_Z,
               _FAN_Y, rgb(40, 226, 184)),
))
FAN_TRIANGLES = 8

model = build_model()
TRIANGLE_COUNT, VERTEX_COUNT = model["triangles"], model["vertices"]
car = native_parts(model, origin_y=-GROUND_Y)
del model
_WHEEL_INDEX = {"wheel_FL": 0, "wheel_FR": 1, "wheel_RL": 2, "wheel_RR": 3}


# Per-frame wheel placement needs only the pivot, the resting hub height and
# the steering flag. Resolve those once instead of matching part names, indexing
# the physics tables and re-adding the constant hub offset on every frame.
def _car_parts():
    parts = []
    for name, mesh, pivot in car:
        if name.startswith("wheel_"):
            index = _WHEEL_INDEX[name]
            rest_y = WHEELS[index][1] - WHEEL_REACH + WHEEL_RADIUS
            parts.append((mesh, (index, pivot[0], rest_y, pivot[2],
                                 name in ("wheel_FL", "wheel_FR"))))
        else:
            parts.append((mesh, None))
    return tuple(parts)


_CAR_PARTS = _car_parts()

def lineup_mesh():
    """Open apron with an inset rim and readable, depth-stable run-up paint."""
    earth,field = rgb(*palette.EARTH),rgb(*palette.FIELD)
    rectangles = [(PAD_LEFT,-PAD_HALF_DEPTH,PAD_RIGHT,PAD_HALF_DEPTH,rgb(*palette.ASPHALT)),
        (-1000.,-1000.,1000.,-PAD_HALF_DEPTH,earth),
        (-1000.,PAD_HALF_DEPTH,1000.,1000.,field),
        (-1000.,-PAD_HALF_DEPTH,PAD_LEFT,PAD_HALF_DEPTH,field),
        (PAD_RIGHT,-PAD_HALF_DEPTH,1000.,PAD_HALF_DEPTH,earth)]
    # Paint replaces pavement instead of floating above it. All faces meet
    # at exact shared edges, avoiding distant 16-bit depth-buffer flicker.
    rim = .32
    marks = [(PAD_LEFT,-PAD_HALF_DEPTH,PAD_RIGHT,-PAD_HALF_DEPTH+rim),
        (PAD_LEFT,PAD_HALF_DEPTH-rim,PAD_RIGHT,PAD_HALF_DEPTH),
        (PAD_LEFT,-PAD_HALF_DEPTH+rim,PAD_LEFT+rim,PAD_HALF_DEPTH-rim),
        (PAD_RIGHT-rim,-PAD_HALF_DEPTH+rim,PAD_RIGHT,PAD_HALF_DEPTH-rim)]
    for bounds in marks:
        rectangles = _cut_floor(rectangles,bounds)
        rectangles.append(bounds+(rgb(*palette.PAINT),))
    # Same takeoff position and freely standing cars; wider paint is visual only.
    for i in range(4):
        x = RUNUP_X-3+i*3
        bounds = (x,-.2,x+1.4,.2)
        rectangles = _cut_floor(rectangles,bounds)
        rectangles.append(bounds+(rgb(*palette.GUIDE),))
    bounds = (ROW_START-11.25,-4.,ROW_START-10.75,4.)
    rectangles = _cut_floor(rectangles,bounds)
    rectangles.append(bounds+(rgb(*palette.GUIDE),))
    faces = [floor_rect(x1,z1,x2,z2,0.,shade) for x1,z1,x2,z2,shade in rectangles]
    return faces_mesh(faces),len(faces)*2


lot,LOT_TRIANGLES = lineup_mesh()
MAX_SCENE_TRIANGLES = (TRIANGLE_COUNT + GROUND_TRIANGLES + LOOP_TRIANGLES +
                       PLUME_TRIANGLES + FAN_TRIANGLES + LOT_TRIANGLES +
                       SURFACE_GRID_TRIANGLES + TRACK_RUNWAY_TRIANGLES + fleet_scene.MAX_FLEET_TRIANGLES)
material = pico3d.material(color=color.rgb(255, 255, 255), shading=pico3d.material.UNLIT)
double_material = pico3d.material(color=color.rgb(255, 255, 255),
                                  shading=pico3d.material.UNLIT, double_sided=True)
renderer = pico3d.surface(screen)
# Select quality before the first frame to avoid allocating the full desktop
# scene on a small device. Both qualities use the same detailed Roadster mesh.
geometry = None
_geometry_quality = None
_geometry_collection = None
_geometry_scene = None
quality = "full"
scene_triangle_capacity = MAX_SCENE_TRIANGLES + 100
scene_vertex_capacity = VERTEX_COUNT + 3300 + fleet_scene.MAX_FLEET_VERTICES
_initial_render_cores = pico3d.engine.core_count()
render_cores = _initial_render_cores
projection = pico3d.mat4.perspective(50, 320 / 240, .35, 135)
identity = pico3d.mat4()
_UP = pico3d.vec3(0, 1, 0)
# The widened boost lens is a pure function of its field of view. Rebuild it
# only when that value changes, not on every boosting frame.
_lens = projection
_lens_fov = 50.
camera_yaw = 0.0
camera_time = -1.0
triangle_count = 0
collection_enabled = True
inspect_target = None
camera_view = "chase"
follow_view = "chase"
camera_fov = 50.
camera_distance = 11.
camera_side_offset = 4.8
ground_look_yaw = 0.
ground_look_height = 0.
visual_intensity = 0.
speed_streak_count = 0
streak_phase = 0.
background = Background()
scene_arena_estimate = 0
_ground_x = None
_ground_z = None
_ground_transform = None
_surface_x = _surface_z = None
_surface_transform = None
# Three 16-bit depth units towards the eye, applied in clip space. The decal
# shares the physical ground plane; this changes depth testing only, not X/Y.
_surface_bias = pico3d.mat4().translate(0., 0., -6./65535.)
_focused_slug = None
_focused = None
# Fixed world-selection bounds; the per-frame tests compare against these
# rather than re-deriving them from the pad constants.
_NEAR_LEFT = PAD_LEFT - 135
_NEAR_RIGHT = PAD_RIGHT + 135
_NEAR_DEPTH = PAD_HALF_DEPTH + 135
_INSPECT_ANGLES = {"front": 0., "side": math.pi/2, "rear": math.pi}
# Per-streak constants: the phase offset and the signed distance of each edge
# row from the vanishing row, resolved once instead of on every boosting frame.
_STREAK_ROWS = tuple((i * .173, edge_y - 124.)
                     for i, edge_y in enumerate((48., 78., 108., 145., 174., 204.)))


def set_quality(value):
    """Explicit portable render budget; no vehicle or collision simplification.

    Badge keeps the 2,084-triangle Roadster, all ten parked silhouettes and
    up to two closer authored parked meshes. The horizon costs no scene cache.
    There are no synchronous model rebuilds during driving. Physics, distances
    and the ten parked collision bodies remain unchanged.
    """
    global quality, geometry, ground, loop, ramp, _geometry_quality
    global GROUND_TRIANGLES, LOOP_TRIANGLES, RAMP_TRIANGLES
    global scene_triangle_capacity, scene_vertex_capacity, render_cores
    if value not in ("full", "badge"):
        raise ValueError("Unknown Roadster render quality")
    if value == quality:
        return quality
    geometry = None
    _geometry_quality = None
    ground = loop = ramp = None
    gc.collect()
    quality = value
    fleet_scene.set_quality(value)
    if value == "badge":
        ground, GROUND_TRIANGLES = ground_mesh(4, 32)
        loop, LOOP_TRIANGLES = track_mesh(24, 12)
        ramp, RAMP_TRIANGLES = track_mesh(12, 12)
        scene_vertex_capacity = VERTEX_COUNT + 1200 + fleet_scene.BADGE_FLEET_VERTICES
        fleet_triangles = fleet_scene.BADGE_FLEET_TRIANGLES
        render_cores = pico3d.engine.cores(2)
    else:
        ground, GROUND_TRIANGLES = ground_mesh()
        loop, LOOP_TRIANGLES = track_mesh(48)
        ramp, RAMP_TRIANGLES = track_mesh(24)
        scene_vertex_capacity = VERTEX_COUNT + 3300 + fleet_scene.MAX_FLEET_VERTICES
        fleet_triangles = fleet_scene.MAX_FLEET_TRIANGLES
        render_cores = pico3d.engine.cores(_initial_render_cores)
    scene_triangle_capacity = (TRIANGLE_COUNT + GROUND_TRIANGLES +
        SURFACE_GRID_TRIANGLES + LOOP_TRIANGLES + PLUME_TRIANGLES +
        FAN_TRIANGLES + LOT_TRIANGLES + TRACK_RUNWAY_TRIANGLES + fleet_triangles + 100)
    gc.collect()
    return quality


def restore_renderer():
    """Return the shared native renderer to its entry core setting on exit."""
    global render_cores
    render_cores = pico3d.engine.cores(_initial_render_cores)


def prepare_geometry(sim):
    """Fit each badge scene's declared cache to its actual submitted mesh budget."""
    global geometry, _geometry_quality, _geometry_collection, _geometry_scene
    global scene_triangle_capacity, scene_vertex_capacity, scene_arena_estimate
    if geometry is not None and _geometry_quality == quality and (
            quality != "badge" or (_geometry_collection == collection_enabled and
                                   _geometry_scene == sim.scene_name)):
        return
    geometry = None
    gc.collect()
    if quality == "badge":
        # The collection apron replaces the tiled ground and runway. These
        # mutually exclusive meshes must not inflate the native cache arena.
        if collection_enabled:
            world_triangles = max(LOT_TRIANGLES, GROUND_TRIANGLES)
        else:
            floor_triangles = TRACK_RUNWAY_TRIANGLES if sim.scene_name in ('loop','ramp') else RUNWAY_TRIANGLES
            world_triangles = max(floor_triangles,GROUND_TRIANGLES)
        world_triangles += SURFACE_GRID_TRIANGLES
        if sim.scene_name == "loop":
            world_triangles += LOOP_TRIANGLES
        elif sim.scene_name == "ramp":
            world_triangles += RAMP_TRIANGLES
        fleet_triangles = fleet_vertices = 0
        if collection_enabled:
            fleet_triangles = fleet_scene.BADGE_FLEET_TRIANGLES
            fleet_vertices = fleet_scene.BADGE_FLEET_VERTICES
        scene_triangle_capacity = (TRIANGLE_COUNT + world_triangles +
            PLUME_TRIANGLES + FAN_TRIANGLES + fleet_triangles + 16)
        # World faces are quads (4 vertices / 2 triangles), plume triangles
        # deliberately do not share vertices, and the car/fleet are indexed.
        scene_vertex_capacity = (VERTEX_COUNT + 2 * world_triangles +
            3 * PLUME_TRIANGLES + 2 * FAN_TRIANGLES + fleet_vertices + 16)
    geometry = pico3d.scene(renderer, meshes=len(car) + len(plumes) + 16,
                            vertices=scene_vertex_capacity,
                            triangles=scene_triangle_capacity)
    # Public native-source sizing estimate, not proof of this badge's arena
    # size or allocation location. UNLIT uses a 16-byte vertex cache entry.
    scene_arena_estimate = (16*scene_vertex_capacity + 14*scene_triangle_capacity
                            + 8*(len(car)+len(plumes)+16))
    _geometry_quality = quality
    _geometry_collection = collection_enabled
    _geometry_scene = sim.scene_name


def _capacity_exceeded():
    raise RuntimeError("Roadster sandbox scene capacity exceeded")


def add(mesh, transform, view_projection, mat=material):
    if not geometry.add(mesh, transform, view_projection, mat):
        _capacity_exceeded()


def body_transform(sim, basis=None):
    """Exact Y-X-Z decomposition for pico3d's exposed rotation-only builders.

    The physical orientation is always a quaternion. These Euler angles are
    only a way to construct its matrix using the native public Python API.
    """
    left, up, forward = sim.basis() if basis is None else basis
    pitch = math.asin(max(-1.0, min(1.0, -forward[1])))
    if abs(forward[1]) < .99999999999:
        yaw = math.atan2(forward[0], forward[2])
        roll = math.atan2(left[1], up[1])
    else:
        yaw = math.atan2(-left[2], left[0])
        roll = 0.0
    return (pico3d.mat4().translate(sim.x, sim.y, sim.z)
            .rotate_y_radians(yaw).rotate_x_radians(pitch)
            .rotate_z_radians(roll))


def render_car(view_projection, transform, sim):
    # The native add is called directly in this per-part loop; a failed add
    # still raises the same capacity error as add().
    geometry_add = geometry.add
    mat4 = pico3d.mat4
    compression = sim.wheel_compression
    for mesh, wheel in _CAR_PARTS:
        if wheel is None:
            if not geometry_add(mesh, transform, view_projection, material):
                _capacity_exceeded()
            continue
        index, pivot_x, rest_y, pivot_z, steered = wheel
        local = mat4().translate(pivot_x, rest_y + compression[index], pivot_z)
        if steered:
            local.rotate_y_radians(sim.steering)
        local.rotate_x_radians(sim.wheel_spin)
        if not geometry_add(mesh, transform * local, view_projection, material):
            _capacity_exceeded()
    forces = sim.nozzle_forces
    for i in range(PLUME_COUNT):
        if forces[i] > 1:
            if not geometry_add(plumes[i], transform, view_projection, double_material):
                _capacity_exceeded()
    if sim.fan_speed > .1:
        if not geometry_add(fan, transform, view_projection, double_material):
            _capacity_exceeded()


def draw_speed_streaks(intensity):
    """Thin peripheral lines leave the car, obstacles and both HUD bars clear."""
    global speed_streak_count
    speed_streak_count = 0
    if intensity < .16:
        return
    screen.pen = color.rgb(int(61+77*intensity), int(111+81*intensity),
                           int(137+82*intensity))
    length = 4. + 15. * intensity
    line = screen.line
    for offset, edge in _STREAK_ROWS:
        phase = (streak_phase + offset) % 1.
        x = 4. + 48. * (1. - phase)
        y = 124. + edge * (.85 + .15 * phase)
        inner_y = 124. + (y - 124.) * (1. - length / (160. - x))
        # screen.line is native PicoVector; no browser overlay or texture.
        line(x, y, x + length, inner_y)
        line(320. - x, y, 320. - x - length, inner_y)
    speed_streak_count = 2 * len(_STREAK_ROWS)


def draw(sim, feedback=None, reduced_effects=False, viewport=None):
    """Draw the world, optionally excluding regions covered by an opaque HUD.

    `viewport` is a native rect in the original 320x240 screen coordinates;
    it clips raster work without changing the lens or camera composition.
    The caller's previous clip is restored before returning, even on failure.
    """
    global camera_yaw, camera_time, triangle_count, streak_phase
    global camera_fov, camera_distance, camera_side_offset, visual_intensity
    global _ground_x, _ground_z, _ground_transform
    global _surface_x, _surface_z, _surface_transform
    global _lens, _lens_fov, _focused_slug, _focused
    prepare_geometry(sim)
    basis = sim.basis()
    left, up, forward = basis
    sim_x, sim_y, sim_z = sim.x, sim.y, sim.z
    scene_name = sim.scene_name
    elapsed = max(0.0, min(.1, sim.time - camera_time))
    if sim.time < camera_time or camera_time < 0:
        camera_yaw = math.atan2(forward[0], forward[2])
        streak_phase = 0.
    # Keep the horizon steady through flips and vertical travel. Resume
    # following the body's heading when its wheels point roughly downward.
    if up[1] > .3 and forward[0] ** 2 + forward[2] ** 2 > .15:
        heading = math.atan2(forward[0], forward[2])
        difference = (heading - camera_yaw + math.pi) % (2 * math.pi) - math.pi
        camera_yaw += difference * min(1.0, elapsed * 3)
    camera_time = sim.time
    s, c = math.sin(camera_yaw), math.cos(camera_yaw)
    # Pull sideways beside the loop so its ribbon does not hide the inverted
    # car. Away from the track this becomes a conventional offset chase view.
    track_view = 0.0
    if scene_name == "loop" and camera_view == "chase":
        track_view = max(0.0, min(1.0, (27 - abs(sim_z - LOOP_Z)) / 18))
        track_view *= max(0.0, min(1.0, (14 - abs(sim_x)) / 7))
    follow = 1 - track_view
    # Directly-behind remains on axis through acceleration. The alternative
    # chase view retains its normal offset and the loop's side transition.
    inspecting = bool(inspect_target) or camera_view not in ("chase", "behind")
    visual_intensity = (max(0., min(1., feedback.intensity))
                        if feedback is not None and not reduced_effects and not inspecting else 0.)
    # Keep the loop's deliberately close side framing readable at speed.
    camera_effect = visual_intensity * follow
    camera_fov = 50. + 12. * camera_effect
    camera_distance = 11. + 2.2 * camera_effect
    # Airfield and Tesla Lineup share one follow framing. Collection geometry
    # changes what is in the scene, not how the drivable car is followed.
    camera_side_offset = 0. if camera_view == "behind" else 4.8
    # Grounded tilt orbits the eye, never the car or its heading. The app eases
    # these offsets to zero on takeoff; live air tilt continues to own the jets.
    look_s, look_c = (math.sin(camera_yaw+ground_look_yaw),
                      math.cos(camera_yaw+ground_look_yaw)) if ground_look_yaw else (s,c)
    chase_x = -look_s * camera_distance + look_c * camera_side_offset
    chase_z = -look_c * camera_distance - look_s * camera_side_offset
    # View nearly across the ribbon near the loop. A high rear camera looks
    # through the opaque track at the top and hides the inverted car.
    # As the car rises, lower the chase pitch enough to retain a visible
    # horizon. The camera stays world-upright through rolls and backflips.
    air_mix = max(0.,min(1.,(sim_y-4.)/18.))
    ground_height = 3.6 if scene_name == 'ramp' else 5.6
    chase_height = max(1.5, ground_height + (1.5-ground_height)*air_mix + ground_look_height)
    eye = pico3d.vec3(sim_x + chase_x * follow + 18 * track_view,
                      sim_y + chase_height * follow + 2 * track_view,
                      sim_z + chase_z * follow - 2 * track_view)
    look_ahead = 6.-5.*air_mix if scene_name == "ramp" else 1.
    target = pico3d.vec3(sim_x + s * look_ahead * follow, sim_y + .15,
                         sim_z + c * look_ahead * follow)
    if inspect_target:
        # The parked record is immutable; look it up again only when the
        # inspected vehicle changes.
        if inspect_target != _focused_slug:
            _focused = find_vehicle(inspect_target)
            _focused_slug = inspect_target
        focused = _focused
    else:
        focused = None
    if inspecting:
        if focused:
            cx, cy, cz = focused["x"], focused["height"] * .48, focused["z"]
            distance = max(5.4, focused["length"] * 1.32)
        else:
            cx, cy, cz = sim_x, sim_y + .08, sim_z
            distance = 6.5
        if camera_view in ("front", "rear"):
            length = focused["length"] if focused else 4.75
            width = focused["width"] if focused else 2.09
            height = focused["height"] if focused else 1.162
            distance = length / 2 + max(width * 1.05, height * 1.7)
        angle = _INSPECT_ANGLES.get(camera_view, .7 + sim.time * .2)
        elevation = .06 if camera_view in ("front", "side", "rear") else .40
        eye = pico3d.vec3(cx + math.sin(angle)*distance, cy + distance*elevation,
                          cz + math.cos(angle)*distance)
        target = pico3d.vec3(cx, cy, cz)
    if camera_effect > .0001:
        if camera_fov != _lens_fov:
            _lens = pico3d.mat4.perspective(camera_fov, 320 / 240, .35, 135)
            _lens_fov = camera_fov
        lens = _lens
    else:
        lens = projection
    vp = lens * pico3d.mat4.look_at(eye, target, _UP)
    background.camera(eye,target,camera_fov)
    geometry.reset()
    # Recycle by a complete two-tile color period, so the land never swaps
    # colors under the car when crossing a patch boundary. Round to the
    # nearest patch center so there is still ground ahead before recycling.
    ground_step = 64 if quality == "badge" else 16
    ground_x = math.floor((sim_x+ground_step*.5) / ground_step) * ground_step
    ground_z = math.floor((sim_z+ground_step*.5) / ground_step) * ground_step
    if ground_x != _ground_x or ground_z != _ground_z:
        _ground_transform = pico3d.mat4().translate(ground_x, 0, ground_z)
        _ground_x, _ground_z = ground_x, ground_z
    if focused:
        focus_x, focus_z = focused["x"], focused["z"]
    else:
        focus_x, focus_z = sim_x, sim_z
    near_collection = (collection_enabled and _NEAR_LEFT < focus_x < _NEAR_RIGHT
                       and abs(focus_z) < _NEAR_DEPTH)
    near_airfield = not collection_enabled and abs(focus_x) < 183. and abs(focus_z) < 315.
    geometry_add = geometry.add
    if not near_collection and not near_airfield:
        if not geometry_add(ground, _ground_transform, vp, material):
            _capacity_exceeded()
    if near_airfield:
        if not geometry_add(track_runway if scene_name in ('loop','ramp') else runway,
                            identity, vp, material):
            _capacity_exceeded()
    if collection_enabled:
        if near_collection:
            if not geometry_add(lot, identity, vp, material):
                _capacity_exceeded()
        fleet_scene.draw(sim, vp, add, focused)
    # Place the reusable decal over visible terrain, not just below the car.
    # Whole 32m shifts preserve every seam's world position and major-line phase.
    # Above 200m the plane is outside even the widest lens's 135m frustum.
    # Let the distant backdrop supply scale instead of allocating a matrix
    # and submitting a native mesh that cannot contribute a single pixel.
    if background.ey < 200.:
        distance_ahead = min(80., max(12., background.ey*2.5))
        horizontal = max(.0001, background.uy)
        surface_x = math.floor((background.ex+background.fx/horizontal*distance_ahead+16.)/32.)*32.
        surface_z = math.floor((background.ez+background.fz/horizontal*distance_ahead+16.)/32.)*32.
        if surface_x != _surface_x or surface_z != _surface_z:
            _surface_transform = pico3d.mat4().translate(surface_x, 0., surface_z)
            _surface_x, _surface_z = surface_x, surface_z
        if not geometry_add(surface_grid, _surface_transform, _surface_bias*vp, material):
            _capacity_exceeded()
    if scene_name == "loop":
        if not geometry_add(loop, identity, vp, double_material):
            _capacity_exceeded()
    elif scene_name == "ramp":
        if not geometry_add(ramp, identity, vp, double_material):
            _capacity_exceeded()
    render_car(vp, body_transform(sim, basis), sim)
    previous_clip = screen.clip
    try:
        if viewport is not None:
            screen.clip = viewport
        background.draw()
        triangle_count = renderer.draw(geometry)
        horizontal_speed = math.sqrt(sim.vx * sim.vx + sim.vz * sim.vz)
        streak_phase = (streak_phase + elapsed * horizontal_speed * .045) % 1.
        # Actual travel gates the lines even while the smoothed boost cue is
        # decaying after a stop. Firing against the brake cannot fake fast motion.
        flow = visual_intensity if horizontal_speed > 8. and feedback is not None and feedback.speed_mix > .01 else 0.
        draw_speed_streaks(flow)
    finally:
        screen.clip = previous_clip
