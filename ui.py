"""ui.py -- the MediTools screens, drawn against an abstract painter.

Implements design hand-off "Presto Screens.dc.html", direction 1b: a coloured
state band on top, a black label/value panel below.

This module deliberately imports nothing hardware-specific. It talks to a
`painter` object with a small interface, which lets the exact same layout code
run on the Presto (painter.py, via PicoGraphics/PicoVector) and on a desktop
(tools/svgpainter.py, which emits SVG for tools/render_preview.py). Layout
bugs are therefore visible without touching the device.

Painter interface:
    clear(rgb)
    rect(x, y, w, h, rgb)
    line(x1, y1, x2, y2, thickness, rgb)
    text(s, x, baseline_y, style, rgb, align="left")
    measure(s, style) -> width in px

Every draw_* function returns the ambient-LED colour for that state, so the
screen and the LEDs can never drift apart.
"""

import theme as T

GAP_NUM_UNIT = 14          # between "83" and "min"
UNIT_DROP = 12             # "min" sits this far above the numeral baseline
MIN_COUNTDOWN_SIZE = 88    # auto-fit never shrinks past this
CAPTION_TOP = 96           # in the gap between the state word and the numeral


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def _fit(p, value, unit, style, unit_style):
    """Shrink the countdown numeral until value + unit fits the content width.

    The design is drawn with a two-digit countdown, but a long eating window
    (or a generous no_eat_before_min) can produce three or four digits.
    """
    size = style.size
    while size > MIN_COUNTDOWN_SIZE:
        trial = T.Type(size, style.weight)
        w = p.measure(value, trial) + GAP_NUM_UNIT + p.measure(unit, unit_style)
        if w <= T.CONTENT_W:
            break
        size -= 8
    return T.Type(size, style.weight)


def _countdown(p, value, unit, bottom, colour, unit_colour):
    """Big numeral with its unit, baseline-aligned, bottom-left of the band."""
    style = _fit(p, value, unit, T.T_COUNTDOWN, T.T_COUNTDOWN_UNIT)
    p.text(value, T.PAD_X, bottom, style, colour)
    x = T.PAD_X + p.measure(value, style) + GAP_NUM_UNIT
    p.text(unit, x, bottom - UNIT_DROP, T.T_COUNTDOWN_UNIT, unit_colour)


def _rows(p, rows):
    """The black panel's label/value rows, vertically centred with a divider.

    `rows` is a list of (label, value) pairs.
    """
    row_h = 16 + T.T_VALUE.size + 16                   # 70
    block = row_h * len(rows) + (len(rows) - 1)        # + 1px dividers
    top = T.BAND_H + (T.HEIGHT - T.BAND_H - block) // 2
    for i, (label, value) in enumerate(rows):
        y = top + i * (row_h + 1)
        base = T.baseline(y + 16, T.T_VALUE)
        p.text(label, T.PAD_X, base, T.T_LABEL, T.LABEL)
        p.text(value, T.WIDTH - T.PAD_X, base, T.T_VALUE, T.WHITE, align="right")
        if i < len(rows) - 1:
            p.rect(T.PAD_X, y + row_h, T.CONTENT_W, 1, T.DIVIDER)


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

def draw_state(p, txt, red, minutes, until_hhmm, dose_hhmm, caption=None):
    """RED (no eating) or GREEN (eating allowed) -- the two everyday screens.

    `caption` names what the big number is counting down to. Without it a bare
    number is ambiguous the moment the same screen can count toward two
    different things.
    """
    band = T.RED_BAND if red else T.GREEN_BAND
    p.clear(T.PANEL_BG)
    p.rect(0, 0, T.WIDTH, T.BAND_H, band)

    p.text(txt["no_eat"] if red else txt["eat"], T.PAD_X,
           T.baseline(T.PAD_TOP, T.T_STATE), T.T_STATE, T.WHITE)
    if caption:
        p.text(caption, T.PAD_X, T.baseline(CAPTION_TOP, T.T_CAPTION),
               T.T_CAPTION, T.mix(T.WHITE, band, 0.82))
    _countdown(p, "%d" % minutes, txt["unit_min"],
               T.BAND_H - T.PAD_BOTTOM, T.WHITE, T.mix(T.WHITE, band, 0.85))

    _rows(p, [
        (txt["eat_at_label"] if red else txt["eat_until_label"], until_hhmm),
        (txt["med_at_label"], dose_hhmm),
    ])
    return T.LED_RED if red else T.LED_GREEN


def draw_alarm(p, txt, orange_phase, hold_frac):
    """The blinking medicine alarm, with the hold-to-confirm bar."""
    if orange_phase:
        band, ink, fill, led = (T.ORANGE_BAND, T.ALARM_DARK_TEXT,
                                T.BAR_FILL_A, T.LED_ORANGE)
    else:
        band, ink, fill, led = (T.BLUE_BAND, T.WHITE,
                                T.BAR_FILL_B, T.LED_BLUE)

    p.clear(T.PANEL_BG)
    p.rect(0, 0, T.WIDTH, T.BAND_H, band)
    p.text(txt["med_title"], T.PAD_X,
           T.baseline(T.PAD_TOP, T.T_STATE), T.T_STATE, ink)

    # two-line call to action, bottom-aligned in the band
    lines = txt["med_lines"]
    step = int(T.T_ALARM_LINE.size * 0.95)
    bottom = T.BAND_H - T.PAD_BOTTOM
    for i, line in enumerate(lines):
        y = bottom - (len(lines) - 1 - i) * step
        p.text(line, T.PAD_X, y, T.T_ALARM_LINE, ink)

    # hint + progress bar, vertically centred in the black panel
    bar_h = 44
    block = T.T_ALARM_HINT.size + 18 + bar_h
    top = T.BAND_H + (T.HEIGHT - T.BAND_H - block) // 2
    p.text(txt["med_hold"], T.PAD_X,
           T.baseline(top, T.T_ALARM_HINT), T.T_ALARM_HINT, T.ALARM_HINT)
    bar_y = top + T.T_ALARM_HINT.size + 18
    p.rect(T.PAD_X, bar_y, T.CONTENT_W, bar_h, T.BAR_TRACK)
    if hold_frac > 0:
        w = int(T.CONTENT_W * min(1.0, hold_frac))
        if w > 0:
            p.rect(T.PAD_X, bar_y, w, bar_h, fill)
    return led


