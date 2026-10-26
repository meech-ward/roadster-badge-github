"""Independent, photo-observed mesh of the October 2026 Tesla Roadster.

Constructed here from Tesla's public front, side and rear photographs. No
imported mesh, upstream model generator, sampled shape array or physics source
is used. This is a hand-built visual approximation, not Tesla CAD or measured
production dimensions. Wheel and jet geometry share this game's vehicle design.

Coordinates: +X left, +Y up, +Z front; tyres touch y=0. Wheel geometry is local
to its explicit pivot and spins about X. Other parts have a zero pivot.
Colours are native pico3d vertex RGB packed as 0x00BBGGRR. Only math is needed.
"""

import math
from vehicle_spec import (WHEEL_RADIUS, WHEELBASE, WHEEL_TRACK, GROUND_Y, NOZZLES)


REFERENCE_URLS = (
    'https://www.tesla.com/roadster',
    'https://digitalassets.tesla.com/tesla-contents/image/upload/f_auto,q_auto/Roadster-03-Carousel-Slide-1-Desktop.jpg',
    'https://digitalassets.tesla.com/tesla-contents/image/upload/f_auto,q_auto/Roadster-02-Pilot-Desktop.jpg',
    'https://digitalassets.tesla.com/tesla-contents/image/upload/f_auto,q_auto/Roadster-01-Hero-Desktop.jpg',
)

WHEEL_CENTRES = tuple((x, WHEEL_RADIUS, z)
                      for z in (WHEELBASE*.5, -WHEELBASE*.5)
                      for x in (WHEEL_TRACK*.5, -WHEEL_TRACK*.5))
_AXLES = (-WHEELBASE*.5, WHEELBASE*.5)
WHEEL_NAMES = ('wheel_FL', 'wheel_FR', 'wheel_RL', 'wheel_RR')

# Independently chosen longitudinal stations: z, half width, centre height,
# shoulder height. Separate creases describe the low bonnet and high haunches.
_BODY_STATIONS = (
    (-2.30, 0.88, 0.76, 0.78), (-2.17, 0.99, 0.80, 0.84),
    (-1.90, 1.025, 0.83, 0.88), (-WHEELBASE*.5, 1.045, 0.86, 0.90),
    (-0.94, 1.015, 0.82, 0.87), (-0.50, 0.985, 0.78, 0.82),
    (0.10, 0.975, 0.74, 0.78), (0.65, 0.975, 0.74, 0.79),
    (1.00, 0.990, 0.73, 0.81), (WHEELBASE*.5, 0.995, 0.70, 0.80),
    (1.70, 0.980, 0.65, 0.78), (2.00, 0.935, 0.58, 0.70),
    (2.22, 0.855, 0.515, 0.59), (2.30, 0.765, 0.495, 0.51),
)

_WHITE = (225, 229, 231)
_EDGE = (245, 245, 241)
_DARK = (22, 25, 30)
_CARBON = (34, 39, 44)
_SILVER = (150, 160, 171)
_GLASS = (71, 61, 52)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _rgb(c):
    return int(c[0]) | (int(c[1]) << 8) | (int(c[2]) << 16)


class _Mesh:
    def __init__(self, name, pivot=(0.0, 0.0, 0.0)):
        self.name = name
        self.pivot = pivot
        self.positions = []
        self.indices = []
        self.colors = []
        self._lookup = {}

    def face(self, points, color, normal, shade=True):
        """Convex face, with explicit outward direction and flat baked light."""
        points = list(points)
        n = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
        if _dot(n, normal) < 0:
            points.reverse()
            n = (-n[0], -n[1], -n[2])
        length = math.sqrt(_dot(n, n))
        if length < 1e-10:
            raise ValueError('degenerate face in ' + self.name)
        if shade:
            # Fixed studio light lets an unlit, texture-free native material
            # preserve white paint and still describe the actual facets.
            light = max(0.0, (-0.36 * n[0] + 0.86 * n[1] + 0.36 * n[2]) / length)
            amount = 0.64 + 0.36 * light
            color = tuple(min(255, int(c * amount)) for c in color)
        packed = _rgb(color)
        ids = []
        for p in points:
            key = (p[0], p[1], p[2], packed)
            index = self._lookup.get(key)
            if index is None:
                index = len(self.colors)
                self._lookup[key] = index
                self.positions.extend(p)
                self.colors.append(packed)
            ids.append(index)
        for i in range(1, len(ids) - 1):
            self.indices.extend((ids[0], ids[i], ids[i + 1]))

    def result(self):
        return (self.name, self.positions, self.indices, self.colors, self.pivot)


