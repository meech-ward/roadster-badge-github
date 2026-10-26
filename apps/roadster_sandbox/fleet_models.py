"""Original, hand-authored low-poly Tesla fleet for the driving sandbox.

No mesh, texture, tracing, or exporter input is used by this module. The public
Tesla gallery and model photographs were visual references only. Dimensions are
rounded artistic envelopes in metres, including the modeled mirrors. Concept
vehicle dimensions are estimates, not factory engineering measurements.

Coordinates: +X left, +Y up, +Z front; ground is Y=0. Wheel positions are local
to their supplied pivots so callers can rotate them around X. Other pivots are
zero. Packed colours are R | G << 8 | B << 16. Only one model is built at a time.
"""
from math import cos, sin, pi, sqrt

REFERENCE_URLS = (
    "https://www.tesla.com/tesla-gallery",
    "https://www.tesla.com/models",
    "https://www.tesla.com/model3",
    "https://www.tesla.com/modelx",
    "https://www.tesla.com/modely",
    "https://www.tesla.com/cybertruck",
    "https://www.tesla.com/semi",
    "https://www.tesla.com/sites/default/files/downloads/tesla_roadster_emergency_responder_guide.pdf",
)

# Collision boxes: center X/Y/Z followed by half-width/height/length. The lower
# body and roof are separate so a low car can brush a high cabin's tapered end.
CATALOG = (
    {"id": "original_roadster", "name": "Original Roadster", "length": 3.95, "width": 1.90, "height": 1.13,
     "collision_boxes": ((0, .44, 0, .85, .28, 1.97), (0, .90, .27, .69, .23, .63))},
    {"id": "model_s", "name": "Model S", "length": 5.02, "width": 2.18, "height": 1.43,
     "collision_boxes": ((0, .53, 0, .98, .35, 2.51), (0, 1.12, -.24, .78, .31, 1.23))},
    {"id": "model_3", "name": "Model 3", "length": 4.72, "width": 2.08, "height": 1.44,
     "collision_boxes": ((0, .51, 0, .93, .34, 2.36), (0, 1.14, -.13, .75, .30, 1.11))},
    {"id": "model_x", "name": "Model X", "length": 5.05, "width": 2.27, "height": 1.68,
     "collision_boxes": ((0, .61, 0, 1.00, .41, 2.52), (0, 1.35, -.24, .80, .33, 1.34))},
    {"id": "model_y", "name": "Model Y", "length": 4.79, "width": 2.12, "height": 1.62,
     "collision_boxes": ((0, .58, 0, .96, .39, 2.395), (0, 1.29, -.16, .77, .33, 1.18))},
    {"id": "model_y_l", "name": "Model Y L", "length": 4.97, "width": 2.12, "height": 1.67,
     "collision_boxes": ((0, .59, 0, .96, .40, 2.485), (0, 1.33, -.26, .78, .34, 1.31))},
    {"id": "cybertruck", "name": "Cybertruck", "length": 5.68, "width": 2.41, "height": 1.79,
     "collision_boxes": ((0, .72, 0, 1.10, .43, 2.84), (0, 1.40, .24, .96, .39, 1.42))},
    {"id": "semi", "name": "Semi", "length": 7.20, "width": 2.60, "height": 3.72,
     "collision_boxes": ((0, .62, 0, 1.18, .40, 3.60), (0, 2.29, 1.73, 1.17, 1.43, 1.83))},
    {"id": "cybercab", "name": "Cybercab", "length": 4.45, "width": 1.95, "height": 1.48,
     "collision_boxes": ((0, .51, 0, .97, .34, 2.225), (0, 1.15, -.14, .77, .33, 1.12))},
    {"id": "robovan", "name": "Robovan", "length": 6.80, "width": 2.65, "height": 2.85,
     "collision_boxes": ((0, 1.34, 0, 1.31, 1.23, 3.28), (0, 2.65, 0, 1.05, .20, 2.71))},
)


def _rgb(r, g, b):
    return r | (g << 8) | (b << 16)


INK = _rgb(14, 20, 25)
RUBBER = _rgb(25, 29, 34)
TREAD = _rgb(36, 40, 44)
GLASS = _rgb(29, 53, 66)
GLINT = _rgb(70, 104, 119)
SILVER = _rgb(176, 186, 190)
LAMP = _rgb(226, 248, 255)
RED = _rgb(240, 34, 37)
AMBER = _rgb(243, 164, 37)


def _shade(color, amount):
    return _rgb(max(0, min(255, (color & 255) + amount)),
                max(0, min(255, ((color >> 8) & 255) + amount)),
                max(0, min(255, ((color >> 16) & 255) + amount)))


