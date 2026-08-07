"""Dev-only tool: regenerates two files -
  dev_preview_inner.html - the actual render of weather_de.html, using the
    plugin's real rendering pipeline (same Jinja template, same fonts as
    production). LINKS to the real CSS files (plugin.css, weather_de.css)
    via RELATIVE paths (not file:// absolute paths - those would bake this
    machine's home directory/username into a committed file) rather than
    inlining a frozen copy - so editing render/weather_de.css and reloading
    applies the change immediately, no need to re-run this script for
    CSS-only tweaks.
  dev_preview.html - a thin wrapper that loads dev_preview_inner.html inside
    a <iframe width=800 height=480>. Open THIS one, not the inner file
    directly: weather_de.css sizes things in dvh/vh, which bind to the
    actual browser viewport, not to any parent element - opening the inner
    file directly and resizing your browser window to "800x480" never gives
    an exact 800x480 *content* viewport (tab bar/address bar/window borders
    all eat into it unpredictably), which is what causes dvh-based heights
    to come out wrong (the large-whitespace/content-pushed-to-top symptom).
    An iframe is its own browsing context with its own independent
    viewport for vh/dvh purposes, sized by the iframe element's own fixed-
    pixel CSS box - that's what actually makes this match the production
    Chromium screenshot pipeline's exact, chrome-less viewport.

Every filesystem reference in the output (CSS, fonts, icons, chart.js) is
rewritten to a path relative to the two output files, both of which live
in this same directory - so nothing in either file reveals this machine's
absolute filesystem path/username, and both stay valid from any checkout
of this repo without edits.

Re-run this script only when you want fresh random weather data (or a
fresh live Regenalarm fetch) - not for CSS-only tweaks.

Run from the repo root:
    PYTHONPATH=src .venv/bin/python src/plugins/weather_de/dev_preview_generate.py
Then open src/plugins/weather_de/dev_preview.html directly in a browser.

`settings` below is a snapshot of frame's actual live weather_de instance
(pulled from its device.json) - update it by hand if you change that
instance's settings and want the preview to keep matching. Weather values
themselves (temperature, wind, forecast, hourly data) are randomized on
each run but flow through the plugin's real parse_bright_sky_data() etc.,
so units/icons/labels/day-grouping all match production exactly - only the
underlying numbers are fake. Regenalarm rain/map data is fetched for real
(public, keyless API), since it's what the graph's x-axis-bucketing fix is
actually exercising.

This script and its two output files (dev_preview.html, dev_preview_inner.html)
are committed - the path-rewriting above keeps them machine-independent, so
the preview is viewable straight from a checkout without re-running anything.
Re-run only when you want the weather numbers refreshed.
"""
import sys
import os
import random
import pathlib

sys.path.insert(0, "src")
os.environ.setdefault("INKYPI_ENV", "dev")

from datetime import datetime, timedelta  # noqa: E402

from utils.time_utils import get_timezone  # noqa: E402
from utils.app_utils import get_fonts, resolve_path  # noqa: E402
from plugins.plugin_registry import get_plugin_instance  # noqa: E402
from plugins.weather_de.weather_de import BRIGHT_SKY_ICON_MAP  # noqa: E402

# --- Live settings, copied verbatim from frame's device.json
#     (playlist_config -> playlists[0] -> plugins -> plugin_id=weather_de) ---
settings = {
    "language": "de",
    "latitude": "52.53474856220323",
    "longitude": "13.41928482055664",
    "weatherProvider": "BrightSky",
    "units": "metric",
    "rainDataSource": "regenalarm",
    "displayRegenalarmMap": "true",
    "customTitle": "",
    "displayRefreshTime": "false",
    "displayDate": "false",
    "displayMetrics": "true",
    "displayGraph": "true",
    "displayRain": "true",
    "moonPhase": "false",
    "displayGraphIcons": "false",
    "displayForecast": "true",
    "forecastDays": "7",
    "selectedFrame": "None",
    "topMargin": "",
    "bottomMargin": "",
    "leftMargin": "",
    "rightMargin": "",
    "backgroundOption": "color",
    "backgroundColor": "#ffffff",
    "textColor": "#000000",
    "instance_name": "Wetter",
}
# Live device-level settings (frame's device.json top level)
TIME_FORMAT = "24h"
TIMEZONE_NAME = "Europe/Berlin"
RESOLUTION = (800, 480)

lat = float(settings["latitude"])
long = float(settings["longitude"])
units = settings["units"]
language = settings["language"]
tz = get_timezone(TIMEZONE_NAME)

