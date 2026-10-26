"""Continuous Roadster rigid-body sandbox, metres / kilograms / seconds.

Portable MicroPython: math only. No stages, path constraints, automatic flips,
velocity resets or completion states. Inputs produce forces; impacts produce
contact impulses. Parameters are exploratory estimates, not Tesla specifications.
Vehicle design, force targets and reservoir model live in vehicle_spec.
Quaternion order is (w,x,y,z); body axes are +X left, +Y up, +Z forward.

Hot paths spell out the small vector helpers (q_rotate, dot, cross, add, scale,
unit, _point_velocity, _force, _impulse, the inertia tensors) as straight-line
float expressions. Force integration keeps the helper operand order. Contact
resolution caches the equivalent world inertia tensor and per-contact impulse
response; this intentionally changes rounding while preserving the force laws.
Badge mode selects a 40 Hz integration budget, retaining 240 Hz refinement
for hard impacts. This preserves the force laws and geometry but intentionally
trades some numerical accuracy for responsiveness: sustained loop motion can
lose more energy to suspension/body contacts than at 60 or 120 Hz.
"""
import math
from vehicle_spec import (
    GRAVITY, GROUND_Y, WHEEL_RADIUS, WHEEL_REACH, SPRING, DAMPER,
    SPRING_TRAVEL, BUMP_SPRING, DRY_MASS, TANK_MASS, TANK_PRESSURE,
    AMBIENT_PRESSURE, TANK_RESIDUAL, EXHAUST_SPEED, REFERENCE_MASS,
    INERTIA, WHEELS, NOZZLES, SKIRT_PROBES, HULL, FAN_AREA, FAN_PLANE_Y,
    FAN_MAX_FORCE, FAN_SEAL_HEIGHT, FAN_CAPTURE_HEIGHT, FAN_RISE_TIME,
    FAN_FALL_TIME, DRIVE_FORCE, DRIVE_POWER, TYRE_FRICTION,
    ROLLING_RESISTANCE, TYRE_RELAXATION, AIR_DENSITY, DRAG_AREA,
    tank_pressure, supply_fraction, meter_nozzles,
)

PI = math.pi
TAU = PI * 2.0
FIXED_DT = 1.0 / 120.0
MAX_SUBSTEPS = 16
LOOP_RADIUS = 8.0
LOOP_Z = 10.0
LOOP_HALF_WIDTH = 3.5
TRACK_THICKNESS = 0.16
# Conservative millimetre margins keep these rejection tests away from the
# exact contact boundaries, including native float32 rounding at this radius.
_TRACK_SAFE_INNER2 = (LOOP_RADIUS-.001)**2
_TRACK_SAFE_OUTER2 = (LOOP_RADIUS+TRACK_THICKNESS-.001)**2
# The two ribbon walls a ray can enter: (radius, normal side), inner first.
_TRACK_WALLS = ((LOOP_RADIUS, -1.), (LOOP_RADIUS+TRACK_THICKNESS, 1.))
# Immutable design values are shared with the handmade model and controllers.
_I0, _I1, _I2 = INERTIA
_DRAG = -.5*AIR_DENSITY*DRAG_AREA
# Each fan sample represents an equal portion of the plenum. Its seal falls
# smoothly from full adhesion at the skirt reach to zero beyond capture.
_FAN_PRESSURE = FAN_MAX_FORCE/FAN_AREA
_FAN_SPAN = FAN_CAPTURE_HEIGHT-FAN_SEAL_HEIGHT
_FAN_SPIN_TIME = .28
# Broad-phase bounds come from the current vehicle, including probe endpoints.
# They must expand automatically when a different handmade body is configured.
_HULL_MIN = tuple(min(p[i] for p in HULL) for i in range(3))
_HULL_MAX = tuple(max(p[i] for p in HULL) for i in range(3))
_HULL_CENTER = tuple((_HULL_MIN[i]+_HULL_MAX[i])*.5 for i in range(3))
_HULL_HALF = tuple((_HULL_MAX[i]-_HULL_MIN[i])*.5 for i in range(3))
_BODY_REACH = .01+max(
    max(math.sqrt(sum(v*v for v in p)) for p in HULL),
    max(math.sqrt(sum(v*v for v in p))+WHEEL_REACH for p in WHEELS),
    max(math.sqrt(sum(v*v for v in p))+FAN_CAPTURE_HEIGHT for p in SKIRT_PROBES))
# A padded neighborhood covers both probe rays and this tick's chassis sweep.
_NEAR_MOVE = .6
_NEAR_REACH = _BODY_REACH+_NEAR_MOVE+.1
_NEAR_CACHE_MOVE = 2.
_NEAR_CACHE_REACH = _NEAR_REACH+_NEAR_CACHE_MOVE+.05
# Per-call wheel contact scratch shared by every simulation: contact point
# XYZ and normal XYZ per wheel, plus the surface label. _wheels fills the
# entries it reads within the same call, so no per-tick tuples are allocated.
_WHEEL_SCRATCH = [0.] * 24
_WHEEL_SURFACES = [None] * 4


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def number(x, fallback=0.):
    # MicroPython bool is not an int subclass; browser toggles supply booleans.
    if not isinstance(x, (bool, int, float)) or x != x or abs(x) > 1.e12:
        return fallback
    return float(x)


def _bounded_control(value, lo, hi):
    # Same number()+clamp() contract with one call per input. Controllers run
    # each fixed tick, so repeating nested conversions/min/max for all ten
    # nozzles becomes significant while multiple rotation jets are active.
    if not isinstance(value, (bool, int, float)) or value != value or abs(value) > 1.e12:
        return 0.
    if value <= lo:
        return lo
    if value >= hi:
        return hi
    return float(value)


def _control_values(controls):
    c = controls or {}
    throttle = _bounded_control(c.get('throttle', 0.), -1., 1.)
    brake = _bounded_control(c.get('brake', 0.), 0., 1.)
    steer = _bounded_control(c.get('steer', 0.), -1., 1.)
    fan = _bounded_control(c.get('fan', 0.), 0., 1.)
    raw = c.get('nozzles', ())
    duties = [_bounded_control(raw[i], 0., 1.) if i < len(raw) else 0. for i in range(10)]
    return throttle, brake, steer, fan, duties


def dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def add(a, b):
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])


def scale(a, s):
    return (a[0]*s, a[1]*s, a[2]*s)


def unit(v):
    n = math.sqrt(dot(v, v))
    return scale(v, 1./n) if n > 1.e-10 else (0., 0., 0.)


def q_rotate(q, v):
    """Rotate a body vector into world space by a normalized (w,x,y,z) q."""
    w, x, y, z = q
    tx = 2. * (y*v[2] - z*v[1])
    ty = 2. * (z*v[0] - x*v[2])
    tz = 2. * (x*v[1] - y*v[0])
    return (v[0] + w*tx + y*tz-z*ty,
            v[1] + w*ty + z*tx-x*tz,
            v[2] + w*tz + x*ty-y*tx)


def q_inverse_rotate(q, v):
    return q_rotate((q[0], -q[1], -q[2], -q[3]), v)


def q_axis(axis, angle):
    """Axis-angle helper for setting an initial pose or constructing scenarios."""
    s = math.sin(angle*.5)
    v = unit(axis)
    return (math.cos(angle*.5), v[0]*s, v[1]*s, v[2]*s)


def q_advance(q, omega, dt):
    """Exact quaternion increment for a constant world angular velocity."""
    magnitude = math.sqrt(dot(omega, omega))
    if magnitude < 1.e-10:
        return q
    half = .5*magnitude*dt
    s = math.sin(half)/magnitude
    a, b, c, d = math.cos(half), omega[0]*s, omega[1]*s, omega[2]*s
    e, f, g, h = q
    result = (a*e-b*f-c*g-d*h, a*f+b*e+c*h-d*g,
              a*g-b*h+c*e+d*f, a*h+b*g-c*f+d*e)
    a, b, c, d = result
    inv = 1./math.sqrt(a*a+b*b+c*c+d*d)
    return (a*inv, b*inv, c*inv, d*inv)


def _track_angle(y, z):
    a = math.atan2(z-LOOP_Z, LOOP_RADIUS-y)
    return a if a >= 0. else a + TAU


def _track_here(x, y, z, scene):
    if scene == 'flat' or abs(x) > LOOP_HALF_WIDTH:
        return False
    if scene == 'loop' or z >= LOOP_Z:
        return True
    # The open ramp occupies the positive-Z semicircle. Negative Z is outside
    # except for the tiny original angular tolerance at its top edge. Keep the
    # exact atan2 query in a generous boundary band (also covering float32
    # rounding); ordinary inside/outside queries need no trigonometry.
    if y <= LOOP_RADIUS or LOOP_Z-z > (y-LOOP_RADIUS)*1.e-5:
        return False
    return _track_angle(y, z) <= PI + 1.e-8


def _box_local(box, point):
    dx, dy, dz = point[0]-box[0], point[1]-box[1], point[2]-box[2]
    return (box[6]*dx-box[7]*dz, dy, box[7]*dx+box[6]*dz)