class _Part:
    """Small indexed mesh builder; repeated coordinates share coloured vertices."""
    def __init__(self, name, pivot=(0., 0., 0.)):
        self.name = name
        self.pivot = pivot
        self.positions = []
        self.indices = []
        self.colors = []
        self.lookup = {}
        self.orient_details = name not in ("body", "steel_body", "gold_body",
                                           "tractor_cab", "streamlined_shell")

    def poly(self, points, color):
        # Mirroring a decorative panel also reverses its winding. The native
        # renderer culls back faces, so orient details away from the car center.
        # Wheels use their own local center; body lofts already wind outward.
        if self.orient_details and len(points) >= 3:
            a,b,c=points[0],points[1],points[2]
            u=(b[0]-a[0],b[1]-a[1],b[2]-a[2])
            v=(c[0]-a[0],c[1]-a[1],c[2]-a[2])
            normal=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            cy=0. if self.name.startswith("wheel_") else .55
            center=(sum(p[0] for p in points)/len(points),
                    sum(p[1] for p in points)/len(points)-cy,
                    sum(p[2] for p in points)/len(points))
            if sum(normal[i]*center[i] for i in range(3)) < 0:
                points=tuple(reversed(points))
        ids = []
        for point in points:
            key = (round(point[0], 5), round(point[1], 5), round(point[2], 5), color)
            index = self.lookup.get(key)
            if index is None:
                index = len(self.colors)
                self.lookup[key] = index
                self.positions.extend(key[:3])
                self.colors.append(color)
            ids.append(index)
        for i in range(1, len(ids) - 1):
            self.indices.extend((ids[0], ids[i], ids[i + 1]))

    def finish(self):
        return (self.name, self.positions, self.indices, self.colors, self.pivot)


def _quad(part, a, b, c, d, color):
    part.poly((a, b, c, d), color)


def _box(part, x0, x1, y0, y1, z0, z1, color):
    _quad(part, (x0,y0,z1), (x1,y0,z1), (x1,y1,z1), (x0,y1,z1), color)
    _quad(part, (x1,y0,z0), (x0,y0,z0), (x0,y1,z0), (x1,y1,z0), _shade(color,-14))
    _quad(part, (x1,y0,z1), (x1,y0,z0), (x1,y1,z0), (x1,y1,z1), _shade(color,-8))
    _quad(part, (x0,y0,z0), (x0,y0,z1), (x0,y1,z1), (x0,y1,z0), _shade(color,-8))
    _quad(part, (x0,y1,z1), (x1,y1,z1), (x1,y1,z0), (x0,y1,z0), _shade(color,12))
    _quad(part, (x0,y0,z0), (x1,y0,z0), (x1,y0,z1), (x0,y0,z1), _shade(color,-22))


def _loft(part, rings, color, caps=True):
    n = len(rings[0])
    for i in range(len(rings) - 1):
        for j in range(n):
            shade = (0, 10, 17, 17, 10, 0, -12, -20, -20, -12)[j % 10]
            _quad(part, rings[i][j], rings[i][(j+1)%n],
                  rings[i+1][(j+1)%n], rings[i+1][j], _shade(color, shade))
    if caps:
        part.poly(tuple(reversed(rings[0])), _shade(color, -7))
        part.poly(rings[-1], _shade(color, 4))


def _body_ring(z, width, top, low, high):
    # A soft shoulder and the crown are independent of the wheel-opening floor.
    shoulder = max(low + .035, top - .11)
    if high:
        return ((width*.87,low,z), (width,low+.028,z), (width,shoulder,z),
                (width*.78,top-.015,z), (0,top,z),
                (-width*.78,top-.015,z), (-width,shoulder,z),
                (-width,low+.028,z), (-width*.87,low,z), (0,low,z))
    return ((width*.9,low,z), (width,shoulder,z), (0,top,z),
            (-width,shoulder,z), (-width*.9,low,z), (0,low,z))


def _sample(profile, z):
    for i in range(len(profile)-1):
        a, b = profile[i], profile[i+1]
        if a[0] <= z <= b[0]:
            t = (z-a[0])/(b[0]-a[0])
            return (a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t)
    return profile[-1][1:]


def _body(part, profile, axles, radius, color, high, floor=.18):
    zs = [p[0] for p in profile]
    if high:
        for axle in axles:
            for t in (-1.08, -.81, -.42, 0., .42, .81, 1.08):
                z = axle + radius*t
                if profile[0][0] < z < profile[-1][0]:
                    zs.append(z)
        zs = sorted(set(zs))
    rings = []
    for z in zs:
        width, top = _sample(profile,z)
        low = floor
        if high:
            for axle in axles:
                d = abs(z-axle)
                if d < radius*1.08:
                    low = max(low, radius + sqrt(max(0., (radius*1.08)**2-d*d)))
        rings.append(_body_ring(z,width,top,min(top-.08,low),high))
    _loft(part,rings,color)


def _canopy(part, profile, color=GLASS, high=True):
    # A closed tapered greenhouse. Each row is z, lower width, roof width,
    # beltline height and crown height. Subdivided across its roof, never a box.
    rings = []
    for z, wb, wt, yb, yt in profile:
        if high:
            rings.append(((wb,yb,z),(wt,yt-.035,z),(0,yt,z),
                          (-wt,yt-.035,z),(-wb,yb,z),(0,yb,z)))
        else:
            rings.append(((wb,yb,z),(wt,yt,z),(-wt,yt,z),(-wb,yb,z)))
    _loft(part,rings,color)


