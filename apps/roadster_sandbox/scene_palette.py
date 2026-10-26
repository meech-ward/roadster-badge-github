"""RGB palette for readable scenery on the badge's small RGB565 display.

Warm land, cool sky and dark pavement separate by both hue and lightness.
Paint is broad and bright; terrain details stay subordinate to the car.
Keep these ordinary tuples so host checks can measure the quantised colors.
"""

SKY = (66, 132, 179)
LOW_SKY = (137, 181, 201)
HAZE = (210, 226, 220)
RIDGE = (83, 111, 129)
EARTH = (174, 158, 121)
FIELD = (124, 142, 104)
SHOULDER = (198, 182, 145)
ASPHALT = (42, 51, 60)
PAINT = (239, 233, 208)
GUIDE = (238, 190, 83)
SURVEY = (119, 108, 82)
SURVEY_MAJOR = (78, 73, 58)
