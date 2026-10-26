"""Roadster Badge's own vehicle design, in metres, kilograms and seconds.

These are game design choices, not measurements of a Tesla or its proposed
thruster package. The visible handmade body supplies the envelope; a simple
mass distribution, suspension target and acceleration targets supply the
mechanics. All derived values are computed once when the app loads.

Body axes are +X left, +Y up, +Z forward. The body origin is its centre of mass.
The tank is an isothermal ideal-gas reservoir with a pressure regulator. The
fixed exhaust speed represents a rated nozzle; below regulated pressure its
flow and thrust fall together. It conserves propellant and delivered impulse,
without attempting a heat-transfer or detailed compressible-flow simulation.
"""
import math

GRAVITY = 9.80665
AMBIENT_PRESSURE = 101325.0

# Geometry chosen for the handmade model: 4.60 m nose-to-tail and 2.09 m body.
# Wheelbase occupies 61% of the body length; the tread centres use 82% of its
# width. Radius is 7.4% of length, leaving visible arch clearance on the coupe.
BODY_LENGTH = 4.60
BODY_WIDTH = 2.09
BODY_FLOOR = .205
WHEEL_RADIUS = BODY_LENGTH*.074
WHEELBASE = BODY_LENGTH*.61
WHEEL_TRACK = BODY_WIDTH*.82

# Mass, box dimensions, and centre height above the road / longitudinal
# position. Front and rear components are symmetric, so products of inertia
# cancel. The broad battery concentrates mass low in the body. These boxes
# approximate mass distribution, not a buildable internal vehicle layout.
_MASS_BOXES = (
    (720., (1.55, .18, 2.60), .24, 0.),
    (580., (1.90, .56, 4.40), .60, 0.),
    (160., (1.40, .32, .55), .40, WHEELBASE*.5),
    (160., (1.40, .32, .55), .40, -WHEELBASE*.5),
    (180., (1.30, .50, 1.50), .80, 0.),
)
DRY_MASS = sum(item[0] for item in _MASS_BOXES)
GROUND_Y = sum(item[0]*item[2] for item in _MASS_BOXES)/DRY_MASS

# Storage is centred at the body origin. A thermal-reservoir approximation
# holds temperature constant; pressure therefore decreases linearly with mass.
AIR_TEMPERATURE = 293.15
AIR_GAS_CONSTANT = 287.05
AIR_HEAT_RATIO = 1.4
_STORAGE_SIZE = (1.20, .28, 1.40)
TANK_VOLUME = _STORAGE_SIZE[0]*_STORAGE_SIZE[1]*_STORAGE_SIZE[2]
TANK_PRESSURE = 25000000.0
REGULATED_PRESSURE = 1600000.0
_PRESSURE_PER_KG = AIR_GAS_CONSTANT*AIR_TEMPERATURE/TANK_VOLUME
TANK_MASS = TANK_PRESSURE/_PRESSURE_PER_KG
TANK_RESIDUAL = AMBIENT_PRESSURE/_PRESSURE_PER_KG
REFERENCE_MASS = DRY_MASS+TANK_MASS
_AIR_CP = AIR_GAS_CONSTANT*AIR_HEAT_RATIO/(AIR_HEAT_RATIO-1.)
EXHAUST_SPEED = math.sqrt(2.*_AIR_CP*AIR_TEMPERATURE*
                         (1.-(AMBIENT_PRESSURE/REGULATED_PRESSURE)**
                          ((AIR_HEAT_RATIO-1.)/AIR_HEAT_RATIO)))


def _reference_inertia():
    pitch = yaw = roll = 0.
    for mass, dimensions, height, z in _MASS_BOXES:
        width, tall, length = dimensions
        y = height-GROUND_Y
        pitch += mass*((tall*tall+length*length)/12.+y*y+z*z)
        yaw += mass*((width*width+length*length)/12.+z*z)
        roll += mass*((width*width+tall*tall)/12.+y*y)
    width, tall, length = _STORAGE_SIZE
    pitch += TANK_MASS*(tall*tall+length*length)/12.
    yaw += TANK_MASS*(width*width+length*length)/12.
    roll += TANK_MASS*(width*width+tall*tall)/12.
    return pitch, yaw, roll