def _line(part, points, color=INK, width=.013, plane="side"):
    width=max(width,.030)
    for a,b in zip(points,points[1:]):
        if plane == "side":
            dy,dz=b[1]-a[1],b[2]-a[2]
            length=sqrt(dy*dy+dz*dz)
            if length < .0001:
                continue
            oy,oz=-dz/length*width*.5,dy/length*width*.5
            _quad(part,(a[0],a[1]+oy,a[2]+oz),(a[0],a[1]-oy,a[2]-oz),
                  (b[0],b[1]-oy,b[2]-oz),(b[0],b[1]+oy,b[2]+oz),color)
        else:
            dx,dz=b[0]-a[0],b[2]-a[2]
            length=sqrt(dx*dx+dz*dz)
            if length < .0001:
                continue
            ox,oz=-dz/length*width*.5,dx/length*width*.5
            _quad(part,(a[0]+ox,a[1],a[2]+oz),(a[0]-ox,a[1],a[2]-oz),
                  (b[0]-ox,b[1],b[2]-oz),(b[0]+ox,b[1],b[2]+oz),color)



def _panel_line(part, profile, sign, points, color=INK, width=.030):
    fitted=[]
    for y,z in points:
        w,top=_sample(profile,z)
        y=min(y,top-.022)
        if y <= top-.11:
            x=w
        else:
            x=w*(1.-.22*(y-(top-.11))/.095)
        fitted.append((sign*(x+.023),y,z))
    _line(part,fitted,color,width)


def _hood_line(part, profile, points, color):
    fitted=[]
    for x,z in points:
        w,top=_sample(profile,z)
        y=top-.015*min(1.,abs(x)/(w*.78))+.020
        fitted.append((x,y,z))
    _line(part,fitted,color,.030,"top")


def _wheel(name, x, z, radius, width, high, hub=SILVER, aero=False):
    part = _Part(name,(x,radius,z))
    sign = 1 if x > 0 else -1
    n = 12 if high else 6
    inner,outer=-sign*width*.5,sign*width*.5
    def circle(at, r):
        return tuple((at, sin(i*2*pi/n-pi/2)*r, cos(i*2*pi/n-pi/2)*r) for i in range(n))
    a,b=circle(inner,radius),circle(outer,radius)
    for i in range(n):
        j=(i+1)%n
        _quad(part,a[i],a[j],b[j],b[i],TREAD if i%2 else RUBBER)
    if not high:
        part.poly(b if sign<0 else tuple(reversed(b)),hub if aero else RUBBER)
        return part.finish()
    c=circle(outer+sign*.012,radius*.78)
    d=circle(outer+sign*.018,radius*.60)
    for i in range(n):
        j=(i+1)%n
        _quad(part,b[i],b[j],c[j],c[i],RUBBER)
        _quad(part,c[i],c[j],d[j],d[i],hub if aero else _shade(hub,-35))
    part.poly(d,hub if aero else INK)
    # Five broad spokes are actual planar facets on each visible wheel face.
    if not aero:
        for i in range(5):
            angle=i*2*pi/5
            at=outer+sign*.023
            points=((at,sin(angle-.14)*radius*.18,cos(angle-.14)*radius*.18),
                    (at,sin(angle+.14)*radius*.18,cos(angle+.14)*radius*.18),
                    (at,sin(angle+.09)*radius*.67,cos(angle+.09)*radius*.67),
                    (at,sin(angle-.09)*radius*.67,cos(angle-.09)*radius*.67))
            part.poly(points,hub)
    else:
        # Radial gold/chrome wedges keep the full-disc hubs from looking flat.
        for i in range(0,n,2):
            j=(i+1)%n
            part.poly(((outer+sign*.025,0,0),d[i],d[j]),_shade(hub,16))
    return part.finish()


def _wheels(parts, axles, track, radius, high, width=.22, hub=SILVER, aero=False):
    for i,z in enumerate(axles):
        for sign in (-1,1):
            parts.append(_wheel("wheel_%s_%s" % (i,"left" if sign>0 else "right"),
                                sign*track,z,radius,width,high,hub,aero))


def _lamp_pair(part, width, y, z, rear=False, slim=False):
    color=RED if rear else LAMP
    h=.035 if slim else .075
    direction=-1 if rear else 1
    for sign in (-1,1):
        # Swept outer tip, with an inward taper visible from three-quarter views.
        part.poly(((sign*width,y,z), (sign*width*.61,y-.012,z+direction*.025),
                   (sign*width*.58,y+h*.55,z+direction*.012),
                   (sign*width*.89,y+h,z-direction*.07)),color)


def _light_bar(part, half, y, z, color=LAMP, thickness=.026):
    _quad(part,(-half,y,z),(half,y,z),(half,y+thickness,z),(-half,y+thickness,z),color)