def _box_vector(box, vector):
    return (box[6]*vector[0]+box[7]*vector[2], vector[1],
            -box[7]*vector[0]+box[6]*vector[2])


def _box_entry(local, direction, half, distance):
    """Slab intersection with an outside-to-inside box face, including t=0.

    Starting strictly inside has no entry face; overlap contacts handle that
    case. The direction need not be normalized, so this also sweeps segments.
    """
    near, far, face, facing = 0., distance, -1, 0.
    for axis in range(3):
        p, d, h = local[axis], direction[axis], half[axis]
        if abs(d) < 1.e-12:
            if abs(p) > h:
                return None
            continue
        a, b, sign = (-h-p)/d, (h-p)/d, -1.
        if a > b:
            a, b, sign = b, a, 1.
        if a >= near-1.e-10:
            near = a if a > 0. else 0.
            face, facing = axis, sign
        if b < far:
            far = b
        if near > far or far < 0.:
            return None
    if face < 0 or near > distance:
        return None
    # Only the final entering face matters. Build its normal once, after a
    # successful slab query, instead of allocating one for every candidate.
    normal = ((facing, 0., 0.) if face == 0 else
              (0., facing, 0.) if face == 1 else (0., 0., facing))
    return near, normal


def _obstacle_raycast(origin, direction, distance, obstacles, best):
    if not obstacles:
        return best
    # A ray segment can only enter boxes reached by its three projected spans.
    # The small margin makes this a conservative rejection on float32 too;
    # the unchanged local slab query still chooses the exact face and distance.
    reach_x = abs(direction[0])*distance+.001
    reach_y = abs(direction[1])*distance+.001
    reach_z = abs(direction[2])*distance+.001
    for box in obstacles:
        if (abs(origin[1]-box[1]) > box[4]+reach_y or
                abs(origin[0]-box[0]) > box[9]+reach_x or
                abs(origin[2]-box[2]) > box[10]+reach_z):
            continue
        local = _box_local(box, origin)
        vector = (box[6]*direction[0]-box[7]*direction[2], direction[1],
                  box[7]*direction[0]+box[6]*direction[2])
        hit = _box_entry(local, vector, box[3:6], distance)
        if hit is not None and (best is None or hit[0] < best[0]):
            best = (hit[0], _box_vector(box, hit[1]), box[8])
    return best


