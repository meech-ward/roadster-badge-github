"""Resident long-distance silhouettes; original detailed fleet is unchanged."""
from fleet_models import (CATALOG, _Part, _SEDANS, _rgb, _shade, SILVER,
                          INK, GLASS, LAMP, RED, RUBBER)


def build_distant_model(slug):
    """Small resident silhouette for parked cars beyond the close-view budget.

    The existing authored low/high meshes remain the close views. This tier
    keeps each vehicle's dimensions, paint, roof type and wheel positions,
    while omitting features smaller than a pixel at the intended distance.
    It never supplies collision geometry.
    """
    info = next(item for item in CATALOG if item['id'] == slug)
    part = _Part('distant_shell')
    width, length, height = info['width'] / 2, info['length'] / 2, info['height']
    paint = (_SEDANS[slug][0] if slug in _SEDANS else {
        'original_roadster': _rgb(220, 84, 23), 'cybertruck': SILVER,
        'semi': _rgb(212, 222, 225), 'cybercab': _rgb(194, 158, 91),
        'robovan': _rgb(213, 222, 224)}[slug])

    def shell(x, z0, z1, bottom, rear, front, shade):
        # Open-bottom trapezoidal prism: five faces / ten triangles.
        a, b = (-x,bottom,z0), (x,bottom,z0)
        c, d = (-x,rear,z0), (x,rear,z0)
        e, f = (-x,bottom,z1), (x,bottom,z1)
        g, h = (-x,front,z1), (x,front,z1)
        for points, col in (((a,b,d,c),_shade(shade,-12)),
                            ((e,g,h,f),shade), ((a,c,g,e),_shade(shade,-20)),
                            ((b,f,h,d),_shade(shade,-8)), ((c,d,h,g),_shade(shade,16))):
            part.poly(points,col)

    if slug == 'semi':
        shell(width*.91,-length,length,.30,.94,.94,INK)
        shell(width*.90,-.15,length*.90,.94,height*.95,height*.91,paint)
        part.poly(((-width*.78,height*.57,length*.902),
                   (width*.78,height*.57,length*.902),
                   (width*.78,height*.84,length*.902),
                   (-width*.78,height*.84,length*.902)),GLASS)
        axles, radius = (-2.74,-1.47,2.43),.49
    elif slug == 'robovan':
        shell(width*.94,-length*.96,length*.96,.25,height*.89,height*.89,paint)
        shell(width*.79,-length*.73,length*.73,height*.89,height,height,paint)
        for sign in (-1,1):
            x=sign*width*.942
            part.poly(((x,height*.57,-length*.78),(x,height*.57,length*.78),
                       (x,height*.84,length*.78),(x,height*.84,-length*.78)),GLASS)
        axles,radius=(-2.12,2.12),.25
    else:
        body_height = height * (.62 if slug not in ('cybertruck','original_roadster') else .65)
        shell(width*.91,-length,length,.24,body_height,body_height*.88,paint)
        if slug == 'cybertruck':
            # The apex and rear bed are its silhouette, even at a few pixels.
            x=width*.78
            for sign in (-1,1):
                part.poly(((sign*x,body_height,-length*.45),
                           (sign*x,height,.18),
                           (sign*x,body_height,length*.68)),GLASS)
            part.poly(((-x,body_height,-length*.45),(x,body_height,-length*.45),
                       (x,height,.18),(-x,height,.18)),paint)
            part.poly(((-x,height,.18),(x,height,.18),(x,body_height,length*.68),
                       (-x,body_height,length*.68)),GLASS)
            axles,radius=(-1.85,1.81),.43
        else:
            z0,z1=(-length*.49,length*.39)
            if slug == 'original_roadster':
                # Open cockpit and a thin upright windscreen, not a sedan roof.
                shell(width*.72,z0,z1,body_height-.03,body_height+.03,body_height+.03,INK)
                shell(width*.70,z1-.13,z1+.10,body_height,body_height+.07,height,GLASS)
                axles,radius=(-1.19,1.16),.30
            else:
                shell(width*.72,z0,z1,body_height,height*.94,height,GLASS)
                axles,radius=((-length*.60,length*.60),height*.235)
        # Bright nose / red tail. Their positions remain fixed to the body.
        for z,y,shade in ((length+.004,body_height*.79,LAMP),
                          (-length-.004,body_height*.86,RED)):
            part.poly(((-width*.66,y,z),(width*.66,y,z),
                       (width*.66,y+.055,z),(-width*.66,y+.055,z)),shade)
    for z in axles:
        for sign in (-1,1):
            x=sign*width*.935
            part.poly(((x,.05,z-radius),(x,.05,z+radius),
                       (x,radius*1.90,z+radius),(x,radius*1.90,z-radius)),RUBBER)
    finished=part.finish()
    return {'parts':(finished,), 'triangles':len(finished[2])//3,
            'vertices':len(finished[1])//3, 'detail':'distant'}