def _profile(z):
    for i in range(len(_BODY_STATIONS) - 1):
        a, b = _BODY_STATIONS[i], _BODY_STATIONS[i + 1]
        if z <= b[0]:
            t = max(0.0, (z - a[0]) / (b[0] - a[0]))
            return tuple(a[j] + (b[j] - a[j]) * t for j in (1, 2, 3))
    return _BODY_STATIONS[-1][1:]


def _hood_point(sign, x, z, lift=.012):
    """Locate trim above the actual bonnet/fender skin, not inside it."""
    width, centre, shoulder = _profile(z)
    xs = (0, width*.67, width*.91, width)
    ys = (centre, centre+.014, shoulder, shoulder-.025)
    for i in range(3):
        if x <= xs[i+1]:
            t = (x-xs[i])/(xs[i+1]-xs[i])
            return (sign*x, ys[i]+(ys[i+1]-ys[i])*t+lift, z)
    return (sign*x, shoulder-.025+lift, z)


def _side_point(sign, y, z):
    """Keep the visible door seam outside the tapered painted side panel."""
    width, centre, shoulder = _profile(z)
    t = max(0, min(1, (y-.205)/(shoulder-.025-.205)))
    return (sign*(width*(.963+.037*t)+.017), y, z)


def _box(mesh, centre, size, color):
    x, y, z = centre
    a, b, c = (size[0] / 2, size[1] / 2, size[2] / 2)
    p = ((x-a, y-b, z-c), (x+a, y-b, z-c), (x+a, y+b, z-c), (x-a, y+b, z-c),
         (x-a, y-b, z+c), (x+a, y-b, z+c), (x+a, y+b, z+c), (x-a, y+b, z+c))
    for ids, normal in (((0, 1, 2, 3), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)),
                        ((0, 4, 7, 3), (-1, 0, 0)), ((1, 5, 6, 2), (1, 0, 0)),
                        ((3, 2, 6, 7), (0, 1, 0)), ((0, 1, 5, 4), (0, -1, 0))):
        mesh.face(tuple(p[i] for i in ids), color, normal)


def _strip(mesh, a, b, width, color, normal):
    direction = _sub(b, a)
    across = _cross(normal, direction)
    scale = width / (2 * math.sqrt(_dot(across, across)))
    across = tuple(v * scale for v in across)
    mesh.face(tuple(tuple(p[j] + sign * across[j] for j in range(3))
                    for p, sign in ((a, -1), (b, -1), (b, 1), (a, 1))),
              color, normal, False)