def _mirror(part, sign, body_w, outer, y, z, color):
    _box(part,sign*(body_w-.03) if sign>0 else -outer,
         outer if sign>0 else -body_w+.03,y,y+.075,z-.10,z+.10,color)
    _quad(part,(sign*outer,y+.012,z-.105),(sign*(body_w+.025),y+.012,z-.105),
          (sign*(body_w+.025),y+.059,z-.105),(sign*outer,y+.059,z-.105),GLINT)


# Each shape uses independently chosen longitudinal stations and cabin outline.
# Sharing construction tools does not turn these into scaled generic car meshes.
_SEDANS = {
    "model_s": (_rgb(176,27,40), .98, .35, (-1.47,1.49),
       ((-2.51,.76,.75),(-2.27,.94,.88),(-1.49,.98,.91),(-.75,.94,.89),(.45,.93,.88),(1.49,.98,.84),(2.16,.88,.69),(2.51,.70,.61)),
       ((-1.72,.79,.28,.87,.97),(-.93,.87,.66,.90,1.33),(-.36,.88,.70,.91,1.43),(.45,.85,.67,.89,1.38),(1.25,.76,.61,.84,.95)),1.09),
    "model_3": (_rgb(226,231,233), .925, .335, (-1.40,1.41),
       ((-2.36,.70,.70),(-2.13,.88,.84),(-1.40,.925,.88),(-.61,.91,.88),(.54,.89,.85),(1.41,.925,.80),(2.08,.81,.66),(2.36,.66,.58)),
       ((-1.54,.76,.35,.84,.95),(-.74,.83,.66,.89,1.36),(-.12,.83,.68,.88,1.44),(.52,.81,.64,.86,1.34),(1.16,.73,.56,.82,.95)),1.04),
    "model_x": (_rgb(93,142,173),1.,.385,(-1.51,1.49),
       ((-2.525,.77,.87),(-2.28,.94,1.00),(-1.51,1.,1.03),(-.70,.97,1.02),(.54,.96,1.00),(1.49,1.,.94),(2.20,.87,.80),(2.525,.70,.70)),
       ((-1.91,.81,.31,.97,1.08),(-1.10,.88,.70,1.01,1.53),(-.40,.90,.74,1.03,1.68),(.38,.88,.71,1.01,1.62),(1.31,.78,.60,.94,1.06)),1.135),
    "model_y": (_rgb(51,91,160),.96,.365,(-1.43,1.46),
       ((-2.395,.77,.85),(-2.12,.93,.97),(-1.43,.96,1.01),(-.66,.94,.98),(.53,.92,.96),(1.46,.96,.91),(2.09,.85,.77),(2.395,.71,.67)),
       ((-1.75,.79,.30,.96,1.08),(-.92,.85,.65,1.,1.49),(-.26,.86,.71,1.,1.62),(.44,.83,.67,.98,1.53),(1.24,.75,.56,.91,1.04)),1.06),
    "model_y_l": (_rgb(207,198,182),.96,.365,(-1.56,1.49),
       ((-2.485,.78,.88),(-2.22,.93,1.00),(-1.56,.96,1.03),(-.73,.94,1.00),(.54,.92,.98),(1.49,.96,.92),(2.18,.85,.78),(2.485,.71,.68)),
       ((-1.98,.81,.36,.99,1.14),(-1.13,.87,.67,1.02,1.56),(-.37,.88,.72,1.02,1.67),(.44,.84,.68,1.,1.58),(1.27,.76,.58,.93,1.05)),1.06),
}


