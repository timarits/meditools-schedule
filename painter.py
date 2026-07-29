"""painter.py -- draws ui.py's screens on the Presto.

Two font engines, chosen automatically at import time:

  VectorFont  PicoVector + Alright Fonts (.af). This is what the design asks
              for -- real Figtree at 176 px with tabular numerals. Requires
              the .af files to be on the device (see tools/make_fonts.py).

  BitmapFont  PicoGraphics' built-in "bitmap8", scaled to integer multiples.
              Fallback so the device is still usable and legible if the fonts
              were never uploaded. It looks nothing like the design.

Pen creation is cached: PicoGraphics has a limited pen palette in some modes
and create_pen() is not free, so each colour is created once.
"""

import theme as T

FONT_FILES = {
    900: "Figtree-Black.af",
    800: "Figtree-ExtraBold.af",
    700: "Figtree-ExtraBold.af",
    600: "Figtree-SemiBold.af",
}

# PicoVector places text by its baseline; if a firmware build disagrees this is
# the single number to adjust (selftest.py prints a ruler to check it against).
VECTOR_BASELINE_OFFSET = 0


class BitmapFont(object):
    """bitmap8 fallback: 8 px per unit of scale, positioned by its top edge."""

    available = False

    def __init__(self, display):
        self.display = display
        display.set_font("bitmap8")

    def _scale(self, style):
        return max(1, int(round(style.size / 8.0)))

    def measure(self, s, style):
        return self.display.measure_text(s, self._scale(style))

    def draw(self, s, x, baseline_y, style):
        scale = self._scale(style)
        self.display.text(s, int(x), int(baseline_y - 8 * scale), T.WIDTH, scale)


class VectorFont(object):
    """PicoVector + Alright Fonts -- the design's actual typography.

    One PicoVector instance per weight. set_font() re-reads and re-parses the
    .af file, which measures at ~77 ms for these files; a single screen mixes
    three weights and alternates between them, so doing it per draw call would
    cost half a second a frame. Each instance keeps its own font, so loading
    them once at boot (~230 ms, ~210 KiB) makes switching weight free.
    Changing *size* on an instance is already free.
    """

    available = True

    def __init__(self, display):
        from picovector import PicoVector, ANTIALIAS_X4, Transform
        self.transform = Transform()
        self._by_weight = {}
        self._size = {}
        self._spacing = {}
        self._calibration = {}
        loaded = {}
        for weight in sorted(set(FONT_FILES)):
            name = FONT_FILES[weight]
            vec = loaded.get(name)
            if vec is None:
                vec = PicoVector(display)
                vec.set_antialiasing(ANTIALIAS_X4)
                vec.set_transform(self.transform)
                vec.set_font(name, 32)
                loaded[name] = vec
                self._size[name] = 32
            self._by_weight[weight] = (name, vec)

    def _spacing_percent(self, name, vec, style):
        """Convert the design's tracking in px into PicoVector's percentage.

        PicoVector expresses letter spacing as a percentage of normal, not in
        pixels, and the pixels-per-percent depends on the face and the size.
        Rather than hard-code a fudge factor, measure it once per weight+size
        with a two-character probe and cache the result.
        """
        key = (name, style.size)
        per_percent = self._calibration.get(key)
        if per_percent is None:
            vec.set_font_letter_spacing(100)
            base = vec.measure_text("MM")[2]
            vec.set_font_letter_spacing(200)
            wide = vec.measure_text("MM")[2]
            per_percent = (wide - base) / 100.0
            self._calibration[key] = per_percent
        if not per_percent:
            return 100
        return int(100 + style.tracking / per_percent)

    def _select(self, style):
        name, vec = self._by_weight[style.weight]
        if self._size[name] != style.size:
            vec.set_font_size(style.size)
            self._size[name] = style.size
        want = (self._spacing_percent(name, vec, style)
                if style.tracking else 100)
        if self._spacing.get(name) != want:
            vec.set_font_letter_spacing(want)
            self._spacing[name] = want
        return vec

    def measure(self, s, style):
        return int(self._select(style).measure_text(s)[2])

    def draw(self, s, x, baseline_y, style):
        # Always draw the whole string: measure_text(" ") reports zero width,
        # so stepping character by character silently swallows every space.
        self._select(style).text(s, int(x),
                                 int(baseline_y + VECTOR_BASELINE_OFFSET))


def make_font(display):
    """VectorFont when the .af files are present, BitmapFont otherwise."""
    try:
        font = VectorFont(display)
        font.measure("0", T.T_LABEL)      # forces a real font load
        return font
    except Exception:
        return BitmapFont(display)


class PrestoPainter(object):
    """The painter interface ui.py draws through."""

    def __init__(self, display, font=None):
        self.display = display
        self.font = font or make_font(display)
        self._pens = {}

    def pen(self, rgb):
        p = self._pens.get(rgb)
        if p is None:
            p = self.display.create_pen(rgb[0], rgb[1], rgb[2])
            self._pens[rgb] = p
        return p

    # -- painter interface --------------------------------------------------

    def clear(self, rgb):
        self.display.set_pen(self.pen(rgb))
        self.display.clear()

    def rect(self, x, y, w, h, rgb):
        self.display.set_pen(self.pen(rgb))
        self.display.rectangle(int(x), int(y), int(w), int(h))

    def line(self, x1, y1, x2, y2, thickness, rgb):
        self.display.set_pen(self.pen(rgb))
        self.display.line(int(x1), int(y1), int(x2), int(y2), int(thickness))

    def measure(self, s, style):
        return self.font.measure(s, style)

    def text(self, s, x, baseline_y, style, rgb, align="left"):
        if align == "right":
            x = x - self.font.measure(s, style)
        elif align == "center":
            x = x - self.font.measure(s, style) // 2
        self.display.set_pen(self.pen(rgb))
        self.font.draw(s, int(x), int(baseline_y), style)
