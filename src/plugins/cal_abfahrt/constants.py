"""Static tables for the Calendar + Departures plugin.

Kept out of cal_abfahrt.py so the hot path (generate_image) reads as data
flow rather than as pages of lookup tables.
"""

# Transit providers, copied from the abfahrtzeiten plugin - the free, keyless
# community HAFAS wrappers. The settings-page stop picker (initAbfahrtzeitenStops
# in static/scripts/plugin_schema.js) writes these same provider keys.
PROVIDER_BASES = {
    "vbb": "https://v6.vbb.transport.rest",
    "bvg": "https://v6.bvg.transport.rest",
    "db": "https://v6.db.transport.rest",
}
PROVIDER_LABELS = {"vbb": "VBB", "bvg": "BVG", "db": "DB"}

# Localization. Deliberately hand-rolled for two languages rather than pulled
# in via Babel (not a dependency here) or the `locale` module (process-global,
# and depends on locales actually being generated on the host - they usually
# aren't on a minimal Pi OS image). Only weekday abbreviations and four words
# are ever needed, since every other string on the panel comes from the
# calendar or the transit API.
LANGUAGES = {
    "de": {
        "weekdays": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "calendar": "Termine",
        "departures": "Abfahrten",
        "today": "Heute",
        "tomorrow": "Morgen",
        "all_day": "ganztg.",
        "until": "bis",
        "now": "jetzt",
        "min": "min",
        "line": "Linie",
        "direction": "Richtung",
        "stop": "Haltestelle",
        "leaves": "ab",
        "no_events": "Keine anstehenden Termine",
        "no_departures": "Keine Abfahrten",
        "unavailable": "Nicht verfügbar",
    },
    "en": {
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "calendar": "Agenda",
        "departures": "Departures",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "all_day": "all day",
        "until": "until",
        "now": "now",
        "min": "min",
        "line": "Line",
        "direction": "Direction",
        "stop": "Stop",
        "leaves": "dep",
        "no_events": "No upcoming events",
        "no_departures": "No departures",
        "unavailable": "Unavailable",
    },
}

# Per-layout pixel metrics, used to decide how much content to render.
#
# These are literal pixel costs measured against the real 800x480 Chromium
# render of each stylesheet, not guesses - see the matching comments in
# render/cal_abfahrt_board.css / _grid.css. Deciding this in Python (rather
# than rendering everything and letting overflow:hidden clip) is what keeps a
# row from being sliced through its middle, which reads as a rendering bug
# rather than a deliberate cut-off. There is no way to ask a headless
# screenshot "did it fit", so the fit has to be computed up front.
LAYOUT_METRICS = {
    "board": {
        # Calendar pane: chrome is the title rule + its margin.
        "cal_chrome": 24,
        "cal_day": 5,       # per-day padding + separator
        "cal_row": 17,      # per-event line
        # Departure pane: chrome is the title rule plus the column header.
        "dep_chrome": 43,
        # 20, not the 17px line-height: baseline-aligning the larger
        # countdown column against the rest grows the row's line box.
        "dep_row": 20,
    },
    "grid": {
        "cal_chrome": 26,
        "cal_day": 7,
        "cal_row": 16,
        # A day's stacked weekday/number chip is taller than a single event
        # line, so a one-event day costs more than cal_row alone.
        "cal_day_min_body": 22,
        # Departure grid: per-card chrome (header + borders) and the smallest
        # a stretched departure row may get before it stops being legible
        # from across the room - the entire point of this layout.
        "card_gap": 8,
        "card_chrome": 20,
        "dep_row_min": 22,
        "dep_row_max": 34,
    },
}