def _sedan(slug, high):
    color,w,r,axles,profile,cabin,mirror=_SEDANS[slug]
    body,glass,trim=_Part("body"),_Part("glass"),_Part("lights_and_trim")
    _body(body,profile,axles,r,color,high)
    _canopy(glass,cabin,high=high)
    last=profile[-1][0]
    first=profile[0][0]
    if slug in ("model_y","model_y_l"):
        _light_bar(trim,.74,profile[-1][2]-.02,last+.002)
        _light_bar(trim,.79,profile[0][2]-.03,first-.002,RED,.045)
    else:
        _lamp_pair(trim,w*.90,profile[-1][2]-.04,last-.045,slim=slug=="model_3")
        _lamp_pair(trim,w*.91,profile[0][2]-.06,first+.045,True)
    if high:
        for sign in (-1,1):
            # Thin bright roof rails frame the recessed glass; their changing
            # width follows the individual car's fastback profile.
            rail=[(sign*(row[2]+.026),row[4]-.007,row[0]) for row in cabin]
            _line(trim,rail,_shade(color,25),.033,"top")
            belt=[(sign*(row[1]+.030),row[3]+.021,row[0]) for row in cabin]
            _line(trim,belt,INK,.025)
            # Door cuts are deliberately model-specific relative to the axles.
            back=-1.02 if slug in ("model_x","model_y_l") else -.84
            front=.94 if slug=="model_s" else .99
            side=sign*(w+.003)
            _panel_line(trim,profile,sign,((.83,front),(.43,front-.12),(.31,front-.33),
                        (.31,back+.10),(.48,back)))
            _panel_line(trim,profile,sign,((.87,-.04),(.31,-.10)))
            for z in (.70,-.64):
                _panel_line(trim,profile,sign,((.83,z),(.83,z-.18)),SILVER if slug=="model_s" else INK,.040)
            # Lower rocker, side camera, mirrors and small rear reflector.
            _panel_line(trim,profile,sign,((.26,axles[0]+.40),(.26,axles[1]-.40)),INK,.055)
            _mirror(trim,sign,w-.03,mirror,1.01 if slug=="model_x" else .91,.92,color)
            _panel_line(trim,profile,sign,((.79,1.13),(.82,1.02)),INK,.040)
        # Hood creases curve inward toward the nose, rather than a rectangular panel.
        for sign in (-1,1):
            _hood_line(trim,profile,((sign*.58,1.27),(sign*.52,1.61),(sign*.41,2.13)),_shade(color,-22))
        _light_bar(trim,.50,.34,last-.006,INK,.075)
        _light_bar(trim,.53,.32,first-.005,INK,.090)
        # Rear deck lip; Model X and crossovers have a longer hatch spoiler.
        lip=.90 if slug in ("model_x","model_y","model_y_l") else .76
        _box(trim,-lip,lip,profile[0][2]+.045,profile[0][2]+.075,first+.07,first+.17,INK)
        if slug=="model_x":
            # Falcon-wing roof hinge cuts and the taller rear door's shoulder.
            for sign in (-1,1):
                _line(trim,((sign*.19,1.683,-.34),(sign*.53,1.66,-.34),
                            (sign*.75,1.55,-.34)),INK,.016,"top")
                _line(trim,((sign*.91,1.01,-.32),(sign*.77,1.55,-.34)),INK,.018)
                _line(trim,((sign*.86,1.02,-1.17),(sign*.70,1.49,-1.12)),INK,.017)
        elif slug=="model_y_l":
            # Extended third-row quarter window and longer D-pillar.
            for sign in (-1,1):
                _line(trim,((sign*.84,1.06,-1.40),(sign*.64,1.40,-1.39)),INK,.045)
        else:
            for sign in (-1,1):
                _line(trim,((sign*.84,.94,-.03),(sign*.69,cabin[2][4]-.06,-.03)),INK,.043)
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,axles,w-.055,r,high,hub=_rgb(124,136,145))
    return parts,r


def _original_roadster(high):
    color=_rgb(220,84,23)
    body,glass,trim=_Part("body"),_Part("windscreen"),_Part("cockpit_and_trim")
    axles=(-1.22,1.13)
    profile=((-1.975,.65,.61),(-1.69,.80,.75),(-1.22,.86,.80),(-.70,.78,.67),
             (.38,.77,.65),(1.13,.84,.77),(1.68,.74,.58),(1.975,.54,.47))
    _body(body,profile,axles,.30,color,high,.14)
    # Short, upright windscreen and open cockpit, with a visible dark tub.
    _canopy(glass,((.30,.69,.62,.66,1.13),(.78,.67,.60,.63,.76)),high=high)
    _quad(trim,(-.61,.686,-.72),(.61,.686,-.72),(.61,.686,.32),(-.61,.686,.32),INK)
    _lamp_pair(trim,.71,.58,1.76)
    if high:
        for sign in (-1,1):
            # Rounded raised pods carry elongated early Roadster headlamps.
            partpts=((sign*.48,.63,1.85),(sign*.73,.66,1.66),
                     (sign*.70,.73,1.43),(sign*.52,.72,1.55))
            trim.poly(partpts,LAMP)
            # Distinctive rear circular lamps, two on each side.
            for x in (.47,.67):
                trim.poly(tuple((sign*x+cos(k*pi/4)*.065,.64+sin(k*pi/4)*.065,-1.979)
                                for k in range(8)),RED)
            _box(trim,sign*.31-.18,sign*.31+.18,.68,.98,-.62,-.42,INK)
            _box(trim,sign*.31-.16,sign*.31+.16,.70,.76,-.43,-.02,_rgb(59,56,52))
            _line(trim,((sign*.70,.69,-.72),(sign*.70,.73,-.43),(sign*.69,.70,.30)),color,.065)
            _line(trim,((sign*.61,1.13,.30),(sign*.67,.72,.75)),INK,.042)
            _line(trim,((sign*.81,.65,.30),(sign*.81,.29,.11),
                        (sign*.81,.25,-.63),(sign*.81,.61,-.83)),INK,.012)
            # Black scalloped intake immediately ahead of each rear wheel.
            trim.poly(((sign*.817,.35,-.66),(sign*.85,.42,-.94),
                       (sign*.85,.62,-.93),(sign*.80,.57,-.66)),INK)
            _mirror(trim,sign,.76,.95,.78,.44,color)
        _line(trim,((-.61,1.112,.30),(.61,1.112,.30)),INK,.035,"top")
        _light_bar(trim,.41,.29,1.977,INK,.085)
        _light_bar(trim,.58,.30,-1.979,INK,.095)
        for z in (-1.63,-1.52,-1.41):
            _line(trim,((-.46,.79,z),(.46,.79,z)),INK,.025,"top")
    else:
        _light_bar(trim,.62,.64,-1.979,RED,.042)
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,axles,.80,.30,high,width=.18,hub=_rgb(192,198,202))
    return parts,.30


