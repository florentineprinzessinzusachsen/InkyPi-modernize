"""SVG generation for the rain-map location marker + wind trajectory arrow.

Geometry ported from the Pillow-based renderer this plugin used to have
(rasterizing to a PIL.Image) - it now emits SVG markup instead, so Chrome
(via BasePlugin.render_image) does the actual painting/scaling instead of
Pillow. Coordinates are in the SAME viewBox space as render/germany_bkg.svg
and render/germany_borders.svg (936x1026 - see MAP_VIEWBOX_W/H), letting
this get composited as one <svg> in the plugin's HTML template.

Colors decoded from the app's own Paint.setColor calls, same as before:
location marker RGB(36,35,172), trajectory line/crossbar/arrowhead
RGB(76,76,76), label badge fill RGBA(204,204,204,243) with RGBA(51,51,51,243)
stroke, label text black.
"""

from __future__ import annotations

import math
from html import escape

MAP_VIEWBOX_W = 936
MAP_VIEWBOX_H = 1026

# germany_bkg.svg/germany_borders.svg's paths don't reach the edges of the
# full 936x1026 box above (measured via a headless-Chromium getBBox() pass
# over both files' combined paths: ink spans roughly x=[139,810],
# y=[87,999]) - composited at 0 0 936 1026 as before, that dead margin
# reads as misaligned/off-center whitespace once the map is placed in a
# tight container (e.g. a narrow grid column) rather than a large
# letterboxed panel. This is a second, independent crop window into the
# SAME coordinate space (~10px padding around the measured ink) - every
# other coordinate in this module (location marker, trajectory, and the
# rain-image overlay drawn at 0 0 MAP_VIEWBOX_W MAP_VIEWBOX_H) stays in the
# original 936x1026 space; only the SVG's outer viewBox attribute (in the
# consuming template) should use these instead of "0 0 936 1026".
MAP_CROP_X = 130
MAP_CROP_Y = 75
MAP_CROP_W = 690
MAP_CROP_H = 935

LOCATION_COLOR = "rgb(36,35,172)"
TRAJECTORY_COLOR = "rgb(76,76,76)"
LABEL_BADGE_FILL = "rgba(204,204,204,0.95)"
LABEL_BADGE_STROKE = "rgba(51,51,51,0.95)"
LABEL_TEXT_COLOR = "rgb(0,0,0)"


def _arrowhead_svg(tail: tuple, head: tuple, width: float = 3.0, head_len: float = 14.0,
                    show_wings: bool = True) -> str:
    """Line from tail to head, optionally with a chevron/wings AT head
    (pointing toward it) - matches the app's trajectory chevron,
    converging on the location point rather than the far end.

    show_wings=False draws just the plain line - for callers where the
    chevron reads as an unwanted triangle at this rendered size rather
    than a legible arrowhead (weather_de's small map column)."""
    x1, y1 = tail
    x2, y2 = head
    parts = [f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
             f'stroke="{TRAJECTORY_COLOR}" stroke-width="{width}" stroke-linecap="round"/>']
    if show_wings:
        theta = math.atan2(y2 - y1, x2 - x1)
        for sign in (-1, 1):
            a = theta + sign * math.radians(150)
            hx, hy = x2 + math.cos(a) * head_len, y2 + math.sin(a) * head_len
            parts.append(f'<line x1="{x2:.1f}" y1="{y2:.1f}" x2="{hx:.1f}" y2="{hy:.1f}" '
                         f'stroke="{TRAJECTORY_COLOR}" stroke-width="{width}" stroke-linecap="round"/>')
    return "\n".join(parts)


_LABEL_FONT_SIZE = 30  # viewBox units - see module docstring; larger for readability

def _label_badge_svg(cx: float, cy: float, text: str) -> str:
    """Rounded-rect badge behind an hour label."""
    # Rough text-width estimate (no DOM measurement available server-side) -
    # generous enough for "+Nh" strings at this font size.
    text_w = _LABEL_FONT_SIZE * 0.53 * len(text) + 4
    text_h = _LABEL_FONT_SIZE * 0.9
    pad_x, pad_y = 9, 6
    rx, ry = cx - text_w / 2 - pad_x, cy - text_h / 2 - pad_y
    w, h = text_w + pad_x * 2, text_h + pad_y * 2
    radius = h / 2
    return (
        f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius:.1f}" '
        f'fill="{LABEL_BADGE_FILL}" stroke="{LABEL_BADGE_STROKE}" stroke-width="1.5"/>'
        f'<text x="{cx:.1f}" y="{cy:.1f}" fill="{LABEL_TEXT_COLOR}" font-size="{_LABEL_FONT_SIZE}" '
        f'font-family="Jost, sans-serif" text-anchor="middle" dominant-baseline="central">{escape(text)}</text>'
    )