def _body(body, trim):
    # Longitudinal bonnet/deck panels. These are kept distinct from the side
    # skin, so neither a solid box nor a painted disc fills the wheel openings.
    for a, b in zip(_BODY_STATIONS, _BODY_STATIONS[1:]):
        for sign in (-1, 1):
            def row(st):
                z, width, centre, shoulder = st
                return ((0, centre, z), (sign * width * .67, centre + .014, z),
                        (sign * width * .91, shoulder, z), (sign * width, shoulder-.025, z))
            ra, rb = row(a), row(b)
            for k in range(3):
                body.face((ra[k], rb[k], rb[k+1], ra[k+1]), _WHITE,
                          (sign * .25, 1, 0))

    arch_radius = WHEEL_RADIUS+.055
    stations = [p[0] for p in _BODY_STATIONS]
    for centre in _AXLES:
        for i in range(11):
            stations.append(centre + arch_radius * math.cos(math.pi * i / 10))
    stations = sorted(set(stations))
    for sign in (-1, 1):
        for a, b in zip(stations, stations[1:]):
            mid = (a + b) * .5
            arch = None
            for centre in _AXLES:
                if abs(mid - centre) < arch_radius:
                    arch = centre
            wa, ca, ha = _profile(a)
            wb, cb, hb = _profile(b)
            la, lb = .205, .205
            if arch is not None:
                la = WHEEL_RADIUS + math.sqrt(max(0, arch_radius**2 - (a-arch)**2))
                lb = WHEEL_RADIUS + math.sqrt(max(0, arch_radius**2 - (b-arch)**2))
            # Slight inward taper below the waist gives a real rocker crease.
            xa = wa if la > .4 else wa * .963
            xb = wb if lb > .4 else wb * .963
            body.face(((sign * wa, ha-.025, a), (sign * wb, hb-.025, b),
                       (sign * xb, lb, b), (sign * xa, la, a)),
                      _WHITE, (sign, 0, 0))
            if arch is None:
                trim.face(((sign * xa, la, a), (sign * xb, lb, b),
                           (sign * (xb+.012), .153, b), (sign * (xa+.012), .153, a)),
                          _CARBON, (sign, 0, 0))
        # Arched inner wells are dark, recessed surfaces. No polygon crosses
        # the opening; the tyre's upper silhouette remains visible.
        for centre in _AXLES:
            for i in range(10):
                ta, tb = math.pi * i/10, math.pi * (i+1)/10
                za, zb = centre + arch_radius*math.cos(ta), centre+arch_radius*math.cos(tb)
                ya, yb = WHEEL_RADIUS+arch_radius*math.sin(ta), WHEEL_RADIUS+arch_radius*math.sin(tb)
                wa, wb = _profile(za)[0], _profile(zb)[0]
                trim.face(((sign*wa, ya, za), (sign*wb, yb, zb),
                           (sign*.67, yb, zb), (sign*.67, ya, za)),
                          (17, 20, 24), (0, -math.sin((ta+tb)/2), -math.cos((ta+tb)/2)))

    # Lower floor only spans between the tyres. The wider painted shell stays
    # genuinely open around all four wheel volumes.
    trim.face(((-.65, .16, -2.15), (.65, .16, -2.15),
               (.65, .16, 2.20), (-.65, .16, 2.20)), _DARK, (0, -1, 0))
    for z, normal in ((2.30, (0, 0, 1)), (-2.30, (0, 0, -1))):
        width, centre, shoulder = _profile(z)
        body.face(((-width, shoulder-.025, z), (0, centre, z),
                   (width, shoulder-.025, z), (width*.95, .30, z), (-width*.95, .30, z)),
                  _WHITE, normal)