def _cybertruck(high):
    body,glass,trim=_Part("steel_body"),_Part("angular_glass"),_Part("bed_and_trim")
    steel=_rgb(159,170,175)
    axles=(-1.72,1.75)
    # Straight facets form the truck's continuous nose/roof/tonneau wedge.
    profile=((-2.84,1.00,1.04),(-2.54,1.10,1.13),(-1.72,1.10,1.19),
             (-.52,1.09,1.33),(.54,1.07,1.36),(1.75,1.09,1.16),(2.84,.96,.98))
    _body(body,profile,axles,.43,steel,high,.32)
    _canopy(glass,((-.74,.94,.72,1.31,1.48),(.22,.99,.84,1.35,1.79),
                  (1.95,.94,.81,1.09,1.19)),high=high)
    _light_bar(trim,.95,1.00,2.842,LAMP,.036)
    _light_bar(trim,1.00,1.02,-2.842,RED,.034)
    # The bed is open at the back; a black sloped tonneau distinguishes it from
    # the windshield in both views. Rail strips flank it in brushed steel.
    _quad(trim,(-.90,1.10,-2.73),(.90,1.10,-2.73),(.90,1.39,-.78),(-.90,1.39,-.78),INK)
    if high:
        for sign in (-1,1):
            _line(trim,((sign*.98,1.10,-2.74),(sign*.98,1.39,-.78)),_shade(steel,24),.09)
            # Squared black wheel-arch cladding is explicitly polygonal.
            for z in axles:
                outline=((sign*1.112,.34,z-.59),(sign*1.112,.72,z-.59),
                         (sign*1.112,1.00,z-.33),(sign*1.112,1.00,z+.33),
                         (sign*1.112,.72,z+.59),(sign*1.112,.34,z+.59))
                _line(trim,outline,INK,.135)
            _line(trim,((sign*1.103,1.18,1.04),(sign*1.103,.39,.93),
                        (sign*1.103,.38,-1.03),(sign*1.103,1.24,-1.16)),INK,.014)
            _line(trim,((sign*1.103,1.29,-.10),(sign*1.103,.39,-.10)),INK,.014)
            _line(trim,((sign*.86,1.77,.22),(sign*.99,1.36,.20)),INK,.03)
            _line(trim,((sign*1.104,.32,-1.13),(sign*1.104,.32,1.17)),INK,.13)
            _mirror(trim,sign,1.04,1.205,1.32,1.15,INK)
        for z in (-2.48,-2.21,-1.94,-1.67,-1.40,-1.13):
            y=1.10+(z+2.73)*.29/1.95+.008
            _line(trim,((-.88,y,z),(.88,y,z)),_rgb(51,59,65),.022,"top")
        _light_bar(trim,.99,.43,2.844,INK,.17)
        _light_bar(trim,.93,.43,-2.844,INK,.18)
        for sign in (-1,1):
            _light_bar(trim,.12,.55,2.848,LAMP,.035)
            _box(trim,sign*.90-.08,sign*.90+.08,.40,.47,-2.84,-2.80,RED)
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,axles,1.035,.43,high,width=.27,hub=_rgb(58,64,68),aero=True)
    return parts,.43


def _cybercab(high):
    gold=_rgb(187,156,91)
    body,glass,trim=_Part("gold_body"),_Part("black_canopy"),_Part("lamps_and_doors")
    axles=(-1.34,1.33)
    profile=((-2.225,.70,.70),(-1.99,.88,.85),(-1.34,.975,.91),(-.55,.92,.88),
             (.59,.91,.87),(1.33,.965,.79),(1.97,.84,.62),(2.225,.70,.55))
    _body(body,profile,axles,.355,gold,high,.18)
    _canopy(glass,((-1.63,.76,.38,.87,1.06),(-.76,.82,.64,.92,1.40),
                  (-.13,.82,.69,.91,1.48),(.55,.79,.65,.88,1.37),(1.18,.71,.55,.81,.96)),high=high)
    _light_bar(trim,.80,.60,2.226,LAMP,.029)
    _light_bar(trim,.81,.73,-2.226,RED,.029)
    if high:
        for sign in (-1,1):
            # One large butterfly door per side: no rear-door cut or handle.
            side=sign*.966
            _panel_line(trim,profile,sign,((.81,1.00),(.37,.79),(.29,-.70),
                        (.52,-.98),(.90,-1.02)),_shade(gold,-55),.030)
            _line(trim,((sign*.80,.93,-1.06),(sign*.65,1.41,-.76)),INK,.045)
            _line(trim,((sign*.77,.91,.62),(sign*.65,1.36,.54)),INK,.03)
            _panel_line(trim,profile,sign,((.26,-.95),(.26,1.02)),INK,.055)
            _line(trim,((sign*.74,.92,-1.46),(sign*.64,1.40,-.76),
                        (sign*.69,1.48,-.13),(sign*.65,1.37,.55)),_shade(gold,20),.025,"top")
            # Rearward shoulder facet is metallic; glass remains a teardrop.
            trim.poly(((sign*.79,.94,-1.49),(sign*.88,.92,-1.11),
                       (sign*.68,1.30,-.98)),_shade(gold,25))
        _light_bar(trim,.62,.32,2.228,INK,.070)
        _light_bar(trim,.65,.31,-2.228,INK,.080)
        for sign in (-1,1):
            _hood_line(trim,profile,((sign*.55,1.21),(sign*.44,1.83)),_shade(gold,-25))
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,axles,.84,.355,high,hub=_rgb(194,164,102),aero=True)
    return parts,.355