# Full-tank diagonal inertia. The solver scales this by current/reference
# mass as a deliberately inexpensive approximation during limited-air play.
INERTIA = _reference_inertia()

# A moderately firm suspension is specified by frequency and damping ratio,
# not unexplained spring numbers. At rest all four corners carry equal weight.
RIDE_FREQUENCY = 2.9
DAMPING_RATIO = .82
SPRING_TRAVEL = .16
_CORNER_MASS = REFERENCE_MASS*.25
_SPRING_OMEGA = 2.*math.pi*RIDE_FREQUENCY
SPRING = _CORNER_MASS*_SPRING_OMEGA*_SPRING_OMEGA
DAMPER = 2.*DAMPING_RATIO*_CORNER_MASS*_SPRING_OMEGA
BUMP_SPRING = 10.*SPRING
STATIC_SAG = _CORNER_MASS*GRAVITY/SPRING
WHEEL_REACH = WHEEL_RADIUS+SPRING_TRAVEL
_WHEEL_MOUNT_Y = WHEEL_RADIUS-GROUND_Y+SPRING_TRAVEL-STATIC_SAG
WHEELS = tuple((x, _WHEEL_MOUNT_Y, z)
               for z in (WHEELBASE*.5, -WHEELBASE*.5)
               for x in (WHEEL_TRACK*.5, -WHEEL_TRACK*.5))

# The collision hull follows the floor, bumpers, roof and broad shoulders.
# It is independent of the wheel probes and never forces the car upright.
HULL = tuple((x, height-GROUND_Y, z)
             for height, half_width, zs in (
                 (BODY_FLOOR, .91, (-1.70, 1.70)),
                 (.51, .80, (BODY_LENGTH*.5,)),
                 (.76, .88, (-BODY_LENGTH*.5,)),
                 (1.16, .68, (-.72, .72)),
                 (.82, BODY_WIDTH*.5, (-WHEELBASE*.5, WHEELBASE*.5)))
             for x in (-half_width, half_width) for z in zs)

# Six samples cover a compact rectangular fan plenum between the axles.
FAN_WIDTH = 1.28
FAN_LENGTH = 2.12
FAN_AREA = FAN_WIDTH*FAN_LENGTH
FAN_PLANE_Y = BODY_FLOOR-GROUND_Y
SKIRT_PROBES = tuple((x, FAN_PLANE_Y, z)
                    for z in (-FAN_LENGTH*.5, FAN_LENGTH*.5)
                    for x in (-FAN_WIDTH*.5, 0., FAN_WIDTH*.5))
FAN_MAX_FORCE = REFERENCE_MASS*GRAVITY*3.
FAN_SEAL_HEIGHT = BODY_FLOOR+.04
FAN_CAPTURE_HEIGHT = FAN_SEAL_HEIGHT+.32
FAN_RISE_TIME = .14
FAN_FALL_TIME = .09

# Motor and tyre parameters are also design targets: ordinary acceleration
# is limited by 1.3 g dry grip; fan loading lets the 1.8 g motor target help
# climb the ramp. The separate rocket boost exceeds wheel traction limits.
DRIVE_FORCE = REFERENCE_MASS*18.
DRIVE_POWER = DRIVE_FORCE*19.
TYRE_FRICTION = 1.30
ROLLING_RESISTANCE = .015
TYRE_RELAXATION = .10
AIR_DENSITY = AMBIENT_PRESSURE/(AIR_GAS_CONSTANT*AIR_TEMPERATURE)
DRAG_AREA = BODY_WIDTH*(1.16-BODY_FLOOR)*.78*.30