def _canopy(body, glass, detail):
    # The new car's continuous low copper-black canopy, with straight bright
    # roof borders, is deliberately unlike the older rounded red Roadster.
    rows = ((.82, .81, .775), (.255, .645, 1.13),
            (-.56, .655, 1.15), (-1.245, .79, .90))
    colors = ((83, 68, 53), (39, 35, 32), (48, 43, 39))
    for index in range(3):
        za, wa, ya = rows[index]
        zb, wb, yb = rows[index+1]
        for sign in (-1, 1):
            glass.face(((0, ya+.012, za), (sign*wa, ya, za),
                        (sign*wb, yb, zb), (0, yb+.012, zb)), colors[index],
                       (0, 1, 1 if index == 0 else -1), False)
    for sign in (-1, 1):
        points = ((sign*.817, .787, .795), (sign*.653, 1.13, .255),
                  (sign*.663, 1.15, -.56), (sign*.803, .905, -1.245),
                  (sign*.919, .861, -1.19), (sign*.934, .773, .47))
        # Three facets provide a subtle bronze side reflection and a separate
        # rear quarter window without a texture or baked photograph.
        glass.face((points[0], points[1], points[2], points[5]), _GLASS, (sign, .1, 0), False)
        glass.face((points[5], points[2], points[3], points[4]), (50, 46, 41), (sign, .1, 0), False)
        for a, b in ((points[0], points[1]), (points[1], points[2]),
                     (points[2], points[3]), (points[4], points[5]), (points[5], points[0])):
            _strip(body, a, b, .017, _EDGE, (sign, .4, 0))
        # C pillars taper into broad rear fenders; A pillars join the bonnet.
        body.face((points[2], points[3], (sign*1.025, .88, -1.90),
                   (sign*.935, .881, -1.20)), _WHITE, (sign, .5, 0))
        body.face((points[0], points[1], (sign*.755, .87, .38),
                   (sign*.956, .79, .72)), _WHITE, (sign, .5, .2))
        # Door/window split, camera pod and flush door handle.
        _strip(detail, (sign*.668, 1.142, -.37), (sign*.918, .844, -.57),
               .020, (15, 17, 19), (sign, .2, 0))
        _strip(detail, _side_point(sign, .78, .57), _side_point(sign, .222, .42),
               .021, (51, 57, 62), (sign, 0, 0))
        _strip(detail, _side_point(sign, .827, -.71), _side_point(sign, .222, -.86),
               .021, (51, 57, 62), (sign, 0, 0))
        _strip(detail, _side_point(sign, .221, .42), _side_point(sign, .221, -.86),
               .016, (52, 58, 64), (sign, 0, 0))
        _box(detail, (sign*.994, .756, -.56), (.011, .020, .15), (54, 60, 67))
        # A small camera enclosure sits just behind each front wheel.
        _box(detail, (sign*.993, .696, .855), (.052, .035, .13), (22, 25, 30))
        _box(detail, (sign*1.023, .697, .845), (.004, .015, .026), (87, 115, 128))


def _lamps_and_trim(detail, trim):
    # Front: a flat central bonnet, recessed triangular lamp pockets and a
    # broad dark mouth beneath. These silhouettes are legible at badge scale.
    for sign in (-1, 1):
        lamp = tuple(_hood_point(sign, x, z) for x, z in
                     ((.641, 1.87), (.885, 1.87), (.644, 2.28)))
        detail.face(lamp, (12, 19, 26), (sign*.3, 1, .6), False)
        lens = tuple(_hood_point(sign, x, z, .021) for x, z in
                     ((.653, 1.945), (.826, 1.945), (.785, 2.025), (.649, 2.025)))
        detail.face(lens, (226, 247, 255), (sign*.3, 1, .6), False)
        # Three compact lens divisions sit above the white light strip.
        for i in range(3):
            x = .684+i*.034
            _strip(detail, _hood_point(sign, x, 1.954, .026),
                   _hood_point(sign, x-.009, 2.012, .026), .008,
                   (166, 206, 220), (0, 1, .6))
        _strip(detail, _hood_point(sign, .63, 2.293), _hood_point(sign, .65, .83),
               .012, (136, 143, 148), (0, 1, 0))
    trim.face(((-.665, .493, 2.329), (.665, .493, 2.329),
               (.735, .188, 2.310), (-.735, .188, 2.310)), (9, 14, 20), (0, 0, 1))
    _strip(detail, (-.660, .503, 2.330), (.660, .503, 2.330), .010,
           (230, 235, 236), (0, 0, 1))
    _box(trim, (0, .233, 2.252), (1.74, .055, .205), _CARBON)
    _box(trim, (0, .158, 2.285), (1.78, .027, .12), (18, 22, 27))
    for y in (.269, .365):
        _strip(trim, (-.61, y, 2.335), (.61, y, 2.335),
               .010, (25, 32, 41), (0, 0, 1))

    # Rear: a thin full-width red light below the sharp tail deck; dark
    # diffuser and four exposed rear thrust bells below the plate recess.
    _box(trim, (0, .661, -2.315), (1.74, .071, .035), (15, 18, 22))
    _strip(detail, (-.845, .677, -2.336), (.845, .677, -2.336),
           .019, (255, 44, 34), (0, 0, -1))
    _strip(detail, (-.842, .690, -2.337), (.842, .690, -2.337),
           .006, (255, 130, 112), (0, 0, -1))
    _box(trim, (0, .372, -2.225), (1.65, .245, .16), (18, 22, 27))
    _box(detail, (0, .535, -2.335), (.31, .105, .016), (156, 162, 164))
    for x in (-.66, -.35, .35, .66):
        _box(trim, (x, .206, -2.245), (.021, .17, .30), _CARBON)
    # Individually placed metallic letter-like strokes read as rear badging
    # without shipping a font, texture or externally sourced logo asset.
    for x in (-.21, -.105, 0, .105, .21):
        _strip(detail, (x-.023, .756, -2.307), (x+.023, .756, -2.307),
               .006, (224, 227, 228), (0, 0, -1))