def raycast(origin, direction, distance, scene, obstacles=()):
    """Nearest analytic ground/track hit: (distance,normal,surface) or None.

    The loop is a finite-width, finite-thickness cylindrical ribbon. It is not
    an attachment path. Wheels and fans can lose it through either side.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    best = None
    if dy < -1.e-9:
        t = -oy / dy
        if -1.e-6 <= t <= distance:
            best = (max(0., t), (0., 1., 0.), 'ground')
    if scene == 'flat':
        return _obstacle_raycast(origin, direction, distance, obstacles, best) if obstacles else best
    cy, cz = oy-LOOP_RADIUS, oz-LOOP_Z
    a = dy*dy + dz*dz
    if a < 1.e-12:
        return _obstacle_raycast(origin, direction, distance, obstacles, best)
    b = cy*dy + cz*dz
    radial2 = cy*cy+cz*cz
    b2 = b*b
    closed = scene == 'loop'
    for radius, side in _TRACK_WALLS:
        discriminant = b2 - a*(radial2-radius*radius)
        if discriminant < 0.:
            continue
        root = math.sqrt(discriminant)
        for t in ((-b-root)/a, (-b+root)/a):
            if t < -1.e-6 or t > distance or (best is not None and t >= best[0]):
                continue
            px, py, pz = ox+dx*t, oy+dy*t, oz+dz*t
            if closed:
                if abs(px) > LOOP_HALF_WIDTH:
                    continue
            elif not _track_here(px, py, pz, scene):
                continue
            ny, nz = side*(py-LOOP_RADIUS)/radius, side*(pz-LOOP_Z)/radius
            if 0.*dx + ny*dy + nz*dz < -1.e-7:
                best = (max(0., t), (0., ny, nz), 'track')
        # A ray originating safely inside the cylinder must cross the inner
        # wall before the outer wall. Once that inner hit is valid (including
        # width and ramp limits), the outer wall cannot replace the nearer hit.
        if (side < 0. and best is not None and best[2] == 'track' and
                radial2 < _TRACK_SAFE_INNER2):
            break
    return _obstacle_raycast(origin, direction, distance, obstacles, best)


class Simulation:
    """step(dt, controls) holds input over fixed physical substeps.

    Default 120 Hz; set_step_hz(40) selects the badge budget; 60 Hz remains available.

    controls: throttle -1..1, brake 0..1, steer -1..1 (+left), fan 0..1,
    nozzles sequence of ten independent 0..1 duties in NOZZLES order.
    reset(scene) explicitly refills/repositions; refill_air() only refills.
    """
    def __init__(self, scene='loop', obstacles=()):
        self.air_mode = 'limited'
        self._configure_step_hz(120)
        self.scene_name = scene if scene in ('flat', 'ramp', 'loop') else 'loop'
        self.set_obstacles(obstacles)
        self.reset()

    def _configure_step_hz(self, hz):
        self.step_hz = hz
        self.fixed_dt = 1. / hz
        # The standalone budget is 125–133 ms. Badge mode explicitly lowers
        # it to four 40 Hz ticks (100 ms) to keep input/render polls bounded.
        self.max_substeps = 5 if hz == 40 else 8 if hz == 60 else MAX_SUBSTEPS
        self._fan_response = 1. - math.exp(-self.fixed_dt / _FAN_SPIN_TIME)
        self._fan_release = 1. - math.exp(-self.fixed_dt / FAN_FALL_TIME)
        self._fan_fill = 1. - math.exp(-self.fixed_dt / FAN_RISE_TIME)
        self._impact_steps = 6 if hz == 40 else 4
        self._impact_dt = self.fixed_dt / self._impact_steps
        self._impact_fan_response = 1. - math.exp(-self._impact_dt / _FAN_SPIN_TIME)
        self._impact_fan_release = 1. - math.exp(-self._impact_dt / FAN_FALL_TIME)
        self._impact_fan_fill = 1. - math.exp(-self._impact_dt / FAN_RISE_TIME)

    def set_step_hz(self, hz):
        """Select 40, 60 or 120 Hz without resetting pose, air, clock or pending time.

        Each segment uses an integer tick count from its time anchor, avoiding
        cumulative clock drift. A pending partial tick retains its elapsed
        time and is consumed under the newly selected fixed step.
        """
        if hz not in (40, 60, 120):
            raise ValueError('Physics step rate must be 40, 60 or 120 Hz')
        if hz != self.step_hz:
            self._time_base = self.time
            self._step_base = self.total_steps
            self._configure_step_hz(hz)
        return self.step_hz

    def set_obstacles(self, vehicles):
        """Configure fixed, collidable parked vehicles; reset preserves them.

        Entries are {id,x,z,yaw,length,width,height}, yaw in radians, ground
        Y=0. Optional collision_boxes overrides the default body/cabin shape:
        each (cx,cy,cz,hx,hy,hz) uses local centers and positive half extents,
        +X left/+Y up/+Z forward. Boxes approximate surfaces, not exact meshes.
        Wheels and fan probes see the same boxes as the chassis. The parked
        vehicles never receive dynamics or controls. At most 128 boxes are
        accepted, keeping collision work and storage explicitly bounded.
        """
        boxes = []
        for index, vehicle in enumerate(vehicles):
            x, z = number(vehicle.get('x', 0.), None), number(vehicle.get('z', 0.), None)
            yaw = number(vehicle.get('yaw', 0.), None)
            if x is None or z is None or yaw is None:
                raise ValueError('Invalid parked vehicle pose')
            c, s = math.cos(yaw), math.sin(yaw)
            shape = vehicle.get('collision_boxes')
            if shape is None:
                length = number(vehicle.get('length'), None)
                width = number(vehicle.get('width'), None)
                height = number(vehicle.get('height'), None)
                if length is None or width is None or height is None or min(length, width, height) <= 0.:
                    raise ValueError('Parked vehicle dimensions must be positive')
                # Low body plus inset cabin avoids filling the whole silhouette
                # with a roof-height wall across the nose and rear deck.
                shape = ((0., height*.28, 0., width*.5, height*.28, length*.5),
                         (0., height*.78, -length*.06, width*.41, height*.22, length*.26))
            label = 'vehicle:' + str(vehicle.get('id', index))
            for box in shape:
                if len(box) != 6:
                    raise ValueError('Collision box needs center XYZ and half XYZ')
                values = tuple(number(v, None) for v in box)
                if any(v is None for v in values) or min(values[3:]) <= 0.:
                    raise ValueError('Invalid parked vehicle collision box')
                cx, cy, cz, hx, hy, hz = values
                boxes.append((x+c*cx+s*cz, cy, z-s*cx+c*cz,
                              hx, hy, hz, c, s, label,
                              abs(c)*hx+abs(s)*hz, abs(s)*hx+abs(c)*hz))
                if len(boxes) > 128:
                    raise ValueError('Too many parked vehicle collision boxes')
        self.obstacles = tuple(boxes)
        self._near_cache_source = self._near_cache_pos = None
        self._near_cache_boxes = ()
        return len(boxes)

    def reset(self, scene=None):
        if scene in ('flat', 'ramp', 'loop'):
            self.scene_name = scene
        self.x, self.y, self.z = 0., GROUND_Y, -14.
        if self.scene_name == 'loop':
            self.y, self.z = .59, LOOP_Z
        self.vx = self.vy = self.vz = 0.
        self.q = (1., 0., 0., 0.)
        self._basis_q = None
        self._basis_value = None
        self._near_cache_source = self._near_cache_pos = None
        self._near_cache_boxes = ()
        self._hull_q = self._hull_q2 = None
        self._hull_value = self._hull_value2 = None
        self._hull_track_q = self._hull_track_q2 = None
        self._hull_track_pos = self._hull_track_pos2 = None
        self._hull_track_value = self._hull_track_value2 = None
        self.omega = [0., 0., 0.]
        self.speed = self.steering = self.wheel_spin = 0.
        self.time = self.distance = 0.
        self.refill_air()
        self.fan_force = self.fan_speed = self.fan_pressure = 0.
        self._fan_gap_value = .5
        self._fan_gap_pending = None
        self.contact_count = self.chassis_contacts = 0
        self.nozzle_forces = [0.] * 10
        self.wheel_compression = [0.] * 4
        self.wheel_loads = [0.] * 4
        self.wheel_contacts = [False] * 4
        self.surface = 'ground'
        self.airborne = False
        self.total_steps = self.last_substeps = 0
        self.total_refinements = self.last_refinements = 0
        self._time_base = 0.
        self._step_base = 0
        self.dropped_time = self._accumulator = 0.
        self._f = [0., 0., 0.]
        self._t = [0., 0., 0.]

    def refill_air(self):
        """Explicit development refill, preserving pose, motion and clock.

        Adding air changes body mass, as at reset. It does not supply an
        impulse, cancel a tumble, change fan state or replay held controls.
        """
        self.air_mass = TANK_MASS
        self.air_fraction = 1.
        self.pressure = TANK_PRESSURE
        self.mass = DRY_MASS + self.air_mass

    def set_air_mode(self, mode):
        """Explicit practice supply; changing it never changes pose or motion.

        Unlimited air is a game option, not a model of a finite pressure tank.
        Entering it fills the tank; leaving it starts from that current supply.
        """
        if mode not in ('limited', 'unlimited'):
            raise ValueError('Air mode must be limited or unlimited')
        if mode != self.air_mode:
            self.air_mode = mode
            if mode == 'unlimited':
                self.refill_air()
        return self.air_mode

    def lift_status(self):
        """Estimate vertical takeoff capability of all four lift nozzles.

        This is diagnostic telemetry, not an assist or a contact constraint.
        It compares current pressure, gravity, orientation and residual fan
        suction. Existing velocity, collisions and track geometry can still
        affect a launch. Air percentage measures usable mass, not thrust.
        """
        factor = supply_fraction(self.air_mass)
        force = sum(NOZZLES[i][3] for i in range(4, 8))*factor
        weight = (DRY_MASS+self.air_mass)*GRAVITY
        up = self.basis()[1][1]
        projection = max(0., up)
        ratio = force/weight
        vertical_ratio = (force-self.fan_force)*projection/weight
        if ratio <= 1.:
            reason, message = 'low_pressure', 'Low pressure: T refills air in place'
        elif up < .5 or force*projection <= weight:
            reason, message = 'orientation', 'Lift points sideways/down: roll upright'
        elif vertical_ratio <= 1.:
            reason, message = 'fan', 'Fan suction: F off for lift'
        else:
            reason, message = 'ready', 'Lift ready'
        return {'force': force, 'ratio': ratio, 'vertical_ratio': vertical_ratio,
                'up': up, 'reason': reason, 'message': message}

    def basis(self):
        # Quaternions are immutable tuples. Multiple controller, observer and
        # drawing requests for the same pose share these exact axis rotations;
        # any direct pose assignment also invalidates the identity check.
        q = self.q
        if self._basis_q is not q:
            # q_rotate of the three unit axes, spelled out with the same
            # literal zero/one products so each component rounds identically.
            w, x, y, z = q
            tx = 2. * (y*0. - z*0.)
            ty = 2. * (z*1. - x*0.)
            tz = 2. * (x*0. - y*1.)
            left = (1. + w*tx + y*tz-z*ty, 0. + w*ty + z*tx-x*tz, 0. + w*tz + x*ty-y*tx)
            tx = 2. * (y*0. - z*1.)
            ty = 2. * (z*0. - x*0.)
            tz = 2. * (x*1. - y*0.)
            up = (0. + w*tx + y*tz-z*ty, 1. + w*ty + z*tx-x*tz, 0. + w*tz + x*ty-y*tx)
            tx = 2. * (y*1. - z*0.)
            ty = 2. * (z*0. - x*1.)
            tz = 2. * (x*0. - y*0.)
            forward = (0. + w*tx + y*tz-z*ty, 0. + w*ty + z*tx-x*tz, 1. + w*tz + x*ty-y*tx)
            self._basis_value = (left, up, forward)
            self._basis_q = q
        return self._basis_value

    def step(self, dt, controls=None, control_filter=None, observer=None):
        """Advance physics, optionally sampling a controller each fixed tick.

        control_filter(self.fixed_dt, simulation, controls) returns normal controls.
        This lets portable timed controllers retain the same timing across
        render rates. Unfiltered manual input is normalized once per call.
        observer(self.fixed_dt, simulation, normalized_controls) samples completed
        ticks for telemetry. Its return value never changes the simulation.
        """
        dt = number(dt)
        self.last_substeps = 0
        self.last_refinements = 0
        if dt <= 0.:
            return 0
        accepted = min(dt, self.max_substeps*self.fixed_dt)
        self.dropped_time += dt-accepted
        self._accumulator += accepted
        c = controls or {}
        values = _control_values(c) if control_filter is None else None
        while self._accumulator + 1.e-9 >= self.fixed_dt and self.last_substeps < self.max_substeps:
            if control_filter is not None:
                values = _control_values(control_filter(self.fixed_dt, self, c))
            self._tick(*values)
            self._accumulator = max(0., self._accumulator-self.fixed_dt)
            self.total_steps += 1
            self.last_substeps += 1
            self.time = self._time_base + (self.total_steps-self._step_base)*self.fixed_dt
            if observer is not None:
                observer(self.fixed_dt, self, values)
        return self.last_substeps

    def _inv_inertia(self, v):
        return self._inv_inertia_xyz(v[0], v[1], v[2])

    def _inv_inertia_xyz(self, vx, vy, vz):
        # Contact impulses evaluate this many times at the same pose. Spell
        # out the two quaternion rotations to avoid their temporary vectors,
        # conjugate tuple and Python calls. Operation order matches q_rotate
        # exactly; this is still the full rotated anisotropic inertia tensor.
        w, x, y, z = self.q
        ix, iy, iz = -x, -y, -z
        tx = 2. * (iy*vz - iz*vy)
        ty = 2. * (iz*vx - ix*vz)
        tz = 2. * (ix*vy - iy*vx)
        ax = vx + w*tx + iy*tz-iz*ty
        ay = vy + w*ty + iz*tx-ix*tz
        az = vz + w*tz + ix*ty-iy*tx
        f = REFERENCE_MASS/self.mass
        ax, ay, az = ax/INERTIA[0]*f, ay/INERTIA[1]*f, az/INERTIA[2]*f
        tx = 2. * (y*az - z*ay)
        ty = 2. * (z*ax - x*az)
        tz = 2. * (x*ay - y*ax)
        return (ax + w*tx + y*tz-z*ty,
                ay + w*ty + z*tx-x*tz,
                az + w*tz + x*ty-y*tx)

    def _inertia(self, v):
        a = q_inverse_rotate(self.q, v)
        f = self.mass/REFERENCE_MASS
        return q_rotate(self.q, (a[0]*INERTIA[0]*f, a[1]*INERTIA[1]*f, a[2]*INERTIA[2]*f))

    def _point_velocity(self, r):
        x, y, z = self.omega
        return (self.vx+(y*r[2]-z*r[1]), self.vy+(z*r[0]-x*r[2]),
                self.vz+(x*r[1]-y*r[0]))

    def _force(self, force, r=(0., 0., 0.)):
        x, y, z = force
        self._f[0] += x
        self._f[1] += y
        self._f[2] += z
        self._t[0] += r[1]*z-r[2]*y
        self._t[1] += r[2]*x-r[0]*z
        self._t[2] += r[0]*y-r[1]*x

    def _impulse(self, impulse, r):
        self.vx += impulse[0]/self.mass
        self.vy += impulse[1]/self.mass
        self.vz += impulse[2]/self.mass
        dw = self._inv_inertia_xyz(r[1]*impulse[2]-r[2]*impulse[1],
                                  r[2]*impulse[0]-r[0]*impulse[2],
                                  r[0]*impulse[1]-r[1]*impulse[0])
        self.omega[0] += dw[0]
        self.omega[1] += dw[1]
        self.omega[2] += dw[2]

    def _effective_inverse_mass(self, r, n):
        dw = self._inv_inertia_xyz(r[1]*n[2]-r[2]*n[1],
                                  r[2]*n[0]-r[0]*n[2],
                                  r[0]*n[1]-r[1]*n[0])
        return 1./self.mass + (n[0]*(dw[1]*r[2]-dw[2]*r[1]) +
                              n[1]*(dw[2]*r[0]-dw[0]*r[2]) +
                              n[2]*(dw[0]*r[1]-dw[1]*r[0]))

    def _probe_obstacles(self, pos):
        # Keep a padded, order-preserving local neighborhood while its center
        # moves at most 2 m on each axis. Exact per-tick bounds below retain the
        # original subset, so wheel rays, fan gaps and chassis sweeps see the
        # same boxes. At a stationary lineup contact the whole collection no
        # longer needs scanning every tick. A direct obstacle replacement or
        # teleport invalidates the cache, and the final .05 m is rounding slack.
        source = self.obstacles
        anchor = self._near_cache_pos
        px, py, pz = pos
        if (self._near_cache_source is not source or anchor is None or
                abs(px-anchor[0]) > _NEAR_CACHE_MOVE or
                abs(py-anchor[1]) > _NEAR_CACHE_MOVE or
                abs(pz-anchor[2]) > _NEAR_CACHE_MOVE):
            candidates = []
            for box in source:
                if (abs(px-box[0]) <= box[9]+_NEAR_CACHE_REACH and
                        abs(py-box[1]) <= box[4]+_NEAR_CACHE_REACH and
                        abs(pz-box[2]) <= box[10]+_NEAR_CACHE_REACH):
                    candidates.append(box)
            self._near_cache_source = source
            self._near_cache_pos = pos
            self._near_cache_boxes = tuple(candidates)
        near = []
        for box in self._near_cache_boxes:
            if (abs(px-box[0]) <= box[9]+_NEAR_REACH and
                    abs(py-box[1]) <= box[4]+_NEAR_REACH and
                    abs(pz-box[2]) <= box[10]+_NEAR_REACH):
                near.append(box)
        return tuple(near)

    def _probe_scene(self, pos):
        # All wheel and skirt rays fit within this _BODY_REACH m sphere, including
        # their endpoints. If the sphere cannot reach the cylindrical ribbon,
        # the exact queries reduce to ordinary ground/parked-vehicle queries.
        # The cylinder void is convex, so its inner radius is also a safe miss.
        scene = self.scene_name
        if scene == 'flat':
            return scene
        if abs(pos[0]) > LOOP_HALF_WIDTH+_BODY_REACH:
            return 'flat'
        cy, cz = pos[1]-LOOP_RADIUS, pos[2]-LOOP_Z
        radius_squared = cy*cy+cz*cz
        if (radius_squared < (LOOP_RADIUS-_BODY_REACH)**2 or
                radius_squared > (LOOP_RADIUS+TRACK_THICKNESS+_BODY_REACH)**2):
            return 'flat'
        return scene

    def get_fan_gap(self):
        # With no fan speed or stored pressure, probe geometry contributes no
        # force. Defer just that geometry until a diagnostic or the next impact
        # classifier needs it. The saved pose is the exact sampling pose, even
        # if integration, a teleport or a later frame has moved the car.
        pending = self._fan_gap_pending
        if pending is not None:
            self._fan_gap_value = self._fan_surface(*pending)[0]
            self._fan_gap_pending = None
        return self._fan_gap_value

    def _fan_surface(self, down, pos, obstacles, probe_scene, q, weighted):
        """Sample sealed area, not an assumed flow/leak equilibrium.

        A cubic seal curve has zero slope at full contact and at release. Each
        sample contributes its own area and pressure-centre moment, so peeling
        over an edge reduces adhesion progressively instead of snapping it off.
        The flat-plane path uses the same distances as the general ray query.
        """
        if obstacles is None:
            obstacles = self.obstacles
        if probe_scene is None:
            probe_scene = self._probe_scene(pos)
        count = len(SKIRT_PROBES)
        if probe_scene == 'flat' and not obstacles and (pos[1] > _BODY_REACH or down[1] >= -1.e-9):
            return FAN_CAPTURE_HEIGHT, 0., 0., 0.
        gaps = cx = cz = area = 0.
        flat = probe_scene == 'flat' and not obstacles
        w, x, y, z = q
        for px, py, pz in SKIRT_PROBES:
            tx = 2.*(y*pz-z*py)
            ty = 2.*(z*px-x*pz)
            tz = 2.*(x*py-y*px)
            height = pos[1]+py+w*ty+z*tx-x*tz
            if flat:
                distance = -height/down[1] if down[1] < -1.e-9 else -1.
            else:
                origin = (pos[0]+px+w*tx+y*tz-z*ty, height,
                          pos[2]+pz+w*tz+x*ty-y*tx)
                hit = raycast(origin, down, FAN_CAPTURE_HEIGHT, probe_scene, obstacles)
                distance = hit[0] if hit is not None else -1.
            if distance < -1.e-6 or distance >= FAN_CAPTURE_HEIGHT:
                gap, seal = FAN_CAPTURE_HEIGHT, 0.
            else:
                gap = max(0., distance)
                if gap <= FAN_SEAL_HEIGHT:
                    seal = 1.
                else:
                    t = (FAN_CAPTURE_HEIGHT-gap)/_FAN_SPAN
                    seal = t*t*(3.-2.*t)
            gaps += gap
            if weighted:
                area += seal
                cx += px*seal
                cz += pz*seal
        return gaps/count, cx, cz, area

    def _fan(self, command, down, pos, obstacles=None, probe_scene=None):
        self.fan_speed += (command-self.fan_speed)*self._fan_response
        if obstacles is None:
            obstacles = self.obstacles
        if probe_scene is None:
            probe_scene = self._probe_scene(pos)
        if self.fan_speed == 0. and self.fan_pressure == 0.:
            self._fan_gap_pending = (down, pos, obstacles, probe_scene, self.q, False)
            self.fan_force = 0.
            return
        gap, cx, cz, area = self._fan_surface(down, pos, obstacles, probe_scene, self.q, True)
        self._fan_gap_value, self._fan_gap_pending = gap, None
        target = _FAN_PRESSURE*self.fan_speed*self.fan_speed*area/len(SKIRT_PROBES)
        response = self._fan_fill if target > self.fan_pressure else self._fan_release
        self.fan_pressure += (target-self.fan_pressure)*response
        self.fan_force = self.fan_pressure*FAN_AREA
        if self.fan_force:
            center = (cx/area, FAN_PLANE_Y, cz/area) if area else (0., FAN_PLANE_Y, 0.)
            self._force(scale(down, self.fan_force), q_rotate(self.q, center))

    def _thrusters(self, duties):
        air_mass = self.air_mass
        self.pressure = tank_pressure(air_mass)
        forces = self.nozzle_forces
        if not any(duties):
            if any(forces):
                for i in range(10):
                    forces[i] = 0.
            self.air_fraction = max(0., air_mass-TANK_RESIDUAL)/(TANK_MASS-TANK_RESIDUAL)
            self.mass = DRY_MASS+air_mass
            return
        supplied = TANK_MASS if self.air_mode == 'unlimited' else air_mass
        consumed = meter_nozzles(duties, supplied, self.fixed_dt, forces)
        axes = None
        for i in range(10):
            f = forces[i]
            if f > 0.:
                layout = NOZZLES[i]
                # Rear and lift groups share exact forward/up directions. The
                # current pose's basis already contains those same quaternion
                # rotations, so a combined boost/lift/flip need not repeat them
                # for each nozzle. Preserve each nozzle's force/torque order.
                if axes is None:
                    axes = self.basis()
                    w, x, y, z = self.q
                    fl = self._f
                    tl = self._t
                direction = (axes[2] if i < 4 else axes[1] if i < 8 else
                             axes[0] if i == 8 else q_rotate(self.q, layout[2]))
                fx, fy, fz = direction[0]*f, direction[1]*f, direction[2]*f
                p0, p1, p2 = layout[1]
                tx = 2. * (y*p2 - z*p1)
                ty = 2. * (z*p0 - x*p2)
                tz = 2. * (x*p1 - y*p0)
                r0 = p0 + w*tx + y*tz-z*ty
                r1 = p1 + w*ty + z*tx-x*tz
                r2 = p2 + w*tz + x*ty-y*tx
                fl[0] += fx
                fl[1] += fy
                fl[2] += fz
                tl[0] += r1*fz-r2*fy
                tl[1] += r2*fx-r0*fz
                tl[2] += r0*fy-r1*fx
        if self.air_mode == 'limited':
            self.air_mass -= consumed
        self.air_fraction = max(0., self.air_mass-TANK_RESIDUAL)/(TANK_MASS-TANK_RESIDUAL)
        self.mass = DRY_MASS+self.air_mass

    def _wheel_hits(self, up, pos, obstacles, scene):
        """Populate reusable wheel contact records for this pose.

        Each record is a world-space lever arm followed by the surface normal.
        The plane specialization avoids building hit tuples in ordinary driving.
        """
        data = _WHEEL_SCRATCH
        flat = scene == 'flat' and not obstacles
        down = (-up[0], -up[1], -up[2])
        count = 0
        w, x, y, z = self.q
        for i in range(4):
            self.wheel_contacts[i] = False
            self.wheel_compression[i] = self.wheel_loads[i] = 0.
            if flat and (pos[1] > _BODY_REACH or up[1] <= .15):
                continue
            px, py, pz = WHEELS[i]
            tx, ty, tz = 2.*(y*pz-z*py), 2.*(z*px-x*pz), 2.*(x*py-y*px)
            rx, ry, rz = px+w*tx+y*tz-z*ty, py+w*ty+z*tx-x*tz, pz+w*tz+x*ty-y*tx
            if flat:
                distance = (pos[1]+ry)/up[1]
                if distance < -1.e-6 or distance > WHEEL_REACH:
                    continue
                distance = max(0., distance)
                nx, ny, nz, surface = 0., 1., 0., 'ground'
            else:
                hit = raycast((pos[0]+rx, pos[1]+ry, pos[2]+rz), down,
                              WHEEL_REACH, scene, obstacles)
                if hit is None or dot(up, hit[1]) <= .15:
                    continue
                distance, (nx, ny, nz), surface = hit
            k = i*6
            data[k], data[k+1], data[k+2] = rx+down[0]*distance, ry+down[1]*distance, rz+down[2]*distance
            data[k+3], data[k+4], data[k+5] = nx, ny, nz
            _WHEEL_SURFACES[i] = surface
            self.wheel_contacts[i] = True
            self.wheel_compression[i] = WHEEL_REACH-distance
            count += 1
        self.contact_count = count
        return count

    def _wheels(self, throttle, brake, steer, up, forward, pos, obstacles=None, probe_scene=None):
        """Suspension reactions and a bounded, dissipative contact-patch model.

        Springs carry static load; dampers oppose approach speed. The motor
        shares effort among supported wheels. Tyres remove lateral slip over a
        relaxation time and share their finite friction budget with propulsion,
        rolling resistance and brakes. All reactions are applied at contact;
        this function never edits velocity or attitude.
        """
        dt = self.fixed_dt
        speed = self.vx*forward[0]+self.vy*forward[1]+self.vz*forward[2]
        self.speed = speed
        desired = steer*.52/(1.+abs(speed)/22.)
        change = desired-self.steering
        self.steering += clamp(change, -3.*dt, 3.*dt)
        self.wheel_spin = (self.wheel_spin+speed*dt/WHEEL_RADIUS) % TAU
        if obstacles is None:
            obstacles = self.obstacles
        if probe_scene is None:
            probe_scene = self._probe_scene(pos)
        supported = self._wheel_hits(up, pos, obstacles, probe_scene)
        if not supported:
            return
        # The drivetrain has a power envelope and a smooth motor speed limit.
        # Boost remains a separate force and can exceed the motor-only speed.
        effort = DRIVE_FORCE if abs(speed)*DRIVE_FORCE <= DRIVE_POWER else DRIVE_POWER/abs(speed)
        top_speed = 40. if throttle >= 0. else 11.
        if speed*throttle > 0.:
            taper = clamp((top_speed-abs(speed))/4., 0., 1.)
            effort *= taper*taper*(3.-2.*taper)
        effort *= throttle*(1. if throttle >= 0. else .35)/supported
        front = q_rotate(self.q, (math.sin(self.steering), 0., math.cos(self.steering)))
        corner_mass = self.mass*.25
        slip_gain = corner_mass/(TYRE_RELAXATION+dt)
        stop_gain = corner_mass/dt
        ox, oy, oz = self.omega
        data, forces, torques = _WHEEL_SCRATCH, self._f, self._t
        for i in range(4):
            if not self.wheel_contacts[i]:
                continue
            k = i*6
            rx, ry, rz = data[k], data[k+1], data[k+2]
            nx, ny, nz = data[k+3], data[k+4], data[k+5]
            vx = self.vx+oy*rz-oz*ry
            vy = self.vy+oz*rx-ox*rz
            vz = self.vz+ox*ry-oy*rx
            compression = self.wheel_compression[i]
            approach = -(vx*nx+vy*ny+vz*nz)
            spring = SPRING*compression+BUMP_SPRING*max(0., compression-SPRING_TRAVEL)
            load = max(0., spring+DAMPER*approach)
            self.wheel_loads[i] = load
            self.surface = _WHEEL_SURFACES[i]
            # Project wheel heading onto this surface, including curved ramps.
            ax, ay, az = front if i < 2 else forward
            normal = ax*nx+ay*ny+az*nz
            ax, ay, az = ax-nx*normal, ay-ny*normal, az-nz*normal
            length = math.sqrt(ax*ax+ay*ay+az*az)
            if length > 1.e-9:
                ax, ay, az = ax/length, ay/length, az/length
            else:
                ax = ay = az = 0.
            sx, sy, sz = ny*az-nz*ay, nz*ax-nx*az, nx*ay-ny*ax
            along = vx*ax+vy*ay+vz*az
            across = vx*sx+vy*sy+vz*sz
            budget = TYRE_FRICTION*load
            # Stop no more than the existing patch motion in one step. The
            # rolling term therefore cannot alternate signs at a quiet stop.
            resistance = min(abs(along)*stop_gain,
                             ROLLING_RESISTANCE*load+brake*budget)
            longitudinal = effort-resistance if along > 0. else effort+resistance if along < 0. else effort
            lateral = -across*slip_gain
            demand2 = longitudinal*longitudinal+lateral*lateral
            if demand2 > budget*budget:
                reduction = budget/math.sqrt(demand2)
                longitudinal *= reduction
                lateral *= reduction
            fx = nx*load+ax*longitudinal+sx*lateral
            fy = ny*load+ay*longitudinal+sy*lateral
            fz = nz*load+az*longitudinal+sz*lateral
            forces[0] += fx
            forces[1] += fy
            forces[2] += fz
            torques[0] += ry*fz-rz*fy
            torques[1] += rz*fx-rx*fz
            torques[2] += rx*fy-ry*fx

    def _parked_contacts(self, old_pos, old_q, boxes=None):
        """Sweep chassis samples against fixed oriented boxes.

        Segment entry retains the near face even if one tick crosses an entire
        thin panel. Contact impulses and split projection then keep the body on
        that face; neither velocity nor attitude is reset. The chassis remains
        a sampled hull, so this is an inexpensive compound-box approximation.
        boxes may be a conservative, order-preserving subset of the obstacles.
        """
        contacts = []
        samples = None
        if boxes is None:
            boxes = self.obstacles
        # These swept center bounds are shared by every parked box. The _BODY_REACH m
        # margin contains both endpoint hulls and their linear sample sweeps.
        min_x, max_x = min(old_pos[0], self.x), max(old_pos[0], self.x)
        min_y, max_y = min(old_pos[1], self.y), max(old_pos[1], self.y)
        min_z, max_z = min(old_pos[2], self.z), max(old_pos[2], self.z)
        for box in boxes:
            reach_x, reach_z = box[9]+_BODY_REACH, box[10]+_BODY_REACH
            if (min_x-reach_x > box[0] or max_x+reach_x < box[0] or
                    min_z-reach_z > box[2] or max_z+reach_z < box[2] or
                    min_y-_BODY_REACH > box[1]+box[4] or max_y+_BODY_REACH < box[1]-box[4]):
                continue
            # The same chassis points are tested against every nearby box.
            # Rotate once, lazily, so a clear frame allocates no sample list.
            if samples is None:
                samples = []
                previous_vectors = self._hull_vectors(old_q)
                current_vectors = self._hull_vectors(self.q)
                for i, r in enumerate(current_vectors):
                    samples.append((r, (self.x+r[0], self.y+r[1], self.z+r[2]),
                                    add(old_pos, previous_vectors[i])))
            half = box[3:6]
            # Reject an individual swept point before rotating it into this
            # box. The world AABB contains the exact box plus the narrow
            # phase's .001 overlap tolerance (.002 covers rotated X/Z axes).
            # Samples above a parked roof are especially common during jumps.
            low_y, high_y = box[1]-box[4]-.001, box[1]+box[4]+.001
            low_x, high_x = box[0]-box[9]-.002, box[0]+box[9]+.002
            low_z, high_z = box[2]-box[10]-.002, box[2]+box[10]+.002
            for r, point, old_point in samples:
                if ((point[1] > high_y and old_point[1] > high_y) or
                        (point[1] < low_y and old_point[1] < low_y) or
                        (point[0] > high_x and old_point[0] > high_x) or
                        (point[0] < low_x and old_point[0] < low_x) or
                        (point[2] > high_z and old_point[2] > high_z) or
                        (point[2] < low_z and old_point[2] < low_z)):
                    continue
                local = _box_local(box, point)
                previous = _box_local(box, old_point)
                delta = (local[0]-previous[0], local[1]-previous[1], local[2]-previous[2])
                entry = _box_entry(previous, delta, half, 1.)
                if entry is not None:
                    normal = entry[1]
                    axis = 0 if normal[0] else 1 if normal[1] else 2
                    depth = half[axis]-local[axis]*normal[axis]+.003
                    if depth <= 0.:
                        continue
                elif (abs(local[0]) <= half[0]+.001 and abs(local[1]) <= half[1]+.001 and
                      abs(local[2]) <= half[2]+.001):
                    axis = 0
                    if half[1]-abs(local[1]) < half[0]-abs(local[0]):
                        axis = 1
                    if half[2]-abs(local[2]) < half[axis]-abs(local[axis]):
                        axis = 2
                    facing = 1. if local[axis] >= 0. else -1.
                    normal = ((facing, 0., 0.) if axis == 0 else
                              (0., facing, 0.) if axis == 1 else (0., 0., facing))
                    depth = half[axis]-abs(local[axis])+.003
                else:
                    continue
                self.surface = box[8]
                contacts.append([r, _box_vector(box, normal), depth, 0.])
        return contacts

    def _hull_vectors(self, q):
        # Sweeps need current and previous poses, and parked/track contacts can
        # share them. Retain only those two immutable quaternion results. The
        # previous endpoint of the next tick is normally already available;
        # direct scenario/reset pose assignments are caught by identity checks.
        if self._hull_q is q:
            return self._hull_value
        if self._hull_q2 is q:
            return self._hull_value2
        self._hull_q2, self._hull_value2 = self._hull_q, self._hull_value
        self._hull_q = q
        w, x, y, z = q
        vectors = []
        for p0, p1, p2 in HULL:
            tx = 2. * (y*p2 - z*p1)
            ty = 2. * (z*p0 - x*p2)
            tz = 2. * (x*p1 - y*p0)
            vectors.append((p0 + w*tx + y*tz-z*ty, p1 + w*ty + z*tx-x*tz,
                            p2 + w*tz + x*ty-y*tx))
        self._hull_value = vectors
        return vectors

    def _track_hull(self, q, pos):
        # Endpoint positions repeat across adjacent contact sweeps. Reuse the
        # exact previous world coordinates and inside-cylinder test; a split
        # correction, teleport or direct pose assignment invalidates the key.
        if self._hull_track_q is q and self._hull_track_pos == pos:
            return self._hull_track_value
        if self._hull_track_q2 is q and self._hull_track_pos2 == pos:
            return self._hull_track_value2
        self._hull_track_q2 = self._hull_track_q
        self._hull_track_pos2 = self._hull_track_pos
        self._hull_track_value2 = self._hull_track_value
        vectors = self._hull_vectors(q)
        px, py, pz = pos
        points = []
        inside = clear = True
        for r in vectors:
            x, y, z = px+r[0], py+r[1], pz+r[2]
            cy, cz = y-LOOP_RADIUS, z-LOOP_Z
            radial2 = cy*cy+cz*cz
            points.append((x, y, z, radial2))
            if radial2 >= _TRACK_SAFE_INNER2:
                inside = False
            if y < .003:
                clear = False
        result = vectors, points, inside, clear
        self._hull_track_q = q
        self._hull_track_pos = pos
        self._hull_track_value = result
        return result

    def _contacts(self, old_pos, old_q, boxes=None):
        flat = self.scene_name == 'flat'
        if not flat:
            # Swept chassis samples cannot reach the finite track ribbon when
            # the entire segment of their bounding sphere misses its bounds.
            # Ground and parked contacts remain active outside those bounds.
            reach = LOOP_RADIUS+TRACK_THICKNESS+_BODY_REACH
            flat = ((old_pos[0] > LOOP_HALF_WIDTH+_BODY_REACH and self.x > LOOP_HALF_WIDTH+_BODY_REACH) or
                    (old_pos[0] < -LOOP_HALF_WIDTH-_BODY_REACH and self.x < -LOOP_HALF_WIDTH-_BODY_REACH) or
                    (old_pos[1] > LOOP_RADIUS+reach and self.y > LOOP_RADIUS+reach) or
                    (old_pos[1] < LOOP_RADIUS-reach and self.y < LOOP_RADIUS-reach) or
                    (old_pos[2] > LOOP_Z+reach and self.z > LOOP_Z+reach) or
                    (old_pos[2] < LOOP_Z-reach and self.z < LOOP_Z-reach))
            if not flat:
                old_y, old_z = old_pos[1]-LOOP_RADIUS, old_pos[2]-LOOP_Z
                new_y, new_z = self.y-LOOP_RADIUS, self.z-LOOP_Z
                # Both complete endpoint hulls inside the circular void imply
                # every swept sample remains inside it too. Keep the original
                # narrow phase for every approach that could touch the ribbon.
                inner = (LOOP_RADIUS-_BODY_REACH)**2
                flat = (old_y*old_y+old_z*old_z < inner and
                        new_y*new_y+new_z*new_z < inner)
        clear_floor = False
        if flat:
            # A conservative oriented box encloses every chassis sample. If
            # its lowest point clears the floor there cannot be a contact.
            # This avoids 16 current and 16 previous quaternion rotations on
            # ordinary suspension-supported driving and airborne frames.
            w, x, y, z = self.q
            left_y = 2.*(x*y+w*z)
            up_y = 1.-2.*(x*x+z*z)
            forward_y = 2.*(y*z-w*x)
            lowest = (self.y + _HULL_CENTER[0]*left_y + _HULL_CENTER[1]*up_y + _HULL_CENTER[2]*forward_y -
                      _HULL_HALF[0]*abs(left_y) - _HULL_HALF[1]*abs(up_y) - _HULL_HALF[2]*abs(forward_y))
            clear_floor = lowest > .00300001
            if clear_floor and not self.obstacles:
                self.chassis_contacts = 0
                return
        contacts = []
        if clear_floor:
            pass
        elif flat:
            w, x, y, z = self.q
            sx, sy, sz = self.x, self.y, self.z
            for local in HULL:
                p0, p1, p2 = local
                tx = 2. * (y*p2 - z*p1)
                ty = 2. * (z*p0 - x*p2)
                tz = 2. * (x*p1 - y*p0)
                r1 = p1 + w*ty + z*tx-x*tz
                py = sy+r1
                if py < .003:
                    contacts.append([(p0 + w*tx + y*tz-z*ty, r1, p2 + w*tz + x*ty-y*tx),
                                     (0., 1., 0.), .003-py, 0.])
        else:
            previous = self._track_hull(old_q, old_pos)
            current = self._track_hull(self.q, (self.x, self.y, self.z))
            vectors, points, inside, clear = current
            old_points = previous[1]
            # A convex cylinder contains every linear sweep whose endpoints
            # both lie safely inside it. Its finite side caps are outside this
            # region too, so only ground/parked contacts can remain.
            inside = inside and previous[2]
            if inside and clear and not self.obstacles:
                self.chassis_contacts = 0
                return
            scene = self.scene_name
            closed = scene == 'loop'
            for index in range(len(HULL)):
                r = vectors[index]
                p = points[index]
                if p[1] < .003:
                    contacts.append([r, (0., 1., 0.), .003-p[1], 0.])
                if inside:
                    continue
                old = old_points[index]
                # Sweep across the two finite ribbon edges. Without these caps a
                # direct sideways approach could pass through its thin side wall.
                # Only a point previously outside one side can enter that cap.
                # A point inside the width cannot satisfy either original test.
                for side in ((1.,) if old[0] > LOOP_HALF_WIDTH else
                             (-1.,) if old[0] < -LOOP_HALF_WIDTH else ()):
                    plane = side*LOOP_HALF_WIDTH
                    if old[0]*side > LOOP_HALF_WIDTH and p[0]*side <= LOOP_HALF_WIDTH:
                        t = (plane-old[0])/(p[0]-old[0])
                        iy = old[1]+t*(p[1]-old[1])
                        iz = old[2]+t*(p[2]-old[2])
                        rr = (iy-LOOP_RADIUS)**2+(iz-LOOP_Z)**2
                        if (LOOP_RADIUS**2 <= rr <= (LOOP_RADIUS+TRACK_THICKNESS)**2 and
                                _track_here(plane, iy, iz, scene)):
                            contacts.append([r, (side, 0., 0.),
                                             LOOP_HALF_WIDTH-side*p[0]+.003, 0.])
                cy, cz = p[1]-LOOP_RADIUS, p[2]-LOOP_Z
                # Ground and side caps were handled above. A sample ending safely
                # inside the inner wall, which did not start beyond the outer wall,
                # satisfies neither original radial contact condition. Keep the
                # exact square roots and crossing tests for every other sample.
                if p[3] < _TRACK_SAFE_INNER2:
                    if old[3] < _TRACK_SAFE_OUTER2:
                        continue
                # The closed loop's _track_here is only the width test.
                if closed:
                    if abs(p[0]) > LOOP_HALF_WIDTH:
                        continue
                elif not _track_here(p[0], p[1], p[2], scene):
                    continue
                radius = math.sqrt(cy*cy+cz*cz)
                old_radius = math.sqrt((old[1]-LOOP_RADIUS)**2+(old[2]-LOOP_Z)**2)
                outer = LOOP_RADIUS+TRACK_THICKNESS
                # Swept radial crossings keep fast corners from tunnelling through
                # the thin ribbon. Side/rim exits remain free; there is no rail.
                if radius > 1.e-6 and radius >= LOOP_RADIUS and (old_radius < LOOP_RADIUS or radius <= outer):
                    if old_radius <= LOOP_RADIUS+TRACK_THICKNESS*.5:
                        n = (0., -cy/radius, -cz/radius)
                        contacts.append([r, n, radius-LOOP_RADIUS+.003, 0.])
                    else:
                        n = (0., cy/radius, cz/radius)
                        contacts.append([r, n, outer-radius+.003, 0.])
                elif radius < LOOP_RADIUS and old_radius > outer:
                    n = (0., cy/radius, cz/radius)
                    contacts.append([r, n, outer-radius+.003, 0.])
        terrain_count = len(contacts)
        parked = self._parked_contacts(old_pos, old_q, boxes) if self.obstacles else ()
        contacts.extend(parked)
        count = len(contacts)
        self.chassis_contacts = count
        if not count:
            return
        # A contact batch keeps pose, mass and lever arms fixed. Build the
        # world inverse-inertia tensor once, then cache each normal response.
        # R diag(1/I) R^T is the same anisotropic rigid-body law as the two
        # quaternion transforms; regrouping the products changes floating-point
        # rounding, not the constraints, impulses or four solver iterations.
        left, up, forward = self.basis()
        l0, l1, l2 = left
        u0, u1, u2 = up
        e0, e1, e2 = forward
        mass = self.mass
        f = REFERENCE_MASS/mass
        i0, i1, i2 = f/_I0, f/_I1, f/_I2
        k00 = l0*l0*i0 + u0*u0*i1 + e0*e0*i2
        k01 = l0*l1*i0 + u0*u1*i1 + e0*e1*i2
        k02 = l0*l2*i0 + u0*u2*i1 + e0*e2*i2
        k11 = l1*l1*i0 + u1*u1*i1 + e1*e1*i2
        k12 = l1*l2*i0 + u1*u2*i1 + e1*e2*i2
        k22 = l2*l2*i0 + u2*u2*i1 + e2*e2*i2
        inv_mass = 1./mass
        dt = self.fixed_dt
        for c in contacts:
            r0, r1, r2 = c[0]
            n0, n1, n2 = c[1]
            a0, a1, a2 = r1*n2-r2*n1, r2*n0-r0*n2, r0*n1-r1*n0
            d0 = k00*a0 + k01*a1 + k02*a2
            d1 = k01*a0 + k11*a1 + k12*a2
            d2 = k02*a0 + k12*a1 + k22*a2
            c.append(inv_mass + (n0*(d1*r2-d2*r1) + n1*(d2*r0-d0*r2) + n2*(d0*r1-d1*r0)))
            c.append(min(3., max(0., c[2]-.002)*.16/dt))
            c.append((d0, d1, d2))
        # Sequential unilateral impulses. Friction acts at each actual contact
        # point; roof sliding and tumbling obey the same rules as floor impacts.
        vx, vy, vz = self.vx, self.vy, self.vz
        omega = self.omega
        ox, oy, oz = omega
        for iteration in range(4):
            for c in contacts:
                r0, r1, r2 = c[0]
                n0, n1, n2 = c[1]
                accumulated = c[3]
                v0 = vx+(oy*r2-oz*r1)
                v1 = vy+(oz*r0-ox*r2)
                v2 = vz+(ox*r1-oy*r0)
                vn = v0*n0 + v1*n1 + v2*n2
                rebound = -.08*vn if iteration == 0 and vn < -1. else 0.
                impulse = (c[5]+rebound-vn)/c[4]
                new = max(0., accumulated+impulse)
                impulse = new-accumulated
                c[3] = new
                vx += n0*impulse/mass
                vy += n1*impulse/mass
                vz += n2*impulse/mass
                normal_response = c[6]
                ox += normal_response[0]*impulse
                oy += normal_response[1]*impulse
                oz += normal_response[2]*impulse
                if new <= 0.:
                    continue
                v0 = vx+(oy*r2-oz*r1)
                v1 = vy+(oz*r0-ox*r2)
                v2 = vz+(ox*r1-oy*r0)
                vd = -(v0*n0 + v1*n1 + v2*n2)
                t0, t1, t2 = v0+n0*vd, v1+n1*vd, v2+n2*vd
                tangent_speed = math.sqrt(t0*t0 + t1*t1 + t2*t2)
                if tangent_speed > 1.e-7:
                    inv = 1./tangent_speed
                    t0, t1, t2 = t0*inv, t1*inv, t2*inv
                    a0, a1, a2 = r1*t2-r2*t1, r2*t0-r0*t2, r0*t1-r1*t0
                    d0 = k00*a0 + k01*a1 + k02*a2
                    d1 = k01*a0 + k11*a1 + k12*a2
                    d2 = k02*a0 + k12*a1 + k22*a2
                    jt = min(tangent_speed/(inv_mass + (t0*(d1*r2-d2*r1) + t1*(d2*r0-d0*r2) + t2*(d0*r1-d1*r0))),
                             .45*new/4.)
                    jt = -jt
                    vx += t0*jt/mass
                    vy += t1*jt/mass
                    vz += t2*jt/mass
                    ox += d0*jt
                    oy += d1*jt
                    oz += d2*jt
        self.vx, self.vy, self.vz = vx, vy, vz
        omega[0], omega[1], omega[2] = ox, oy, oz
        # Small split correction resolves numerical overlap without adding
        # energy or resetting attitude; collisions provide all angular response.
        c0 = c1 = c2 = 0.
        for k in range(terrain_count):
            c = contacts[k]
            n = c[1]
            depth = max(0., c[2]-.001)
            c0 += n[0]*depth*.35/terrain_count
            c1 += n[1]*depth*.35/terrain_count
            c2 += n[2]*depth*.35/terrain_count
        # Parked boxes have thin bodywork and need the complete swept separation
        # rather than the gentle ground overlap correction. Project the shared
        # translation against contact planes, not once per sample (which would
        # multiply the correction at a flat roof). This changes no velocities,
        # clocks, fuel or body orientation and permits tangential roof sliding.
        if parked:
            for iteration in range(4):
                for c in parked:
                    n0, n1, n2 = c[1]
                    remaining = max(0., c[2]-.001-(c0*n0 + c1*n1 + c2*n2))
                    c0 += n0*remaining
                    c1 += n1*remaining
                    c2 += n2*remaining
        self.x += c0
        self.y += c1
        self.z += c2

    def _tick(self, throttle, brake, steer, fan, duties):
        # The stiff suspension/bump stops need finer integration on a hard
        # landing. A single 40 or 60 Hz step can overshoot the first wheel contact,
        # creating a large asymmetric bounce from an otherwise level drop.
        # Refine only genuine stiff events near the floor: a descent whose
        # skirt probes already see a surface (or any very fast descent), a
        # partially supported or chassis-borne body moving or rotating fast,
        # and a fully wheel-supported body whose suspension is still being
        # driven in fast. Steady rolling on the track or the ground, even on
        # the bump stops, keeps the cheaper selected step rate.
        if self.step_hz > 60 or self.y + min(0., self.vy)*self.fixed_dt >= 3.2:
            self._integrate_tick(throttle, brake, steer, fan, duties)
            return
        omega = self.omega
        fast = abs(self.vy) > 2. or abs(omega[0]) > 1. or abs(omega[2]) > 1.
        n = self.contact_count
        wc = self.wheel_compression
        if n == 4:
            stiff = False
            if fast or wc[0] > SPRING_TRAVEL or wc[1] > SPRING_TRAVEL or wc[2] > SPRING_TRAVEL or wc[3] > SPRING_TRAVEL:
                # The previous tick's load splits into spring, bump-stop and
                # damper parts; the damper part is the approach rate. Only a
                # wheel still compressing faster than 1 m/s is a transient.
                wl = self.wheel_loads
                for i in range(4):
                    c = wc[i]
                    excess = wl[i]-SPRING*c
                    if c > SPRING_TRAVEL:
                        excess -= BUMP_SPRING*(c-SPRING_TRAVEL)
                    if excess > DAMPER or excess < -DAMPER:
                        stiff = True
                        break
        elif n == 0 and not self.chassis_contacts:
            stiff = self.vy < -2. and (self.get_fan_gap() < .5 or self.vy < -12. or self.basis()[1][1] < .7)
        else:
            stiff = fast
        if not stiff:
            self._integrate_tick(throttle, brake, steer, fan, duties)
            return
        # Controls and observers still run once per public fixed tick. These
        # six (40 Hz) or four (60 Hz) internal 240 Hz steps advance the same time,
        # without imposing an upright attitude or cancelling impact velocity.
        saved = (self.fixed_dt, self._fan_response, self._fan_release, self._fan_fill)
        self.total_refinements += 1
        self.last_refinements += 1
        self.fixed_dt = self._impact_dt
        self._fan_response = self._impact_fan_response
        self._fan_release = self._impact_fan_release
        self._fan_fill = self._impact_fan_fill
        try:
            for _ in range(self._impact_steps):
                self._integrate_tick(throttle, brake, steer, fan, duties)
        finally:
            self.fixed_dt, self._fan_response, self._fan_release, self._fan_fill = saved

    def _integrate_tick(self, throttle, brake, steer, fan, duties):
        old_pos = (self.x, self.y, self.z)
        old_q = self.q
        left, up, forward = self.basis()
        fl = self._f
        tl = self._t
        fl[0] = 0.
        fl[1] = -self.mass*GRAVITY
        fl[2] = 0.
        tl[0] = tl[1] = tl[2] = 0.
        obstacles = self._probe_obstacles(old_pos) if self.obstacles else ()
        probe_scene = self._probe_scene(old_pos) if self.scene_name != 'flat' else 'flat'
        self._fan(fan, (up[0]*-1., up[1]*-1., up[2]*-1.), old_pos, obstacles, probe_scene)
        self._thrusters(duties)
        self._wheels(throttle, brake, steer, up, forward, old_pos, obstacles, probe_scene)
        # Thrust may have changed the mass; the remaining terms use that value.
        mass = self.mass
        dt = self.fixed_dt
        vx, vy, vz = self.vx, self.vy, self.vz
        speed = math.sqrt(vx*vx + vy*vy + vz*vz)
        drag = _DRAG*speed
        # Aerodynamic drag acts at the centre: its torque terms are exact
        # zeros, which never change the (+0 or nonzero) torque accumulators.
        fl[0] += vx*drag
        fl[1] += vy*drag
        fl[2] += vz*drag
        # Weak rotational aerodynamic resistance, including airborne motion.
        omega = self.omega
        o0, o1, o2 = omega
        tl[0] -= o0*35.
        tl[1] -= o1*35.
        tl[2] -= o2*35.
        # Advance world angular momentum, then solve orientation at a midpoint.
        # This keeps torque-free angular momentum conserved even while a tumble
        # changes the world-space inertia tensor (without a gyro Euler blowup).
        # momentum = _inertia(omega) + torque*dt
        w, x, y, z = old_q
        ix, iy, iz = -x, -y, -z
        tx = 2. * (iy*o2 - iz*o1)
        ty = 2. * (iz*o0 - ix*o2)
        tz = 2. * (ix*o1 - iy*o0)
        a0 = o0 + w*tx + iy*tz-iz*ty
        a1 = o1 + w*ty + iz*tx-ix*tz
        a2 = o2 + w*tz + ix*ty-iy*tx
        f = mass/REFERENCE_MASS
        a0, a1, a2 = a0*_I0*f, a1*_I1*f, a2*_I2*f
        tx = 2. * (y*a2 - z*a1)
        ty = 2. * (z*a0 - x*a2)
        tz = 2. * (x*a1 - y*a0)
        m0 = a0 + w*tx + y*tz-z*ty + tl[0]*dt
        m1 = a1 + w*ty + z*tx-x*tz + tl[1]*dt
        m2 = a2 + w*tz + x*ty-y*tx + tl[2]*dt
        f = REFERENCE_MASS/mass
        # angular_start = _inv_inertia(momentum) at the old pose
        tx = 2. * (iy*m2 - iz*m1)
        ty = 2. * (iz*m0 - ix*m2)
        tz = 2. * (ix*m1 - iy*m0)
        bx = m0 + w*tx + iy*tz-iz*ty
        by = m1 + w*ty + iz*tx-ix*tz
        bz = m2 + w*tz + ix*ty-iy*tx
        bx, by, bz = bx/_I0*f, by/_I1*f, bz/_I2*f
        tx = 2. * (y*bz - z*by)
        ty = 2. * (z*bx - x*bz)
        tz = 2. * (x*by - y*bx)
        o0 = bx + w*tx + y*tz-z*ty
        o1 = by + w*ty + z*tx-x*tz
        o2 = bz + w*tz + x*ty-y*tx
        # q = q_advance(old_q, angular_start, dt*.5)
        magnitude = math.sqrt(o0*o0 + o1*o1 + o2*o2)
        if magnitude < 1.e-10:
            q = old_q
        else:
            half = .5*magnitude*(dt*.5)
            s = math.sin(half)/magnitude
            a, b, c, d = math.cos(half), o0*s, o1*s, o2*s
            qa, qb, qc, qd = (a*w-b*x-c*y-d*z, a*x+b*w+c*z-d*y,
                              a*y-b*z+c*w+d*x, a*z+b*y-c*x+d*w)
            inv = 1./math.sqrt(qa*qa+qb*qb+qc*qc+qd*qd)
            q = (qa*inv, qb*inv, qc*inv, qd*inv)
        self.q = q
        # angular_mid = _inv_inertia(momentum) at the midpoint pose
        w, x, y, z = q
        ix, iy, iz = -x, -y, -z
        tx = 2. * (iy*m2 - iz*m1)
        ty = 2. * (iz*m0 - ix*m2)
        tz = 2. * (ix*m1 - iy*m0)
        bx = m0 + w*tx + iy*tz-iz*ty
        by = m1 + w*ty + iz*tx-ix*tz
        bz = m2 + w*tz + ix*ty-iy*tx
        bx, by, bz = bx/_I0*f, by/_I1*f, bz/_I2*f
        tx = 2. * (y*bz - z*by)
        ty = 2. * (z*bx - x*bz)
        tz = 2. * (x*by - y*bx)
        o0 = bx + w*tx + y*tz-z*ty
        o1 = by + w*ty + z*tx-x*tz
        o2 = bz + w*tz + x*ty-y*tx
        # q = q_advance(old_q, angular_mid, dt)
        magnitude = math.sqrt(o0*o0 + o1*o1 + o2*o2)
        if magnitude < 1.e-10:
            q = old_q
        else:
            half = .5*magnitude*dt
            s = math.sin(half)/magnitude
            a, b, c, d = math.cos(half), o0*s, o1*s, o2*s
            w, x, y, z = old_q
            qa, qb, qc, qd = (a*w-b*x-c*y-d*z, a*x+b*w+c*z-d*y,
                              a*y-b*z+c*w+d*x, a*z+b*y-c*x+d*w)
            inv = 1./math.sqrt(qa*qa+qb*qb+qc*qc+qd*qd)
            q = (qa*inv, qb*inv, qc*inv, qd*inv)
        self.q = q
        # omega = _inv_inertia(momentum) at the completed pose
        w, x, y, z = q
        ix, iy, iz = -x, -y, -z
        tx = 2. * (iy*m2 - iz*m1)
        ty = 2. * (iz*m0 - ix*m2)
        tz = 2. * (ix*m1 - iy*m0)
        bx = m0 + w*tx + iy*tz-iz*ty
        by = m1 + w*ty + iz*tx-ix*tz
        bz = m2 + w*tz + ix*ty-iy*tx
        bx, by, bz = bx/_I0*f, by/_I1*f, bz/_I2*f
        tx = 2. * (y*bz - z*by)
        ty = 2. * (z*bx - x*bz)
        tz = 2. * (x*by - y*bx)
        omega[0] = bx + w*tx + y*tz-z*ty
        omega[1] = by + w*ty + z*tx-x*tz
        omega[2] = bz + w*tz + x*ty-y*tx
        vx += fl[0]/mass*dt
        vy += fl[1]/mass*dt
        vz += fl[2]/mass*dt
        self.vx, self.vy, self.vz = vx, vy, vz
        self.x += vx*dt
        self.y += vy*dt
        self.z += vz*dt
        # The chassis sweep reuses the tick's nearby-box selection while the
        # tick moved at most _NEAR_MOVE per axis; otherwise it sweeps all.
        boxes = None
        if (self.obstacles and abs(self.x-old_pos[0]) <= _NEAR_MOVE and
                abs(self.y-old_pos[1]) <= _NEAR_MOVE and abs(self.z-old_pos[2]) <= _NEAR_MOVE):
            boxes = obstacles
        self._contacts(old_pos, old_q, boxes)
        # Refresh the completed pose's basis inside physics itself. Observers
        # then only read it, and the following tick reuses the same axes.
        forward = self.basis()[2]
        self.speed = self.vx*forward[0] + self.vy*forward[1] + self.vz*forward[2]
        self.distance += math.sqrt((self.x-old_pos[0])**2+(self.y-old_pos[1])**2+(self.z-old_pos[2])**2)
        self.airborne = self.contact_count == 0 and self.chassis_contacts == 0
