"""Inexpensive native horizon and distant terrain for every flight height.

The camera keeps world-up. The background uses that same camera's pitch and
heading, so it never follows a rolling car. Nearby geometry still uses pico3d
and normal depth; this backdrop costs no triangles or native scene-cache space.
Terrain survey lines are projected from a fixed world grid. At high altitude
only coarser powers-of-two grid lines remain, keeping the visible work bounded.
"""
import math
from array import array
from badgeware import *
import scene_palette as palette

SKY = color.rgb(*palette.SKY)
LOW_SKY = color.rgb(*palette.LOW_SKY)
HAZE = color.rgb(*palette.HAZE)
RIDGE = color.rgb(*palette.RIDGE)
EARTH = color.rgb(*palette.EARTH)
SURVEY = color.rgb(*palette.SURVEY)
SURVEY_MAJOR = color.rgb(*palette.SURVEY_MAJOR)

# A repeating distant ridge, built once. No image assets, per-frame shape
# construction, textures, or alpha blending. Different peaks give yaw a cue.
_RIDGE_POINTS = (0,0, 0,-5, 60,-14, 105,-36, 154,-20, 202,-52,
    245,-26, 284,-31, 334,-8, 395,-19, 449,-39, 504,-12, 558,-17,
    604,-45, 652,-31, 693,-57, 740,-24, 806,-13, 859,-32,
    910,-10, 962,-17, 1024,-5, 1024,0)
_ridges = tuple(shape.custom(array('f', _RIDGE_POINTS)) for _ in range(2))
_ridge_transforms = (mat3(), mat3())
_ridge_positions = [[0.,0.],[0.,0.]]


def _clipped_line(line, x1, y1, x2, y2):
    """Preserve bounded endpoints where distant projection magnifies float error."""
    dx,dy=x2-x1,y2-y1
    if (0. <= x1 <= 319. and 0. <= x2 <= 319. and
            0. <= y1 <= 239. and 0. <= y2 <= 239.):
        line(x1,y1,x1+dx,y1+dy)
        return 1
    lo,hi=0.,1.
    p=-dx
    if abs(p) < .00001:
        if x1 < 0.:
            return 0
    else:
        t=x1/p
        if p < 0.:
            if t > hi:
                return 0
            if t > lo:
                lo=t
        else:
            if t < lo:
                return 0
            if t < hi:
                hi=t
    p=dx
    if abs(p) < .00001:
        if 319.-x1 < 0.:
            return 0
    else:
        t=(319.-x1)/p
        if p < 0.:
            if t > hi:
                return 0
            if t > lo:
                lo=t
        else:
            if t < lo:
                return 0
            if t < hi:
                hi=t
    p=-dy
    if abs(p) < .00001:
        if y1 < 0.:
            return 0
    else:
        t=y1/p
        if p < 0.:
            if t > hi:
                return 0
            if t > lo:
                lo=t
        else:
            if t < lo:
                return 0
            if t < hi:
                hi=t
    p=dy
    if abs(p) < .00001:
        if 239.-y1 < 0.:
            return 0
    else:
        t=(239.-y1)/p
        if p < 0.:
            if t > hi:
                return 0
            if t > lo:
                lo=t
        else:
            if t < lo:
                return 0
            if t < hi:
                hi=t
    line(x1+lo*dx,y1+lo*dy,x1+hi*dx,y1+hi*dy)
    return 1