instance = get_plugin_instance({"id": "weather_de", "class": "WeatherDe"})

now = datetime.now(tz)

# --- Synthesize random but Bright-Sky-shaped raw hourly records, then run
#     them through the plugin's REAL parsing so every derived field
#     (units, icon mapping, day grouping, moon phase, wind arrows, ...)
#     matches production exactly. ---
icon_keys = list(BRIGHT_SKY_ICON_MAP.keys())
start = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
hourly_records = []
temp = random.uniform(14, 24)
for i in range(9 * 24):  # 9 days hourly - comfortably covers forecastDays=7
    dt = start + timedelta(hours=i)
    temp += random.uniform(-1.2, 1.2)
    temp = max(-5.0, min(35.0, temp))
    is_rain_hour = random.random() < 0.25
    hourly_records.append({
        "timestamp": dt.isoformat(),
        "icon": random.choice(icon_keys),
        "temperature": round(temp, 1),
        "precipitation": round(random.uniform(0, 3), 2) if is_rain_hour else 0.0,
        "precipitation_probability": random.randint(0, 100),
    })

current_rec = hourly_records[2]
current_data = {
    "weather": {
        "timestamp": current_rec["timestamp"],
        "icon": current_rec["icon"],
        "temperature": current_rec["temperature"],
        "wind_speed_10": round(random.uniform(2, 35), 1),
        "wind_direction_10": random.uniform(0, 360),
        "relative_humidity": random.randint(30, 95),
        "pressure_msl": round(random.uniform(995, 1025), 1),
    }
}

aqi_times = [rec["timestamp"] for rec in hourly_records]
aqi_data = {
    "hourly": {
        "time": aqi_times,
        "uv_index": [round(random.uniform(0, 8), 1) for _ in aqi_times],
        "european_aqi": [round(random.uniform(5, 90), 1) for _ in aqi_times],
    }
}

template_params = instance.parse_bright_sky_data(
    current_data, hourly_records, aqi_data, tz, units, TIME_FORMAT, lat, long, language
)
template_params["title"] = settings.get("customTitle", "")

# --- Same post-processing generate_image() does after parse_*_data() ---
for hour in template_params.get("hourly_forecast", []):
    hour.setdefault("regen_intensity_pct", None)
    hour.setdefault("regen_probability", None)

use_regenalarm_rain = settings.get("rainDataSource", "provider") == "regenalarm"
show_regenalarm_map = settings.get("displayRegenalarmMap", "false") == "true"
regenalarm_map_ok = False
if use_regenalarm_rain or show_regenalarm_map:
    print("Fetching live Regenalarm data (real network call, keyless public API)...")
    regenalarm_data = instance._fetch_regenalarm(lat, long)
    if regenalarm_data is not None and not regenalarm_data.error_message:
        if use_regenalarm_rain:
            instance._merge_regenalarm_rain(template_params, regenalarm_data, tz)
        if show_regenalarm_map:
            regenalarm_map_ok = instance._add_regenalarm_map_params(template_params, regenalarm_data)
        print("  -> Regenalarm data merged OK.")
    else:
        print("  -> Regenalarm unavailable right now; falling back like production would.")
template_params["regenalarm_map_ok"] = regenalarm_map_ok

instance._extract_sun_times_for_graph(template_params, settings, tz)

template_params["plugin_settings"] = settings
template_params["static_dir"] = instance.to_file_url(resolve_path("static"))

last_refresh_time = (
    now.strftime("%Y-%m-%d %H:%M") if TIME_FORMAT == "24h" else now.strftime("%Y-%m-%d %I:%M %p")
)
template_params["last_refresh_time"] = last_refresh_time

# --- Same CSS/font/dimension wiring render_image() does, EXCEPT CSS is
#     linked (file:// <link>), not inlined - plugin.html only inlines when
#     `inline_styles` is set (see its `{% if inline_styles %}` branch), so
#     leaving it unset makes it fall through to `style_sheets` <link> tags
#     instead. That's the whole point of this script: edit
#     render/weather_de.css and just reload the browser tab, no re-run
#     needed. Production's real render_image() always inlines (a screenshot
#     can't follow a live file edit anyway), so this is a deliberate
#     divergence from it, only here. All of these come out as file://
#     absolute paths at this point - rewritten to relative paths in one
#     pass below, right before writing the file out. ---
css_files = instance._build_css_files("weather_de.css", [])
template_params["style_sheets"] = [instance.to_file_url(p) for p in css_files]
template_params["width"] = RESOLUTION[0]
template_params["height"] = RESOLUTION[1]

