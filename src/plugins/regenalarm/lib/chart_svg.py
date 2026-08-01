"""SVG generation for the intensity (bars, blue) / probability (line, red)
chart. Geometry ported from this plugin's earlier Pillow-based renderer -
same clamp/compression math, same Catmull-Rom spline, same x-axis label
thinning - emitting SVG markup instead of rasterizing, so Chrome paints
(and scales) it instead of Pillow.

The viewBox is set to the CALLER'S actual panel size in pixels (see
regenalarm.py's _layout_px), not a generic "design" box that then gets
scaled up/down to fit - that's what "full height, no letterboxing" means
here: the chart fills its panel exactly because it was drawn at that exact
size, not because of any CSS trick. It's also why the font sizes below can
be plain literal pixel values instead of viewBox-relative guesses: 1
viewBox unit == 1 real screen pixel.
"""

from __future__ import annotations

import math

BG_COLOR = "white"
GRID_COLOR = "rgb(222,222,222)"
AXIS_COLOR = "rgb(90,90,90)"
TEXT_COLOR = "rgb(60,60,60)"
BAR_COLOR = "rgb(30,100,220)"    # Intensity - blue
LINE_COLOR = "rgb(210,30,30)"    # Probability - red


def _intensity_fraction(raw: float) -> float:
    """Clamps raw intensity into [0, 300] with a compression above 200
    (raw > 200 -> raw*0.5 + 100, capped at 300), giving a fraction =
    clamped/300 of full bar height."""
    v = raw
    if v > 200.0:
        v = v * 0.5 + 100.0
        if v > 300.0:
            v = 300.0
    elif v < 0.0:
        v = 0.0
    return v / 300.0


def _catmull_rom_points(pts: list, samples_per_seg: int = 16) -> list:
    """Samples a uniform Catmull-Rom spline through pts - a C1-continuous
    curve through every data point, without a bespoke matrix solver."""
    if len(pts) < 3:
        return pts
    ext = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        steps = samples_per_seg if i < len(ext) - 3 else samples_per_seg + 1
        for s in range(steps):
            t = s / samples_per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    return out


def _text_width_estimate(text: str, font_size: float) -> float:
    """Rough monospace-ish width estimate (no DOM measurement available
    server-side) - generous enough for the "H:MM"/"NN%" labels used here."""
    return font_size * 0.6 * len(text)


