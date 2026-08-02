"""Regenalarm plugin for InkyPi.

Rain dashboard for a single chosen German location: a composited Germany
radar map (outline + rain/cloud overlay + wind trajectory) plus an
intensity/probability-over-time chart. Sourced from a reverse-engineered
client for the (undocumented, unauthenticated) regenonline.de API - see
`lib/protocol.py` and `lib/forecast.py` for the full provenance notes.

Only the read-only `/rain/bin` fetch endpoint is ever used; the
crowdsourced `/rain/report` submission endpoint is intentionally not
implemented here (submitting synthetic "it's raining here" reports would
pollute the vendor's own data - see lib/ for what was and wasn't copied).

The upstream data itself refreshes roughly every 15 minutes - avoid setting
the playlist refresh interval for this plugin below that, to keep load on
an unauthenticated third-party endpoint reasonable (see settings.html).

Rendering: like weather/countdown/abfahrtzeiten, this plugin builds an HTML
page (render/regenalarm.html + .css) that Chrome paints and screenshots
(BasePlugin.render_image) - it does NOT use Pillow to rasterize anything
itself. The Germany map is a pre-converted static SVG (render/germany_*.svg
- see the one-time conversion this replaced, lib/mapdata.py + PIL-based
lib/render_view.py, both removed); the rain overlay is embedded as a data
URI straight from the fetched PNG bytes; the wind-trajectory marker and the
intensity/probability chart are generated as SVG markup (lib/map_svg.py,
lib/chart_svg.py) instead of rasterized pixels.

Panel sizing: the map/chart panels' own width+height are computed here in
Python as literal pixel values (_layout_px), not left to CSS dvh/dvw/flex-
grow. This isn't a stylistic choice - the specific chrome-headless-shell
build this renders through has a compositor bug where background-image/
img/inline-svg content painted inside an element sized via any dynamic
unit (dvh, %, flex-grow) silently renders at some unrelated fixed size,
even though the box's own layout (confirmed via getBoundingClientRect) is
correct. Elements sized with literal `px` don't hit this bug. Since the
target resolution is already known before rendering, computing the split
in Python costs nothing and sidesteps it entirely - everything else
(text sizing, the header, generic layout flow) still uses ordinary
CSS/dvh and remains fully responsive to whatever `dimensions` this plugin
is asked to render at.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.settings_schema import callout, schema, section, widget
import base64
import logging
import os
import struct
from datetime import datetime

import requests

from utils.http_client import get_http_session

from .lib.forecast import build_forecast_request, parse_forecast_response, extract_map_image
from .lib.map_svg import (
    MAP_VIEWBOX_W, MAP_VIEWBOX_H, MAP_CROP_X, MAP_CROP_Y, MAP_CROP_W, MAP_CROP_H,
    render_marker_and_trajectory,
)
from .lib.chart_svg import render_chart_svg

logger = logging.getLogger(__name__)

HOSTS = ["regenonline.de", "rainforecast.de", "regen.online", "regenvorschau.de"]
REQUEST_TIMEOUT = 15

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_DIR = os.path.join(PLUGIN_DIR, "render")

with open(os.path.join(RENDER_DIR, "germany_bkg.svg")) as f:
    _MAP_BKG_SVG = f.read()
with open(os.path.join(RENDER_DIR, "germany_borders.svg")) as f:
    _MAP_BORDERS_SVG = f.read()

# Same raw-intensity tier boundaries the chart itself uses for its
# intensity/color mapping (see lib/chart_svg.py) reused here to categorize
# the summary sentence.
_INTENSITY_HEAVY_THRESHOLD = 200.0
_INTENSITY_MEDIUM_THRESHOLD = 100.0


def _rain_category(raw):
    if raw > _INTENSITY_HEAVY_THRESHOLD:
        return "Starker"
    if raw > _INTENSITY_MEDIUM_THRESHOLD:
        return "Mittlerer"
    return "Leichter"


def _rain_summary(intensities, interval):
    """One-line German summary for the dashboard header: 'Kein Regen
    erwartet' if no forecast step has any measurable intensity, otherwise
    'Starker/Mittlerer/Leichter Regen in X Minuten' (or '... Regen jetzt'
    for the very first step)."""
    step = interval or 0
    for idx, raw in enumerate(intensities):
        if raw and raw > 0:
            minutes = idx * step
            category = _rain_category(float(raw))
            if minutes <= 0:
                return f"{category} Regen jetzt"
            return f"{category} Regen in {minutes} Minuten"
    return "Kein Regen erwartet"


def _png_size(data: bytes):
    """Width/height from a PNG's IHDR chunk, no image library needed:
    8-byte signature, 4-byte chunk length, 4-byte "IHDR", then 4+4 bytes
    of big-endian width/height."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