def render_trajectory_svg(loc_x: float, loc_y: float, u: float, v: float,
                           scale_x: float, scale_y: float, hours: int = 4, unit_scale: float = 4.0,
                           show_labels: bool = True, stroke_width: float = 3.0,
                           crossbar_length_scale: float = 1.0, show_arrowhead: bool = True) -> str:
    """Backward wind trajectory: where the air at (loc_x, loc_y) was N hours
    ago, per hour, drawn as a line from the oldest position to the location
    with the arrowhead at the location. Formula unchanged from the original
    Pillow renderer: far_point = location - (U,V)*unit_scale*hours, with
    V's screen-Y contribution negated (SVG/image Y grows downward,
    geographic North is -Y).

    show_labels=False keeps the perpendicular hour-tick crossbars but skips
    the "+Nh" text badges - for callers that composite this map much
    smaller than the standalone Regenalarm plugin's own panel (e.g.
    weather_de's map column), where the badge text shrinks past legible.

    stroke_width applies uniformly to the main line and the crossbars -
    both are computed in this module's fixed 936x1026 viewBox space, so a
    caller that renders that same viewBox much smaller on screen (again,
    weather_de's map column) needs a bigger stroke_width to stay visible
    at all, not just thicker at the same tiny size. crossbar_length_scale
    independently scales just the perpendicular crossbar ticks (bump
    unit_scale instead to lengthen the main line/trajectory itself).
    show_arrowhead=False drops the chevron wings at the line's head,
    keeping just the plain line - the chevron can read as a stray
    triangle rather than a legible arrowhead at a small rendered size."""
    dx_per_hour = u * unit_scale * scale_x
    dy_per_hour = -v * unit_scale * scale_y
    far_x = loc_x - dx_per_hour * hours
    far_y = loc_y - dy_per_hour * hours

    parts = [_arrowhead_svg((far_x, far_y), (loc_x, loc_y), width=stroke_width, head_len=14.0 * (stroke_width / 3.0),
                            show_wings=show_arrowhead)]

    line_len = math.hypot(dx_per_hour, dy_per_hour)
    if line_len > 1e-6:
        perp_x, perp_y = -dy_per_hour / line_len, dx_per_hour / line_len
    else:
        perp_x, perp_y = 0.0, -1.0

    # Which of the two perpendicular directions the labels go on is decided
    # once for the whole trajectory, from the sign of the overall line's
    # delta - not per tick.
    dx_total, dy_total = far_x - loc_x, far_y - loc_y
    same_sign = (dx_total < 0) == (dy_total < 0)
    side = -1 if same_sign else 1

    avg_scale = (scale_x + scale_y) / 2

    for k in range(1, hours + 1):
        tx = loc_x - dx_per_hour * k
        ty = loc_y - dy_per_hour * k

        dist_from_loc = line_len * k
        half_len = max(dist_from_loc * 0.23, k * 2.5 * avg_scale) * crossbar_length_scale
        cx1, cy1 = tx + perp_x * half_len, ty + perp_y * half_len
        cx2, cy2 = tx - perp_x * half_len, ty - perp_y * half_len
        parts.append(f'<line x1="{cx1:.1f}" y1="{cy1:.1f}" x2="{cx2:.1f}" y2="{cy2:.1f}" '
                     f'stroke="{TRAJECTORY_COLOR}" stroke-width="{stroke_width:.1f}"/>')

        if show_labels:
            label_offset = half_len + 16
            lx, ly = tx + perp_x * label_offset * side, ty + perp_y * label_offset * side
            parts.append(_label_badge_svg(lx, ly, f"+{k}h"))

    return "\n".join(parts)


def render_marker_and_trajectory(location_xy: tuple | None, location_uv: tuple | None,
                                  rain_native_size: tuple | None, show_labels: bool = True,
                                  unit_scale: float = 4.0, stroke_width: float = 3.0,
                                  crossbar_length_scale: float = 1.0, show_arrowhead: bool = True) -> str:
    """Scales location_xy/location_uv (native to the rain PNG's own pixel
    space) into this module's fixed MAP_VIEWBOX_W/H space, and returns the
    combined marker + trajectory SVG markup. Returns "" if there's nothing
    to draw (no location data). All other params are forwarded to
    render_trajectory_svg - see its docstring."""
    if location_xy is None or not rain_native_size:
        return ""

    native_w, native_h = rain_native_size
    scale_x = MAP_VIEWBOX_W / native_w if native_w else 1.0
    scale_y = MAP_VIEWBOX_H / native_h if native_h else 1.0
    loc_x = location_xy[0] * scale_x
    loc_y = location_xy[1] * scale_y

    r = max(4, round(min(MAP_VIEWBOX_W, MAP_VIEWBOX_H) * 0.006))
    parts = [f'<circle cx="{loc_x:.1f}" cy="{loc_y:.1f}" r="{r}" fill="{LOCATION_COLOR}"/>']

    if location_uv is not None:
        parts.append(render_trajectory_svg(loc_x, loc_y, location_uv[0], location_uv[1], scale_x, scale_y,
                                            unit_scale=unit_scale, show_labels=show_labels,
                                            stroke_width=stroke_width, crossbar_length_scale=crossbar_length_scale,
                                            show_arrowhead=show_arrowhead))

    return "\n".join(parts)