def _nozzle(mesh, centre, outward, radius, segments=6, depth=.045):
    # A shallow recessed bell, dark throat, and narrow metallic rim. Rings
    # are true geometry rather than painted dots, so undersides remain useful.
    c = centre
    u = (1, 0, 0) if abs(outward[0]) < .5 else (0, 0, 1)
    v = _cross(outward, u)
    rings = []
    for r, d in ((radius, 0), (radius*.71, .003), (radius*.54, -depth)):
        rings.append([tuple(c[j] + r*(u[j]*math.cos(2*math.pi*i/segments)
                                     + v[j]*math.sin(2*math.pi*i/segments))
                            + outward[j]*d for j in range(3)) for i in range(segments)])
    for i in range(segments):
        k = (i+1) % segments
        mesh.face((rings[0][i], rings[0][k], rings[1][k], rings[1][i]),
                  (128, 139, 148), outward)
        radial = tuple(-(u[j]*math.cos(2*math.pi*(i+.5)/segments)
                         + v[j]*math.sin(2*math.pi*(i+.5)/segments)) for j in range(3))
        mesh.face((rings[1][i], rings[1][k], rings[2][k], rings[2][i]),
                  (46, 53, 62), radial)
    mesh.face(rings[2], (7, 11, 15), outward, False)


def _nozzles(mesh):
    # Visible bells, plume origins and applied forces use the same mounts.
    for index, (_, anchor, direction, _) in enumerate(NOZZLES):
        centre = (anchor[0], anchor[1]+GROUND_Y, anchor[2])
        outward = tuple(-v for v in direction)
        radius = .082 if index < 4 else .079 if index < 8 else .055
        _nozzle(mesh, centre, outward, radius, 8 if index < 4 else 6)