class Background:
    """Reusable camera coefficients; no per-frame projected vertex buffers."""
    def __init__(self):
        self.horizon_y = 0.
        self.heading = 0.
        self.grid_lines = 0
        self.grid_spacing = 64.
        self.ex = self.ey = self.ez = 0.
        self.fx = self.fy = self.fz = 0.
        self.rx = self.rz = self.ux = self.uy = self.uz = 0.
        self.focal = 1.
        self._fov = None

    def camera(self, eye, target, fov):
        self.ex,self.ey,self.ez = eye.x,eye.y,eye.z
        dx,dy,dz = target.x-eye.x,target.y-eye.y,target.z-eye.z
        horizontal = math.sqrt(dx*dx+dz*dz)
        length = math.sqrt(horizontal*horizontal+dy*dy)
        horizontal = max(.0001,horizontal)
        self.fx,self.fy,self.fz = dx/length,dy/length,dz/length
        self.rx,self.rz = -dz/horizontal,dx/horizontal
        self.ux,self.uy,self.uz = (-dx*dy/(horizontal*length),horizontal/length,
                                  -dz*dy/(horizontal*length))
        if fov != self._fov:
            self.focal = 120. / math.tan(fov*math.pi/360.)
            self._fov = fov
        self.horizon_y = 120. + self.focal*dy/horizontal
        self.heading = math.atan2(dx,dz)

    # Both survey directions project a ground segment with the reference
    # arithmetic order and let the native line primitive clip it. The segment's
    # eye-relative height is -ey at both ends and the near-plane mix never changes the shared axis,
    # so the products that only involve that axis are computed once per line.
    def _survey_x(self, line, ax, az, bz, ayfy, ayuy):
        """Project the constant-x segment (ax, az)-(ax, bz), eye-relative."""
        fz = self.fz
        axfx = ax*self.fx
        ad=axfx+ayfy+az*fz
        bd=axfx+ayfy+bz*fz
        if ad < 1. and bd < 1.:
            return 0
        if ad < 1.:
            az=az+(bz-az)*((1.-ad)/(bd-ad))
            ad=1.
        elif bd < 1.:
            bz=bz+(az-bz)*((1.-bd)/(ad-bd))
            bd=1.
        focal = self.focal
        axrx = ax*self.rx
        axux = ax*self.ux+ayuy
        rz, uz = self.rz, self.uz
        x1=160.+focal*(axrx+az*rz)/ad
        y1=120.-focal*(axux+az*uz)/ad
        x2=160.+focal*(axrx+bz*rz)/bd
        y2=120.-focal*(axux+bz*uz)/bd
        if ((x1 < 0. and x2 < 0.) or (x1 > 319. and x2 > 319.) or
                (y1 < 0. and y2 < 0.) or (y1 > 239. and y2 > 239.)):
            return 0
        if self.ey > 512.:
            return _clipped_line(line,x1,y1,x2,y2)
        dx,dy=x2-x1,y2-y1
        if (0. <= x1 <= 319. and 0. <= x2 <= 319. and
                0. <= y1 <= 239. and 0. <= y2 <= 239.):
            line(x1,y1,x1+dx,y1+dy)
            return 1
        # Segment bounds already overlap the screen. Its line must also cross
        # the screen rectangle: the two extreme corner cross-products give
        # that exact reject without divisions or constructing clipped points.
        corner=dy*x1-dx*y1
        low=corner+(dx*239. if dx < 0. else 0.)-(dy*319. if dy > 0. else 0.)
        high=corner+(dx*239. if dx > 0. else 0.)-(dy*319. if dy < 0. else 0.)
        if low > 0. or high < 0.:
            return 0
        # Native screen.line clips before rasterizing, so no second full
        # Python edge clip is needed at ordinary flight heights.
        line(x1,y1,x2,y2)
        return 1

    def _survey_z(self, line, ax, bx, az, ayfy, ayuy):
        """Project the constant-z segment (ax, az)-(bx, az), eye-relative."""
        fx = self.fx
        azfz = az*self.fz
        ad=ax*fx+ayfy+azfz
        bd=bx*fx+ayfy+azfz
        if ad < 1. and bd < 1.:
            return 0
        if ad < 1.:
            ax=ax+(bx-ax)*((1.-ad)/(bd-ad))
            ad=1.
        elif bd < 1.:
            bx=bx+(ax-bx)*((1.-bd)/(ad-bd))
            bd=1.
        focal = self.focal
        azrz = az*self.rz
        azuz = az*self.uz
        rx, ux = self.rx, self.ux
        x1=160.+focal*(ax*rx+azrz)/ad
        y1=120.-focal*(ax*ux+ayuy+azuz)/ad
        x2=160.+focal*(bx*rx+azrz)/bd
        y2=120.-focal*(bx*ux+ayuy+azuz)/bd
        if ((x1 < 0. and x2 < 0.) or (x1 > 319. and x2 > 319.) or
                (y1 < 0. and y2 < 0.) or (y1 > 239. and y2 > 239.)):
            return 0
        if self.ey > 512.:
            return _clipped_line(line,x1,y1,x2,y2)
        dx,dy=x2-x1,y2-y1
        if (0. <= x1 <= 319. and 0. <= x2 <= 319. and
                0. <= y1 <= 239. and 0. <= y2 <= 239.):
            line(x1,y1,x1+dx,y1+dy)
            return 1
        # Segment bounds already overlap the screen. Its line must also cross
        # the screen rectangle: the two extreme corner cross-products give
        # that exact reject without divisions or constructing clipped points.
        corner=dy*x1-dx*y1
        low=corner+(dx*239. if dx < 0. else 0.)-(dy*319. if dy > 0. else 0.)
        high=corner+(dx*239. if dx > 0. else 0.)-(dy*319. if dy < 0. else 0.)
        if low > 0. or high < 0.:
            return 0
        # Native screen.line clips before rasterizing, so no second full
        # Python edge clip is needed at ordinary flight heights.
        line(x1,y1,x2,y2)
        return 1

    def draw(self):
        screen.pen=SKY
        screen.clear()
        horizon=self.horizon_y
        ground_top=max(0.,min(240.,horizon))
        # Warm haze distinguishes the true horizon from either terrain or HUD.
        if horizon > 0.:
            # Two broad sky bands remain legible after RGB565 quantisation.
            # No gradient texture, alpha blend or extra native scene geometry.
            screen.pen=LOW_SKY
            low_top=max(0.,min(240.,horizon-38.))
            screen.rectangle(0,low_top,320,ground_top-low_top)
            screen.pen=HAZE
            haze_top=max(0.,min(240.,horizon-14.))
            screen.rectangle(0,haze_top,320,ground_top-haze_top)
            phase=(self.heading*1024./(2.*math.pi)) % 1024.
            # The panorama is a distant angular cue. Exact local parallax is
            # provided by the ground grid and nearby native world geometry.
            y=int(horizon)
            x=int(phase)-1024
            screen.pen=RIDGE
            for index in range(2):
                old=_ridge_positions[index]
                if x != old[0] or y != old[1]:
                    _ridge_transforms[index].translate(x-old[0],y-old[1])
                    old[0],old[1]=x,y
                    _ridges[index].transform=_ridge_transforms[index]
                screen.shape(_ridges[index])
                x+=1024
        screen.pen=EARTH
        screen.rectangle(0,ground_top,320,240.-ground_top)
        lines=0
        ey=self.ey
        if ey > .1:
            # Coarser grid lines are subsets of the same 8m survey, so their
            # world phase never slides with the car. Bounded twelve-by-twelve
            # work also gives ground cues well beyond pico3d's 135m far clip.
            spacing=8.
            while spacing < ey*.5:
                spacing*=2.
            self.grid_spacing=spacing
            # Center the finite grid on the visible terrain ahead, not directly
            # beneath the camera (which is below the screen during high flight).
            look_distance=ey*4.
            horizontal=max(.0001,self.uy)
            ex,ez=self.ex,self.ez
            x=math.floor((ex+self.fx/horizontal*look_distance)/spacing)*spacing
            z=math.floor((ez+self.fz/horizontal*look_distance)/spacing)*spacing
            extent=spacing*6.
            x_phase=int(x/spacing) & 3
            z_phase=int(z/spacing) & 3
            line=screen.line
            survey_x=self._survey_x
            survey_z=self._survey_z
            # Eye-relative segment ends; the two constant products of the
            # segment height -ey are shared by every line of this frame.
            ay=-ey
            ayfy=ay*self.fy
            ayuy=ay*self.uy
            near_z,far_z=z-extent-ez,z+extent-ez
            near_x,far_x=x-extent-ex,x+extent-ex
            for step in range(-5,7):
                offset=step*spacing
                # Major survey lines are fixed in world space, not relative
                # to the moving patch. One in four anchors distance/heading.
                screen.pen=SURVEY_MAJOR if (x_phase+step) & 3 == 0 else SURVEY
                lines+=survey_x(line,x+offset-ex,near_z,far_z,ayfy,ayuy)
                screen.pen=SURVEY_MAJOR if (z_phase+step) & 3 == 0 else SURVEY
                lines+=survey_z(line,near_x,far_x,z+offset-ez,ayfy,ayuy)
        self.grid_lines=lines