def draw_taken(p, txt, time_hhmm):
    """'GENOMEN!' -- shown for ~2.5 s after a successful hold."""
    p.clear(T.GREEN_BAND)

    # The check is drawn, not typed: the device font has no guaranteed glyph
    # for U+2713 and a missing glyph on this screen would read as a failure.
    s = 120
    x0, y0 = T.PAD_X, 60
    p.line(x0 + int(0.04 * s), y0 + int(0.55 * s),
           x0 + int(0.34 * s), y0 + int(0.85 * s), int(0.16 * s), T.WHITE)
    p.line(x0 + int(0.34 * s), y0 + int(0.85 * s),
           x0 + int(0.95 * s), y0 + int(0.12 * s), int(0.16 * s), T.WHITE)

    sub_base = T.HEIGHT - T.PAD_BOTTOM - 6 - 1
    p.text(txt["taken_sub"] % time_hhmm, T.PAD_X, sub_base,
           T.T_TAKEN_SUB, T.mix(T.WHITE, T.GREEN_BAND, 0.8))
    p.text(txt["taken"], T.PAD_X,
           sub_base - T.T_TAKEN_SUB.size - 10 - 6, T.T_TAKEN, T.WHITE)
    return T.LED_GREEN


def draw_night(p, clock_hhmm):
    """Night: as little light as the panel can emit."""
    p.clear(T.PURE_BLACK)
    p.text(clock_hhmm, T.PAD_X,
           T.HEIGHT - T.PAD_BOTTOM - int(T.T_NIGHT_CLOCK.size * 0.22),
           T.T_NIGHT_CLOCK, T.NIGHT_CLOCK)
    return T.LED_OFF


def draw_night_wake(p, txt, hours, minutes, dose_hhmm, clock_hhmm):
    """Tapped at night: how long until the next medicine, then back to sleep."""
    p.clear(T.PURE_BLACK)
    p.rect(0, T.BAND_H - 1, T.WIDTH, 1, T.NIGHT_DIVIDER)

    p.text(txt["sleep"], T.PAD_X,
           T.baseline(T.PAD_TOP, T.T_SLEEP_WORD), T.T_SLEEP_WORD, T.NIGHT_WORD)

    bottom = T.BAND_H - T.PAD_BOTTOM
    x = T.PAD_X
    if hours > 0:
        h = "%d" % hours
        p.text(h, x, bottom, T.T_SLEEP_BIG, T.NIGHT_BIG)
        x += p.measure(h, T.T_SLEEP_BIG) + 12
        p.text(txt["unit_hour"], x, bottom - 10, T.T_SLEEP_UNIT, T.NIGHT_BIG)
        x += p.measure(txt["unit_hour"], T.T_SLEEP_UNIT) + 12
        p.text("%02d" % minutes, x, bottom, T.T_SLEEP_BIG, T.NIGHT_BIG)
    else:
        m = "%d" % minutes
        p.text(m, x, bottom, T.T_SLEEP_BIG, T.NIGHT_BIG)
        x += p.measure(m, T.T_SLEEP_BIG) + 12
        p.text(txt["unit_min"], x, bottom - 10, T.T_SLEEP_UNIT, T.NIGHT_BIG)

    base = T.BAND_H + (T.HEIGHT - T.BAND_H) // 2 + 10
    p.text(txt["med_at"] % dose_hhmm, T.PAD_X, base, T.T_LABEL, T.NIGHT_SUB)
    p.text(clock_hhmm, T.WIDTH - T.PAD_X, base, T.T_ALARM_HINT,
           T.NIGHT_SMALL_CLOCK, align="right")
    return T.LED_OFF


def draw_error(p, txt):
    """Shown until the clock has been set -- the device is not usable before."""
    p.clear(T.ERROR_BG)
    p.rect(0, 0, T.WIDTH, T.BAND_H, T.ERROR_BAND)
    p.rect(0, 0, T.WIDTH, 14, T.ERROR_ACCENT)

    p.text(txt["err_eyebrow"], T.PAD_X,
           T.baseline(14 + T.PAD_TOP, T.T_ERROR_EYEBROW),
           T.T_ERROR_EYEBROW, T.ERROR_ACCENT)

    lines = txt["no_clock_lines"]
    bottom = T.BAND_H - T.PAD_BOTTOM
    for i, line in enumerate(lines):
        y = bottom - (len(lines) - 1 - i) * T.T_ERROR_WORD.size
        p.text(line, T.PAD_X, y, T.T_ERROR_WORD, T.WHITE)

    base = T.BAND_H + (T.HEIGHT - T.BAND_H) // 2 + 10
    p.text(txt["connecting"], T.PAD_X, base, T.T_ERROR_SUB, T.ERROR_SUB)
    return T.LED_OFF