fonts = get_fonts()
for f in fonts:
    if isinstance(f, dict):
        f["url"] = instance.to_file_url(f.get("url", ""))
template_params["font_faces"] = fonts

html = instance._render_template("weather_de.html", template_params)

out_dir = os.path.dirname(__file__)
inner_path = os.path.join(out_dir, "dev_preview_inner.html")

# --- Rewrite every absolute filesystem reference (file:// CSS/font/script
#     links AND bare-path <img> srcs, e.g. weather/moon icons - those never
#     go through to_file_url() even in production, since production only
#     ever screenshots this HTML, never ships it) to a path relative to
#     inner_path's own directory. Both output files live in this same
#     directory, so nothing here should ever reveal this machine's home
#     directory/username, and the files stay correct from any checkout of
#     this repo without hand-editing. file:// form must be replaced BEFORE
#     the bare form (which is a substring of it) or the file:// URLs would
#     end up mangled into "file://<relative path>". ---
repo_root = str(pathlib.Path(__file__).resolve().parents[3])
rel_to_root = os.path.relpath(repo_root, os.path.abspath(out_dir)).replace(os.sep, "/")
rel_prefix = "" if rel_to_root in (".", "") else rel_to_root + "/"
html = html.replace(f"file://{repo_root}/", rel_prefix)
html = html.replace(repo_root + "/", rel_prefix)

leaked = repo_root if repo_root in html else None
if leaked:
    raise RuntimeError(
        f"Relative-path rewrite missed an occurrence of {leaked!r} - "
        "inspect the output before opening it, it may still leak this machine's path."
    )

with open(inner_path, "w", encoding="utf-8") as f:
    f.write(html)

# weather_de.css sizes things in dvh/vh - viewport-relative units that bind
# to the actual BROWSER viewport, not to any parent element's box. Opening
# the inner render directly and resizing the browser window to "800x480"
# never gives an exact 800x480 *content* viewport (tab bar/address bar/
# window borders/OS chrome all eat into it by a varying, OS/browser-
# dependent amount), so every dvh height comes out wrong - that's the
# large-whitespace/content-pushed-to-top symptom. An <iframe> is its own
# browsing context with its own independent viewport for vh/dvh purposes,
# sized by the iframe element's own box - fixing that box at exactly
# 800x480px via CSS (not vh/vw, which would have the identical problem one
# level up) makes the inner content see a real 800x480 viewport regardless
# of the outer window/tab size, matching the production Chromium
# screenshot pipeline (which also renders into an exact, chrome-less
# viewport) instead of approximating it.
wrapper_path = os.path.join(out_dir, "dev_preview.html")
wrapper_html = f"""<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<title>weather_de dev preview ({RESOLUTION[0]}x{RESOLUTION[1]})</title>
<style>
  html, body {{
    margin: 0;
    min-height: 100%;
    background: #2b2b2b;
    font-family: sans-serif;
  }}
  body {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 16px;
    box-sizing: border-box;
  }}
  .frame-wrap {{
    box-shadow: 0 0 0 1px #000, 0 8px 24px rgba(0, 0, 0, 0.5);
    line-height: 0;
    flex: none;
  }}
  iframe {{
    display: block;
    width: {RESOLUTION[0]}px;
    height: {RESOLUTION[1]}px;
    border: 0;
  }}
  .label {{
    color: #999;
    font-size: 12px;
  }}
</style>
</head>
<body>
  <div class="frame-wrap">
    <iframe src="dev_preview_inner.html" width="{RESOLUTION[0]}" height="{RESOLUTION[1]}"></iframe>
  </div>
  <div class="label">{RESOLUTION[0]}&times;{RESOLUTION[1]} fixed viewport (iframe) - resizing this browser window does not affect it. Edit render/weather_de.css and reload to see changes.</div>
</body>
</html>
"""
with open(wrapper_path, "w", encoding="utf-8") as f:
    f.write(wrapper_html)

print(f"\nWrote {inner_path} ({len(html)} bytes) - raw render, don't open this one directly")
print(f"Wrote {wrapper_path} - open THIS one (fixed {RESOLUTION[0]}x{RESOLUTION[1]} viewport regardless of window size)")
print(f"Current temp: {template_params.get('current_temperature')}{template_params.get('temperature_unit')}, "
      f"feels like {template_params.get('feels_like')}")
print(f"Forecast days generated: {len(template_params.get('forecast', []))}")
print(f"Hourly forecast points: {len(template_params.get('hourly_forecast', []))}")
print(f"Regenalarm map ok: {regenalarm_map_ok}")