class Regenalarm(BasePlugin):
    def build_settings_schema(self):
        return schema(
            section(
                "Location",
                widget(
                    "weather-map",
                    template="widgets/weather_map.html",
                    # Default new instances to roughly the middle of Germany,
                    # rather than the weather-map widget's built-in NYC default.
                    config={"latitude": "51.1657", "longitude": "10.4515"},
                ),
                callout(
                    "Data refreshes roughly every 15 minutes upstream - avoid "
                    "setting the playlist refresh interval below ~15 minutes.",
                ),
            ),
        )

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        # Full-bleed composed dashboard - frame/background style options
        # don't apply to it, so they're not shown.
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        try:
            lat = float(settings.get('latitude'))
            lon = float(settings.get('longitude'))
        except (TypeError, ValueError):
            raise RuntimeError("A location is required. Pick one on the map in the plugin settings.")

        parsed = self._fetch_forecast(lat, lon)
        if parsed is None:
            raise RuntimeError("Unable to reach regenonline.de (or its fallback hosts) for rain data.")
        if parsed.error_message:
            raise RuntimeError(f"Regenalarm error: {parsed.error_message}")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        time_format = device_config.get_config("time_format", default="12h")
        template_params = {
            "plugin_settings": settings,
            "summary_text": _rain_summary(parsed.intensities or [], parsed.interval or 0),
            "last_refresh_time": self._format_now(time_format),
        }

        has_map = parsed.payload_blob is not None
        has_chart = bool(parsed.intensities and parsed.probabilities)
        if not has_map and not has_chart:
            raise RuntimeError("Regenalarm returned no usable rain data for this location.")

        # Map SVG rendering doesn't depend on the panel layout (it's a
        # scalable viewBox), so it's safe to try first and let the actual
        # outcome - not the pre-fetch has_map guess - drive the panel split.
        # Otherwise a map that fails after has_map=True leaves the chart
        # built at the "shared with a map" width even though no map panel
        # ends up rendering, i.e. an unexplained gap where the map would
        # have been instead of the chart filling the available width.
        map_ok = self._add_map_params(template_params, parsed) if has_map else False

        layout = self._layout_px(dimensions, map_ok, has_chart)
        template_params.update(layout)

        # The chart is built at its exact panel pixel size (layout["chart_w"] /
        # layout["content_h"]) so it fills the panel with no letterboxing -
        # see chart_svg.py's module docstring.
        chart_ok = self._add_chart_params(template_params, parsed, layout) if has_chart else False

        if map_ok and has_chart and not chart_ok:
            # Symmetric case: chart was expected (and the layout above split
            # the width for it) but its build failed for an unrelated reason
            # (e.g. malformed chart data) - give the map panel the full
            # width instead of leaving the same unexplained gap in reverse.
            layout = self._layout_px(dimensions, map_ok, False)
            template_params.update(layout)

        if not map_ok and not chart_ok:
            raise RuntimeError("Regenalarm returned no usable rain data for this location.")

        image = self.render_image(dimensions, "regenalarm.html", "regenalarm.css", template_params)
        if not image:
            raise RuntimeError("Failed to render Regenalarm image, please check logs.")
        return image

    def _fetch_forecast(self, lat, lon):
        """Tries each fallback host in order, first success wins. No
        published rate limit is available for this unauthenticated API, so
        this makes exactly one attempt per host per refresh - no retries."""
        body = build_forecast_request(lat, lon, wind_query=(1500, -1))
        for host in HOSTS:
            try:
                resp = get_http_session().post(
                    f"https://{host}/rain/bin", data=body, timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                return parse_forecast_response(resp.content)
            except (requests.exceptions.RequestException, OSError, TimeoutError) as e:
                logger.warning(f"Regenalarm: host {host} failed: {e}")
                continue
        return None

    def _add_map_params(self, template_params, parsed):
        if parsed.payload_blob is None:
            return False
        try:
            rain_png = extract_map_image(parsed.payload_blob)
            rain_native_size = _png_size(rain_png)
            template_params["map_bkg_svg"] = _MAP_BKG_SVG
            template_params["map_borders_svg"] = _MAP_BORDERS_SVG
            template_params["map_viewbox_w"] = MAP_VIEWBOX_W
            template_params["map_viewbox_h"] = MAP_VIEWBOX_H
            template_params["map_crop_x"] = MAP_CROP_X
            template_params["map_crop_y"] = MAP_CROP_Y
            template_params["map_crop_w"] = MAP_CROP_W
            template_params["map_crop_h"] = MAP_CROP_H
            template_params["rain_image_data_uri"] = "data:image/png;base64," + base64.b64encode(rain_png).decode("ascii")
            template_params["map_marker_svg"] = render_marker_and_trajectory(
                parsed.location_xy, parsed.location_uv, rain_native_size,
            )
            return True
        except Exception as e:
            logger.warning(f"Regenalarm: failed to build map SVG: {e}")
            return False

    def _add_chart_params(self, template_params, parsed, layout):
        try:
            template_params["chart_svg"] = render_chart_svg(
                parsed.intensities, parsed.probabilities,
                parsed.reference_time_minutes, parsed.interval,
                width=layout["chart_w"], height=layout["content_h"],
            )
            return True
        except Exception as e:
            logger.warning(f"Regenalarm: failed to build chart SVG: {e}")
            return False

    def _layout_px(self, dimensions, has_map, has_chart):
        """Literal pixel sizes for the header and the map/chart panels -
        see the module docstring's "Panel sizing" note for why this can't
        just be dvh/flex-grow in CSS. The map panel's width is derived
        from its own (portrait-ish) aspect ratio so it isn't letterboxed
        with wasted space; the chart panel takes the rest.

        h_pad/v_pad/v_gap deliberately mirror regenalarm.css's
        `.regenalarm-dashboard` padding (1.5dvh 1.5dvw) and gap (1dvh)
        exactly (dvh/dvw are viewport-relative, so height/width here are
        the same reference CSS uses) - content_h/content_w must match
        what CSS actually leaves for `.content`, or the panels (sized to
        content_h/content_w in literal px) end up taller than their real
        box and visually stick to one edge instead of filling it.

        header_h is likewise applied to `.header` itself as an explicit
        inline height in regenalarm.html (rather than left to its text's
        intrinsic size) - it MUST be, since content_h below is computed
        by subtracting it: if the browser rendered the header at some
        other, content-driven height instead, that difference would be
        unaccounted-for leftover space at the bottom of the page (below
        a fixed-size .content), pinning everything to the top - exactly
        the "chart sits at the top" bug this fixes.

        header_top_gap nudges the headline text down a little *within*
        header_h's existing box (applied as the header's own padding-top
        in regenalarm.html) - it must NOT be subtracted from content_h /
        added to the header's total footprint, or it just reproduces the
        same bug one level down: content_h would shrink and .content
        would get pushed lower to make room, moving the map/chart along
        with the headline when only the headline should move."""
        width, height = dimensions
        h_pad = round(width * 0.015)
        v_pad = round(height * 0.015)
        v_gap = round(height * 0.01)
        gap = round(width * 0.015)
        header_h = round(height * 0.12)
        header_top_gap = round(height * 0.012)  # the "move it down slightly" offset
        content_h = height - header_h - v_gap - 2 * v_pad
        content_w = width - 2 * h_pad

        layout = {"header_h": header_h, "header_top_gap": header_top_gap, "content_h": content_h, "gap": gap}
        if has_map and has_chart:
            # Aspect of the CROPPED viewBox (what's actually visible), not
            # the full 936x1026 asset - otherwise this still budgets width
            # for the dead margins the crop just removed.
            map_aspect = MAP_CROP_W / MAP_CROP_H
            ideal_map_w = content_h * map_aspect
            map_w = round(max(content_w * 0.32, min(ideal_map_w, content_w * 0.62)))
            layout["map_w"] = map_w
            layout["chart_w"] = content_w - map_w - gap
        elif has_map:
            layout["map_w"] = content_w
            layout["chart_w"] = 0
        elif has_chart:
            layout["map_w"] = 0
            layout["chart_w"] = content_w
        return layout

    def _format_now(self, time_format):
        now = datetime.now()
        return now.strftime("%H:%M") if time_format == "24h" else now.strftime("%I:%M %p")