# Targets describe the feel: boost alone reaches 60 mph in under one second,
# bottom jets have 24 m/s² net lift at full weight, and side jets are gentler.
# Positions are tied to our wheel/body dimensions. Rear jets sit in the bumper;
# bottom jets sit just beyond the axles; side jets sit at the front shoulders.
BOOST_ACCELERATION = 32.0
LIFT_ACCELERATION = 24.0
SIDE_ACCELERATION = 4.0
_REAR_FORCE = REFERENCE_MASS*BOOST_ACCELERATION*.25
_LIFT_FORCE = REFERENCE_MASS*(GRAVITY+LIFT_ACCELERATION)*.25
_SIDE_FORCE = REFERENCE_MASS*SIDE_ACCELERATION
_REAR_Y = .43-GROUND_Y
_REAR_Z = -BODY_LENGTH*.5-.025
_LIFT_Z = WHEELBASE*.5+WHEEL_RADIUS+.12
_SIDE_Y = .31-GROUND_Y
_SIDE_X = BODY_WIDTH*.5+.025
_SIDE_Z = WHEELBASE*.5+WHEEL_RADIUS

# Index order is a public control/rendering contract: rear 0..3, bottom
# front-left/front-right/rear-left/rear-right 4..7, yaw-left/right 8..9.
NOZZLES = tuple(('R%d' % (i+1),
                 (WHEEL_TRACK*.5*fraction, _REAR_Y, _REAR_Z),
                 (0., 0., 1.), _REAR_FORCE)
                for i, fraction in enumerate((.75, .25, -.25, -.75))) + (
    ('D-FL', (WHEEL_TRACK*.5, FAN_PLANE_Y, _LIFT_Z), (0., 1., 0.), _LIFT_FORCE),
    ('D-FR', (-WHEEL_TRACK*.5, FAN_PLANE_Y, _LIFT_Z), (0., 1., 0.), _LIFT_FORCE),
    ('D-RL', (WHEEL_TRACK*.5, FAN_PLANE_Y, -_LIFT_Z), (0., 1., 0.), _LIFT_FORCE),
    ('D-RR', (-WHEEL_TRACK*.5, FAN_PLANE_Y, -_LIFT_Z), (0., 1., 0.), _LIFT_FORCE),
    ('Y-L', (-_SIDE_X, _SIDE_Y, _SIDE_Z), (1., 0., 0.), _SIDE_FORCE),
    ('Y-R', (_SIDE_X, _SIDE_Y, _SIDE_Z), (-1., 0., 0.), _SIDE_FORCE),
)


def tank_pressure(air_mass):
    """Absolute pressure of the current isothermal reservoir in pascals."""
    return max(0., air_mass)*_PRESSURE_PER_KG


def supply_fraction(air_mass):
    """Available nozzle force fraction, after the regulator's pressure drop."""
    pressure = tank_pressure(air_mass)
    if pressure >= REGULATED_PRESSURE:
        return 1.
    if pressure <= AMBIENT_PRESSURE:
        return 0.
    return (pressure-AMBIENT_PRESSURE)/(REGULATED_PRESSURE-AMBIENT_PRESSURE)


def meter_nozzles(duties, air_mass, dt, forces):
    """Fill the reusable ten-force buffer and return propellant consumed.

    The fixed-step caller supplies clamped duties and positive dt. Every
    simultaneous valve shares one regulator and one reservoir. If a step asks
    for more gas than remains, all ten delivered forces scale equally so their
    combined impulse equals the actual exhaust momentum. No list is created.
    Unlimited practice calls this with TANK_MASS and discards the consumption.
    """
    fraction = supply_fraction(air_mass)
    total = 0.
    for i in range(10):
        force = NOZZLES[i][3]*duties[i]*fraction
        forces[i] = force
        total += force
    requested = total*dt/EXHAUST_SPEED
    available = max(0., air_mass-TANK_RESIDUAL)
    if requested > available:
        fraction = available/requested
        for i in range(10):
            forces[i] *= fraction
        return available
    return requested
