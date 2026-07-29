"""identify_leds.py -- work out which LED index is where on the back.

    mpremote connect /dev/cu.usbmodemXXXX run device/identify_leds.py

Pimoroni does not document how the seven ambient LED indices map to physical
positions, so rather than guess, look. This lights each one on its own for two
seconds, in order, printing the index as it goes -- watch the back of the unit
and note which index lands in which corner.

Then it shows the pair currently configured in theme.LED_INDICES, so you can
confirm the corners are the ones actually lit.

Set theme.LED_INDICES to whatever you saw, and theme.LED_BRIGHTNESS to taste.
"""

import time

import presto as presto_mod
import theme as T

p = presto_mod.Presto(full_res=True, ambient_light=False)
display = p.display
p.set_backlight(1.0)

WHITE = display.create_pen(255, 255, 255)
BLACK = display.create_pen(0, 0, 0)


def all_off():
    for i in range(presto_mod.Presto.NUM_LEDS):
        p.set_led_rgb(i, 0, 0, 0)


def banner(line):
    display.set_pen(BLACK)
    display.clear()
    display.set_pen(WHITE)
    display.text(line, 20, 110, 440, 4)
    p.update()


print("Watch the back of the Presto.\n")
print("%d LEDs, indices 0..%d" % (presto_mod.Presto.NUM_LEDS,
                                  presto_mod.Presto.NUM_LEDS - 1))

for i in range(presto_mod.Presto.NUM_LEDS):
    all_off()
    p.set_led_rgb(i, 255, 255, 255)
    banner("LED %d" % i)
    print("  LED %d" % i)
    time.sleep(2)

all_off()
banner("configured")
print("\nNow lighting theme.LED_INDICES = %r at %d%% brightness."
      % (list(T.LED_INDICES), int(T.LED_BRIGHTNESS * 100)))
print("These should be the two corners. If not, edit theme.LED_INDICES.")

r = int(255 * T.LED_BRIGHTNESS)
for i in T.LED_INDICES:
    p.set_led_rgb(i, r, r, r)
time.sleep(6)

all_off()
banner("done")
print("\nDone -- LEDs off. Press RESET to return to the app.")
