"""Uncached drawing oracle for the game's ten-nozzle instrument.

This small test-only fixture describes the visible instrument in force units,
independently of the app's integer pixel cache. It is original project code,
not an archived app or an imported vehicle/model asset. Both the host recording
screen and the native PicoVector tests use it. It has no badge dependencies.
"""


def draw_map(rect, forces, nozzles, colors):
    """Draw the instrument and return the active nozzle indices."""
    shell = ((282, 10, 16, 32, 'BODY'), (284, 6, 12, 6, 'BODY'),
             (284, 18, 12, 14, 'INK'), (278, 16, 3, 8, 'MUTED'),
             (299, 16, 3, 8, 'MUTED'), (278, 32, 3, 8, 'MUTED'),
             (299, 32, 3, 8, 'MUTED'), (287, 8, 6, 2, 'WHITE'))
    for x, y, width, height, color in shell:
        rect(x, y, width, height, colors[color])
    active = []
    z_scale = 17./max(abs(nozzle[1][2]) for nozzle in nozzles)
    for index, (force, nozzle) in enumerate(zip(forces, nozzles)):
        x = int(290 - nozzle[1][0] * 12)
        y = int(26 - nozzle[1][2] * z_scale)
        if force <= 1.:
            rect(x - 1, y - 1, 2, 2, colors['DIM'])
            continue
        active.append(index)
        family = 'GOLD' if index < 4 else 'CYAN' if index < 8 else 'PINK'
        pen = colors[family]
        rect(x - 1, y - 1, 3, 3, pen)
        length = int(3 * min(1., force / nozzle[3]))
        if index < 4:
            rect(x, y + 2, 1, 1 + length, pen)
        elif index >= 8:
            length += 2
            start = x + 2 if index == 8 else x - length - 1
            rect(start, y, length, 1, pen)
    return active
