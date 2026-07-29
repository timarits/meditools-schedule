"""texts.py -- every string that reaches the screen, Dutch first.

Set "language": "en" in schedule.json to switch. Copy is short, imperative and
warm by design ("Eten mag", not "Voedselinname toegestaan") -- see
DESIGNER_BRIEF.md. Keys ending in _lines are pre-broken across screen lines by
the designer rather than wrapped at runtime.
"""

TEXTS = {
    "nl": {
        "no_eat": "NIET ETEN",
        "eat": "ETEN MAG",
        "eat_at_label": "Eten kan om",
        "eat_until_label": "Eten kan tot",
        "med_at_label": "Medicijn om",
        "med_at": "Medicijn om %s",
        "unit_min": "min",
        "unit_hour": "u",
        # What the big countdown is counting down to. The red window runs the
        # first one until the dose, then the second until eating is allowed.
        "until_med": "tot je medicijn",
        "until_eat": "tot je mag eten",
        "sleep": "SLAAPTIJD",
        "med_title": "MEDICIJN",
        "med_lines": ("Pak je", "medicijn"),
        "med_hold": "Houd het scherm vast",
        "taken": "GENOMEN!",
        "taken_sub": "%s \xb7 goed gedaan",
        "err_eyebrow": "GEEN TIJD",
        "no_clock_lines": ("KLOK NIET", "GEZET"),
        "connecting": "Verbinden met WiFi...",
    },
    "en": {
        "no_eat": "DO NOT EAT",
        "eat": "YOU CAN EAT",
        "eat_at_label": "Eating from",
        "eat_until_label": "Eating until",
        "med_at_label": "Medicine at",
        "med_at": "Medicine at %s",
        "unit_min": "min",
        "unit_hour": "h",
        "until_med": "until your medicine",
        "until_eat": "until you can eat",
        "sleep": "SLEEP TIME",
        "med_title": "MEDICINE",
        "med_lines": ("Take your", "medicine"),
        "med_hold": "Hold the screen",
        "taken": "TAKEN!",
        "taken_sub": "%s \xb7 well done",
        "err_eyebrow": "NO TIME",
        "no_clock_lines": ("CLOCK NOT", "SET"),
        "connecting": "Connecting to WiFi...",
    },
}


def for_language(code):
    return TEXTS.get(code, TEXTS["nl"])