def _semi(high):
    body,glass,trim=_Part("tractor_cab"),_Part("wraparound_glass"),_Part("chassis_and_details")
    white=_rgb(215,221,223)
    # Only the tractor is modeled: tall aerodynamic center-seat cab, three axles,
    # exposed rear fifth wheel and chassis, without a misleading cargo trailer.
    cab_profile=((-0.35,1.13,3.62),(.13,1.18,3.72),(1.37,1.17,3.68),
                 (2.35,1.08,3.32),(3.10,.94,2.70),(3.60,.79,1.47))
    rings=[]
    for z,w,top in cab_profile:
        if high:
            rings.append(((w*.85,.46,z),(w,.76,z),(w,top-.34,z),(w*.76,top-.05,z),
                          (0,top,z),(-w*.76,top-.05,z),(-w,top-.34,z),(-w,.76,z),
                          (-w*.85,.46,z),(0,.46,z)))
        else:
            rings.append(((w*.88,.46,z),(w,top-.25,z),(0,top,z),(-w,top-.25,z),(-w*.88,.46,z),(0,.46,z)))
    _loft(body,rings,white)
    _box(trim,-1.02,1.02,.44,.70,-3.60,-.25,INK)
    # The windshield follows the actual sloping cowl facets; a floating flat
    # rectangle would disappear into the nose when seen from the front.
    rows=((2.35,.822,3.282,3.332),(3.10,.714,2.662,2.712),
          (3.20,.691,2.416,2.466))
    for a,b in zip(rows,rows[1:]):
        for sign in (-1,1):
            _quad(glass,(0,a[3],a[0]),(sign*a[1],a[2],a[0]),
                  (sign*b[1],b[2],b[0]),(0,b[3],b[0]),GLASS)
    for sign in (-1,1):
        _quad(glass,(sign*1.208,2.04,1.05),(sign*1.208,3.13,1.05),
              (sign*1.108,2.98,2.35),(sign*1.108,2.04,2.35),GLASS)
        _quad(glass,(sign*1.108,2.04,2.35),(sign*1.108,2.98,2.35),
              (sign*.969,2.36,3.10),(sign*.969,2.04,3.10),GLASS)
    _light_bar(trim,.60,1.02,3.602,LAMP,.042)
    _light_bar(trim,.65,.75,-3.602,RED,.05)
    if high:
        # Large lower intake, cowl edge, center wiper and daytime-light uprights.
        _light_bar(trim,.61,.64,3.604,INK,.18)
        _line(trim,((-.73,2.15,2.98),(.73,2.15,2.98)),INK,.045,"top")
        _line(trim,((0.,2.25,2.95),(.38,2.51,2.78)),INK,.03,"top")
        for sign in (-1,1):
            _line(trim,((sign*.70,.91,3.53),(sign*.81,1.44,3.31)),LAMP,.063)
            _line(trim,((sign*1.187,.81,.28),(sign*1.187,2.19,.34),
                        (sign*1.17,2.19,1.00),(sign*1.17,.82,1.10)),INK,.025)
            _line(trim,((sign*1.19,1.66,.66),(sign*1.19,1.81,.66)),SILVER,.055)
            _box(trim,sign*1.22-.08,sign*1.22+.08,2.16,2.55,1.95,2.17,INK)
            _line(trim,((sign*1.12,2.40,1.71),(sign*1.26,2.40,2.03)),INK,.055,"top")
            # A pair of entry steps below each door and rear mudguards.
            for y in (.65,.88):
                _box(trim,sign*1.10-.08,sign*1.10+.08,y,y+.055,.35,.96,_rgb(105,118,126))
            _box(trim,sign*.92-.20,sign*.92+.20,.93,1.05,-3.30,-1.09,white)
            for z in (-3.20,-1.27):
                _box(trim,sign*.94-.18,sign*.94+.18,.30,.75,z-.025,z+.025,INK)
        # Fifth-wheel turntable and frame rails are explicit geometry.
        trim.poly(tuple((cos(i*pi/6)*.58,.79,-1.66+sin(i*pi/6)*.56) for i in range(12)),_rgb(91,101,107))
        for x in (-.63,.63):
            _box(trim,x-.08,x+.08,.71,.78,-3.45,-.37,_rgb(75,84,91))
        _box(trim,-.57,.57,.82,1.08,-.48,-.29,_rgb(90,101,110))
        for y in (1.20,1.39,1.58):
            _light_bar(trim,.91,y,-.353,_rgb(158,170,177),.025)
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,(-2.74,-1.47,2.43),1.025,.49,high,width=.30,hub=_rgb(160,173,182),aero=True)
    return parts,.49


