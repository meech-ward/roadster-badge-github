# Playing and controls

Hold the badge with the screen facing you in the inverted gamepad grip. When
the centering prompt appears, tap the **outermost right touch pad**, hold your
normal playing grip still for about a second, then release both right pads
and the clicky buttons. Recenter later with **Settings → Center grip**.
The touch D-pad already drives before calibration finishes.

## Touch pads and clicky buttons

The copper touch pads and the physical clicky controls are separate inputs.
Their directions have different jobs during play.

| Control | During play |
| --- | --- |
| Touch D-pad Up | Drive forward |
| Touch D-pad Down | Brake forward motion, then reverse |
| Touch D-pad Left / Right | Steer |
| Outer right touch pad — Select | Hold rear boost; also arms motion on the ground |
| Inner right touch pad — Back | Hold lift; also arms motion on the ground |
| Left middle touch pad — Menu | Open or close Settings |
| Right middle touch pad — Home | Open scene chooser; tap again to browse |
| Clicky Left / Right | Roll left/right; release to counterthrust |
| Clicky Up / Down | Pitch nose-up/nose-down; release to counterthrust |
| Clicky Enter/Select | Toggle fan; confirm a highlighted menu option |

Clicky directions request controlled rotation, not a complete trick. You can
combine lift, boost and a clicky direction. Fan and thrusters work together;
fan suction may still hold the car against a surface. Turn the fan off for an
easy free-air launch.

The outer-right touch pad is named `BUTTON_BACK` by the firmware in this grip;
the app deliberately presents it as **Select**. The inner pad is presented as
**Back**. This remapping is local to this game. Touch Home opens scenes; the
separate physical Home/Boot function belongs to the firmware launcher.

## Tilt and flicks

After calibration, a gentle grounded lean moves the camera: left/right orbits
the car and up/down looks upward/downward. This works while using the
touch D-pad. The movement is smooth and bounded; returning to center restores
the normal view. The gauge reads **LOOK**. Ground looking works in every motion
mode and requires upright wheel support, so an upside-down landing cannot
accidentally turn it on.

Holding lift or boost, or taking off, returns tilt to the jets. Clicky rotation
takes priority over camera control. The camera eases back to its normal framing
in either case. **Both** is the default motion mode. In the air, a gentle lean
requests continuous roll or pitch;
returning to your calibrated center asks the jets to brake and level the car.
Heading stays free. Centering is a physical assist and needs enough room and
thrust to recover.

| Deliberate flick | Committed trick |
| --- | --- |
| Left / right | Kickflip in that direction |
| Back, toward your face | Backflip |
| Forward, away from your face | Frontflip |

A recognized flick commits to one rotation and upright settling. Returning to
center, counter-tilting or continuing to hold lift/boost does not cancel it.
Another flick during the trick is discarded, not queued. A clicky direction
takes over immediately; contact, a menu, centering or reset ends the sequence.
The car never teleports through a collision to finish a flip.

After calibration, motion is armed while airborne even with both right pads
released. On the ground or a parked-car roof, hold lift or boost to arm it.
After menus or calibration, release the right pads and clickies; control
resumes with a fresh valid sensor sample. A new quiet interval is required
before another flick. This prevents an old gesture from firing after landing
or leaving a menu.

**Settings → Motion** cycles Flick, Tilt and Both. Tilt enables continuous lean
and centered recovery; Flick uses deliberate tricks without continuous lean;
Both combines them. Sideways flick onset is 160°/s or 0.75 g; forward/back onset
is 180°/s or 0.80 g, requiring a brisk, deliberate stroke. These are recognition
thresholds, not guaranteed triggers:
calibration, quiet rearming and sample validity also matter. Vertical gestures
use a short intent window to accommodate a preparatory down/up movement.

## Lift and air

Holding lift gives a strong launch, then smoothly reduces ascent toward a
practice hover about 16 metres above the ground-level resting car. Release to
descend. It is a thrust governor, not an invisible ceiling: inertia, a ramp or
a trick can carry the car higher. Lift follows the car's orientation.

Badge control defaults to **Unlimited** air for practice. **Settings → Air
supply** switches to Limited, where pressure and available thrust fall as air
is consumed. **Refill air** restores the tank in place without moving the car.
Landing and lifting again do not require Reset.

## Settings, scenes and camera

Settings always opens on **Reset car**, followed by Fan, Refill air, Center
grip, Scene, Camera, Motion, Air supply and Controls. Use Up/Down from either
input source to choose, outer-right touch or clicky Enter to apply, and the
inner-right touch pad to close. Physics continues behind menus.

The scene chooser contains **Airfield**, **Open Ramp** and **Tesla Lineup**.
Use directions or repeated right-middle taps to browse; Select enters the
highlighted scene. Back or Menu cancels. Confirming a scene explicitly resets
the car to that scene's approach and clears old held thrust.

**Camera** switches between Follow Roadster and Directly behind. Airfield and
Tesla Lineup share the same offset follow angle. Open Ramp starts directly
behind for alignment. A normal car reset preserves the selected follow angle;
changing scene chooses that scene's default. Grounded looking works with both
camera choices, up to 20° left/right and 1.3 metres higher/lower. Changing the
view, resetting or centering the grip clears its temporary offset.

Fixed ground seams provide scale as scenery passes the car; stronger lines
mark larger intervals. The horizon stays world-upright through flips, and
distant ground lines remain visible beyond the nearby floor geometry.

## Reading the top instruments

The top strip shows **km/h**, delivered **boost**, the **fan ON/OFF** tile beside
the clicky fan button, the **tilt square**, and a small **jet diagram**. The
remaining 190 vertical pixels show the world; there is no bottom status bar.

The tilt dot shows sensed lean; the jet diagram shows actual delivered thrust.
They can differ when motion is disarmed or pressure is insufficient. The gauge
label helps distinguish **LOOK** (ground camera), **FLIP** (committed trick), **LEVEL** (recovery),
**STEADY** (settled), **SENSE** (sensing but disarmed), **CENTER** (calibration),
**WAIT** (invalid/unavailable sample) and **CLIP** (sensor saturation).

## Laptop preview

The preview's direct driving panel is a development controller, not a claim
that keyboard keys reproduce physical sensor feel.

| Key | Action |
| --- | --- |
| W/A/S/D or arrows | Throttle/reverse and steering |
| Space | Brake |
| B / J | Rear boost / direct bottom jets |
| Q / E | Left / right bottom jets |
| 1–4 / 5–8 / 9–0 | Individual rear / bottom / side jets |
| F | Toggle fan |
| H / K / I / L | Hop / kickflip / backflip / spin presets |
| X / T / R | Cancel preset / refill air / reset car |

Use the page's scene and camera selectors for inspection or jump alignment.
Laptop J and individual nozzle keys are raw jet commands; they bypass the
badge's held-lift governor. The direct desktop controller starts with Limited
air; activating badge controls selects its Unlimited practice default.
