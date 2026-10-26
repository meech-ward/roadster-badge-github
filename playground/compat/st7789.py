"""RGBA display adapter. Native PicoVector paints these bytes."""
import sim_host

class ST7789(bytearray):
    def __init__(self):
        super().__init__(320 * 240 * 4)
        self.WIDTH, self.HEIGHT = 160, 120

    def fullres(self, enabled):
        self.WIDTH, self.HEIGHT = (320, 240) if enabled else (160, 120)

    def update(self):
        with open('/frame.rgba', 'wb') as f:
            f.write(memoryview(self)[:self.WIDTH * self.HEIGHT * 4])
        sim_host.present(self.WIDTH, self.HEIGHT)

    def backlight(self, value):
        sim_host.event('backlight', str(value))

    def set_vsync(self, value):
        pass  # timing is host-controlled

    def set_framerate(self, value):
        pass

    def set_async(self, value):
        if value:
            raise NotImplementedError('asynchronous display needs hardware validation')