def _robovan(high):
    body,glass,trim=_Part("streamlined_shell"),_Part("tall_window_band"),_Part("light_ribbons_and_doors")
    silver=_rgb(185,188,181)
    # Streamliner outline with rounded front and rear, nearly vertical flanks,
    # concealed wheels and a broad high shoulder: no conventional car hood.
    profile=((-3.40,.72,2.05),(-3.20,1.11,2.47),(-2.63,1.29,2.77),
             (-1.35,1.325,2.85),(1.35,1.325,2.85),(2.63,1.29,2.77),
             (3.20,1.11,2.47),(3.40,.72,2.05))
    rings=[]
    for z,w,h in profile:
        rings.append(((w*.89,.13,z),(w,.43,z),(w,h-.38,z),(w*.73,h-.03,z),
                      (0,h,z),(-w*.73,h-.03,z),(-w,h-.38,z),(-w,.43,z),
                      (-w*.89,.13,z),(0,.13,z)))
    if not high:
        rings=[tuple(r[j] for j in (0,1,2,4,6,7,8)) for r in rings]
    _loft(body,rings,silver)
    # End windows are tall and centered, following the rounded end taper.
    for sign in (-1,1):
        z=sign*3.420
        _quad(glass,(-.47,.78,z),(.47,.78,z),(.45,1.98,z),(-.45,1.98,z),GLASS)
    if high:
        for sign in (-1,1):
            # Tall separated side panes are the bus's defining visual feature.
            x=sign*1.350
            for z0,z1 in ((-2.22,-1.35),(-1.20,-.35),(.35,1.20),(1.35,2.22)):
                glass.poly(((x,.82,z0),(x,.82,z1),(sign*1.350,2.45,z1-.04),
                            (sign*1.350,2.45,z0+.04)),GLASS)
            _line(trim,((x,.30,-.30),(x,2.52,-.30),(x,2.52,.30),(x,.30,.30)),INK,.019)
            _line(trim,((x,.35,0),(x,2.51,0)),INK,.016)
            for y in (1.00,1.13,1.26,1.39):
                _line(trim,((sign*.73,y,-3.403),(sign*1.118,y,-3.202),
                            (sign*1.350,y,-2.61),(sign*1.350,y,2.61),
                            (sign*1.118,y,3.202),(sign*.73,y,3.403)),_rgb(234,215,158),.027)
            _line(trim,((sign*1.22,.22,-2.64),(sign*1.33,.22,2.64)),INK,.065)
        for y in (1.00,1.13,1.26,1.39):
            _light_bar(trim,.73,y,3.409,_rgb(249,238,198),.027)
            _light_bar(trim,.73,y,-3.409,RED,.027)
        # Small visible slivers of tires sit within the lower skirt.
    else:
        for sign in (-1,1):
            _quad(glass,(sign*1.350,.85,-2.20),(sign*1.350,.85,2.20),
                  (sign*1.350,2.42,2.12),(sign*1.350,2.42,-2.12),GLASS)
        _light_bar(trim,.73,1.13,3.409,LAMP,.08)
        _light_bar(trim,.73,1.13,-3.409,RED,.08)
    parts=[body.finish(),glass.finish(),trim.finish()]
    _wheels(parts,(-2.12,2.12),1.05,.25,high,width=.18,hub=_rgb(77,82,85),aero=True)
    return parts,.25


def build_model(slug, detail="high"):
    """Build one original mesh; low detail preserves the same overall silhouette."""
    info=None
    for entry in CATALOG:
        if entry["id"] == slug:
            info=entry
            break
    if info is None:
        raise ValueError("Unknown fleet model: " + str(slug))
    if detail not in ("high","low"):
        raise ValueError("detail must be high or low")
    high=detail=="high"
    if slug in _SEDANS:
        parts,radius=_sedan(slug,high)
    elif slug=="original_roadster":
        parts,radius=_original_roadster(high)
    elif slug=="cybertruck":
        parts,radius=_cybertruck(high)
    elif slug=="cybercab":
        parts,radius=_cybercab(high)
    elif slug=="semi":
        parts,radius=_semi(high)
    else:
        parts,radius=_robovan(high)
    return {"parts":parts, "triangles":sum(len(p[2])//3 for p in parts),
            "vertices":sum(len(p[1])//3 for p in parts),
            "length":info["length"],"width":info["width"],"height":info["height"],
            "wheel_radius":radius,"collision_boxes":info["collision_boxes"],"detail":detail}