def render_chart_svg(intensities: list, probabilities: list, reference_time_minutes: int, interval: int,
                      width: int, height: int) -> str:
    """width/height are the exact panel size in pixels (see module
    docstring) - required, not optional, since sizing correctness (no
    letterboxing) depends on the viewBox matching the real box exactly."""
    n = min(len(intensities), len(probabilities))
    intensities, probabilities = intensities[:n], probabilities[:n]
    if n == 0:
        return ""

    # Font sizes are literal pixels (viewBox == real box, see module
    # docstring) - sized generously ("readable" = large, not just
    # proportionally-technically-correct) with a floor so small panels
    # don't go illegible. Derived from height, but capped relative to
    # width too - a tall, narrow panel (e.g. this plugin's map/chart
    # panels in a portrait/vertical device orientation) would otherwise
    # get a height-driven font size wide enough to overflow its own
    # narrow width.
    base_dim = min(height, width * 1.6)
    # Axis (x/y) labels are deliberately much smaller than the legend text -
    # ~30% of a "readable" size, since these repeat many times across the
    # plot (one per gridline/data point) and being large was both visually
    # heavy and self-defeating: bigger label text forces more aggressive
    # x-axis thinning (see label_indices below), so fewer time labels fit.
    label_font_size = max(10, round(base_dim * 0.055 * 0.4))
    legend_font_size = max(13, round(base_dim * 0.06 * 0.8))
    axis_stroke_w = max(2, round(height * 0.004))
    grid_stroke_w = max(1, round(height * 0.002))

    pct_labels = ["100%", "75%", "50%", "25%", "0%"]
    max_label_w = max(_text_width_estimate(t, label_font_size) for t in pct_labels)
    left = max_label_w + round(width * 0.02) + 8
    top = round(height * 0.05)
    right = width - round(width * 0.02)
    # Bottom margin sized to exactly fit the x-axis time-label row plus a
    # WORST-CASE two-row legend (see the legend section below - it stacks
    # into two rows if a single row wouldn't fit plot_w), computed from
    # the actual font sizes rather than a guessed height fraction, so it
    # can't run out of room and clip text at any aspect ratio.
    bottom_reserve = round(height * 0.045) + round(label_font_size * 0.7) + round(legend_font_size * 2.7) + round(height * 0.02)
    bottom = height - bottom_reserve

    plot_w = right - left
    plot_h = bottom - top

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'font-family="Jost, sans-serif">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{BG_COLOR}"/>')

    # Horizontal gridlines + y-axis percentage labels.
    for i, label in enumerate(pct_labels):
        y = top + plot_h * i / (len(pct_labels) - 1)
        parts.append(f'<line x1="{left:.1f}" y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" '
                     f'stroke="{GRID_COLOR}" stroke-width="{grid_stroke_w}"/>')
        parts.append(f'<text x="{left - 8:.1f}" y="{y:.1f}" fill="{TEXT_COLOR}" font-size="{label_font_size}" '
                     f'text-anchor="end" dominant-baseline="central">{label}</text>')

    # Vertical gridlines, one per data point.
    for j in range(n):
        x = left + (plot_w * j / (n - 1) if n > 1 else 0)
        parts.append(f'<line x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" '
                     f'stroke="{GRID_COLOR}" stroke-width="{grid_stroke_w}"/>')

    # Axis border lines.
    parts.append(f'<line x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{bottom:.1f}" '
                 f'stroke="{AXIS_COLOR}" stroke-width="{axis_stroke_w}"/>')
    parts.append(f'<line x1="{left:.1f}" y1="{bottom:.1f}" x2="{right:.1f}" y2="{bottom:.1f}" '
                 f'stroke="{AXIS_COLOR}" stroke-width="{axis_stroke_w}"/>')

    # Bars (intensity) - shares the line's edge-to-edge x spacing so both
    # series land on the same x per time index.
    slot_w = plot_w / n if n else 0
    bar_w = slot_w * (2 / 3)
    for j, raw in enumerate(intensities):
        x = left + (plot_w * j / (n - 1) if n > 1 else plot_w / 2)
        frac = _intensity_fraction(float(raw))
        bar_h = plot_h * frac
        parts.append(f'<rect x="{x - bar_w / 2:.1f}" y="{bottom - bar_h:.1f}" width="{bar_w:.1f}" '
                     f'height="{bar_h:.1f}" fill="{BAR_COLOR}"/>')

    # Line (probability), Catmull-Rom smoothed.
    pts = []
    for j, p in enumerate(probabilities):
        v = max(0.0, min(100.0, float(p)))
        x = left + (plot_w * j / (n - 1) if n > 1 else plot_w / 2)
        y = bottom - plot_h * v / 100.0
        pts.append((x, y))
    smooth = _catmull_rom_points(pts)
    line_w = max(3, round(height * 0.006))
    if len(smooth) >= 2:
        path_d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in smooth)
        parts.append(f'<path d="{path_d}" fill="none" stroke="{LINE_COLOR}" stroke-width="{line_w}" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
    dot_r = max(3, round(height * 0.006))
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{dot_r}" fill="{LINE_COLOR}"/>')

    # X-axis "H:MM" time labels, thinned to fit the available per-point
    # spacing (first/last always shown, right/left aligned so they don't
    # spill past the plot edges).
    point_spacing = plot_w / (n - 1) if n > 1 else plot_w
    sample_label_w = _text_width_estimate("00:00", label_font_size)
    step = max(1, math.ceil((sample_label_w * 1.4) / point_spacing)) if point_spacing else 1
    label_indices = set(range(0, n, step))
    if n - 1 not in label_indices:
        prior_shown = [i for i in label_indices if i < n - 1]
        if prior_shown and (n - 1 - max(prior_shown)) < step:
            label_indices.discard(max(prior_shown))
        label_indices.add(n - 1)

    label_y = bottom + round(height * 0.045) + label_font_size * 0.7
    t = reference_time_minutes
    for j in range(n):
        x = left + (plot_w * j / (n - 1) if n > 1 else plot_w / 2)
        if j in label_indices:
            anchor = "start" if j == 0 else ("end" if j == n - 1 else "middle")
            parts.append(f'<text x="{x:.1f}" y="{label_y:.1f}" fill="{TEXT_COLOR}" '
                         f'font-size="{label_font_size}" text-anchor="{anchor}">{t // 60}:{t % 60:02d}</text>')
        t = (t + interval) % 1440

    # Legend - a centered row below the x-axis labels (not a top-right
    # corner overlay): stays clear of the plot at any aspect ratio,
    # including a narrow/tall panel where a corner legend would collide
    # with the y-axis labels. Shrinks to fit plot_w if needed, falling
    # back to two stacked rows if it still wouldn't fit even at the
    # smallest readable size (an extremely narrow panel).
    def _legend_geometry(font_size):
        sw = max(12, round(font_size * 0.8))
        pad = sw * 0.6
        gap = sw * 2.2
        iw = _text_width_estimate("Intensity", font_size)
        pw = _text_width_estimate("Probability", font_size)
        return sw, pad, gap, iw, pw, (sw + pad + iw) + gap + (sw + pad + pw)

    swatch, item_pad, group_gap, intensity_w, probability_w, total_w = _legend_geometry(legend_font_size)
    if total_w > plot_w:
        legend_font_size = max(11, round(legend_font_size * plot_w / total_w * 0.95))
        swatch, item_pad, group_gap, intensity_w, probability_w, total_w = _legend_geometry(legend_font_size)

    legend_y = label_y + legend_font_size * 1.3
    if total_w <= plot_w:
        gx = left + max(0, (plot_w - total_w) / 2)
        parts.append(f'<rect x="{gx:.1f}" y="{legend_y - swatch / 2:.1f}" width="{swatch}" height="{swatch}" fill="{BAR_COLOR}"/>')
        parts.append(f'<text x="{gx + swatch + item_pad:.1f}" y="{legend_y:.1f}" fill="{TEXT_COLOR}" '
                     f'font-size="{legend_font_size}" dominant-baseline="central">Intensity</text>')
        gx2 = gx + swatch + item_pad + intensity_w + group_gap
        parts.append(f'<line x1="{gx2:.1f}" y1="{legend_y:.1f}" x2="{gx2 + swatch:.1f}" y2="{legend_y:.1f}" '
                     f'stroke="{LINE_COLOR}" stroke-width="{line_w}"/>')
        parts.append(f'<text x="{gx2 + swatch + item_pad:.1f}" y="{legend_y:.1f}" fill="{TEXT_COLOR}" '
                     f'font-size="{legend_font_size}" dominant-baseline="central">Probability</text>')
    else:
        row1_w = swatch + item_pad + intensity_w
        row2_w = swatch + item_pad + probability_w
        gx1 = left + max(0, (plot_w - row1_w) / 2)
        gx2 = left + max(0, (plot_w - row2_w) / 2)
        legend_y2 = legend_y + legend_font_size * 1.4
        parts.append(f'<rect x="{gx1:.1f}" y="{legend_y - swatch / 2:.1f}" width="{swatch}" height="{swatch}" fill="{BAR_COLOR}"/>')
        parts.append(f'<text x="{gx1 + swatch + item_pad:.1f}" y="{legend_y:.1f}" fill="{TEXT_COLOR}" '
                     f'font-size="{legend_font_size}" dominant-baseline="central">Intensity</text>')
        parts.append(f'<line x1="{gx2:.1f}" y1="{legend_y2:.1f}" x2="{gx2 + swatch:.1f}" y2="{legend_y2:.1f}" '
                     f'stroke="{LINE_COLOR}" stroke-width="{line_w}"/>')
        parts.append(f'<text x="{gx2 + swatch + item_pad:.1f}" y="{legend_y2:.1f}" fill="{TEXT_COLOR}" '
                     f'font-size="{legend_font_size}" dominant-baseline="central">Probability</text>')

    parts.append("</svg>")
    return "\n".join(parts)