def _wheel(name, pivot):
    mesh = _Mesh(name, pivot)
    side = 1 if pivot[0] > 0 else -1
    n = 16
    rings = []
    for x, r in ((-.132, WHEEL_RADIUS-.045), (-.106, WHEEL_RADIUS),
                 (.106, WHEEL_RADIUS), (.132, WHEEL_RADIUS-.045)):
        rings.append([(x, r*math.cos(2*math.pi*i/n), r*math.sin(2*math.pi*i/n)) for i in range(n)])
    for band in range(3):
        for i in range(n):
            j = (i+1) % n
            angle = 2*math.pi*(i+.5)/n
            color = (29, 32, 37) if band == 1 else (37, 40, 44)
            mesh.face((rings[band][i], rings[band+1][i], rings[band+1][j], rings[band][j]),
                      color, (0, math.cos(angle), math.sin(angle)))
    for sign, ring in ((-1, rings[0]), (1, rings[3])):
        inner = [(sign*.134, .263*math.cos(2*math.pi*i/n), .263*math.sin(2*math.pi*i/n)) for i in range(n)]
        for i in range(n):
            j = (i+1) % n
            mesh.face((ring[i], ring[j], inner[j], inner[i]), (30, 33, 38), (sign, 0, 0))
    outer = side*.139
    lip = [(outer, .267*math.cos(2*math.pi*i/n), .267*math.sin(2*math.pi*i/n)) for i in range(n)]
    inner = [(outer+.002*side, .243*math.cos(2*math.pi*i/n), .243*math.sin(2*math.pi*i/n)) for i in range(n)]
    for i in range(n):
        j = (i+1) % n
        mesh.face((lip[i], lip[j], inner[j], inner[i]), _SILVER, (side, 0, 0))
    # Brake disc sits behind the open slender spokes, with a distinct caliper.
    disc = [(side*.109, .224*math.cos(2*math.pi*i/n), .224*math.sin(2*math.pi*i/n)) for i in range(n)]
    mesh.face(disc, (62, 68, 75), (side, 0, 0))
    _box(mesh, (side*.123, .035, -.179), (.024, .139, .051), (128, 49, 35))
    for spoke in range(5):
        angle = 2*math.pi*spoke/5 + .12
        for branch in (-1, 1):
            a = angle + branch*.09
            b = angle + branch*.18
            points = []
            for radius, theta in ((.073, a-.044), (.246, b-.031),
                                  (.246, b+.031), (.073, a+.044)):
                points.append((side*.147, radius*math.cos(theta), radius*math.sin(theta)))
            mesh.face(points, (177, 185, 193), (side, 0, 0))
    hub_front, hub_back = [], []
    for i in range(8):
        angle = 2*math.pi*i/8
        hub_front.append((side*.155, .074*math.cos(angle), .074*math.sin(angle)))
        hub_back.append((side*.138, .074*math.cos(angle), .074*math.sin(angle)))
    mesh.face(hub_front, (72, 80, 91), (side, 0, 0))
    for i in range(8):
        j = (i+1) % 8
        angle = 2*math.pi*(i+.5)/8
        mesh.face((hub_front[i], hub_front[j], hub_back[j], hub_back[i]),
                  (107, 118, 131), (0, math.cos(angle), math.sin(angle)))
    # Small cap mark is geometry, not a copied logo texture.
    _strip(mesh, (side*.157, .034, -.030), (side*.157, .034, .030),
           .008, (219, 223, 227), (side, 0, 0))
    _strip(mesh, (side*.157, .029, 0), (side*.157, -.031, 0),
           .008, (219, 223, 227), (side, 0, 0))
    return mesh


def build_model():
    """Return renderer-independent indexed parts and the measured mesh bounds."""
    body, glass = _Mesh('body'), _Mesh('glass')
    trim, detail, nozzles = _Mesh('trim'), _Mesh('details'), _Mesh('nozzles')
    _body(body, trim)
    _canopy(body, glass, detail)
    _lamps_and_trim(detail, trim)
    _nozzles(nozzles)
    meshes = [body, glass, trim, detail, nozzles]
    for name, pivot in zip(WHEEL_NAMES, WHEEL_CENTRES):
        meshes.append(_wheel(name, pivot))
    parts = [m.result() for m in meshes]
    low, high = [1e9]*3, [-1e9]*3
    for name, positions, indices, colors, pivot in parts:
        for i in range(0, len(positions), 3):
            for axis in range(3):
                v = positions[i+axis] + pivot[axis]
                low[axis] = min(low[axis], v)
                high[axis] = max(high[axis], v)
    return {'parts': parts, 'triangles': sum(len(m.indices)//3 for m in meshes),
            'vertices': sum(len(m.colors) for m in meshes),
            'length': high[2]-low[2], 'width': high[0]-low[0],
            'height': high[1]-low[1], 'wheel_radius': WHEEL_RADIUS}
