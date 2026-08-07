"""Abfahrtzeiten plugin for InkyPi.

Shows upcoming departures (with realtime delays/cancellations) for one or
more configured (stop, line, direction) entries, grouped by stop. Backed by
the free, keyless community HAFAS-wrapper REST APIs - see
`src/static/scripts/plugin_schema.js`'s `initAbfahrtzeitenStops` (and the
other `abfahrtzeiten*` helpers there) for the settings-page address/stop/line
picker that produces the saved `entries[]` this plugin reads. There is no
backend route for this - the picker calls the transit APIs directly from
the browser, per this fork's "no plugin-level backend routes" convention.

Recommended playlist refresh interval: 2-5 minutes. Departures are
time-sensitive, but each refresh issues only one HTTP request per unique
configured stop (not per line), well within the 100 req/min (burst 200)
budget of the BVG/VBB APIs.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.settings_schema import schema, section, row, field, widget
from utils.http_client import get_http_session
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import math
import os
from datetime import datetime
from urllib.parse import quote

logger = logging.getLogger(__name__)

PROVIDER_BASES = {
    "vbb": "https://v6.vbb.transport.rest",
    "bvg": "https://v6.bvg.transport.rest",
    "db": "https://v6.db.transport.rest",
}
PROVIDER_LABELS = {"vbb": "VBB", "bvg": "BVG", "db": "DB"}

DEPARTURES_DURATION_MIN = 60
DEPARTURES_RESULTS = 15  # starting pre-filter cap; a busy multi-line stop learns a bigger one, see below
MAX_FETCHED_DEPARTURES = 12  # raw per-stop fetch cap, ahead of display truncation below
# Ceiling for the learned per-stop `results` cap below - comfortably inside
# what the API already handles today (the settings wizard's stop/line picker
# queries at results=100, see abfahrtzeitenLinesForStop in plugin_schema.js).
DEPARTURES_RESULTS_CEILING = 80
REQUEST_TIMEOUT = 6  # kept short so a hung provider fails fast within the executor's 60s attempt budget

# Filename prefix for the on-disk "learned results cap" cache - see
# _load_learned_results's docstring.
_DEPARTURES_CACHE_PREFIX = "_abfahrtzeiten_departures_cache_"

# How many departures a (cols, grid_rows)-shaped row can show. Tuned by
# hand per shape rather than one formula, since "how many rows visually
# fit" depends on the font-size clamp in abfahrtzeiten.css in a way that
# doesn't reduce to a clean function of cols/rows alone. Falls back to a
# rough estimate for shapes nobody's eyeballed yet.
_TUNED_CAPACITY = {
    (1, 1): 9,   # 1 stop, full width/height
    (2, 1): 9,   # 2 stops side by side
    (3, 1): 10,  # 3 stops side by side (narrower cells -> smaller clamped font -> more rows fit)
    (2, 2): 5,   # 2x2 grid
}


def _capacity_for_row(cols, grid_rows):
    tuned = _TUNED_CAPACITY.get((cols, grid_rows))
    if tuned is not None:
        return tuned
    factor = 10 / 9 if cols == 3 else 1.0
    return max(3, round(9 * factor / grid_rows))


def _parse_entries(raw_entries):
    """Parses the settings' `entries[]` list of JSON strings into dicts,
    skipping any that fail to parse (e.g. hand-edited config)."""
    entries = []
    for raw in raw_entries or []:
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(f"Skipping unparseable Abfahrtzeiten entry: {raw!r}")
            continue
        if entry.get("provider") in PROVIDER_BASES and entry.get("stopId") and entry.get("lineName") and entry.get("direction"):
            entries.append(entry)
    return entries


def _group_by_stop(entries):
    """Groups parsed entries by (provider, stopId), preserving first-seen
    order, and collects the set of (lineName, direction) filters per stop."""
    groups = {}
    order = []
    for entry in entries:
        key = (entry["provider"], entry["stopId"])
        if key not in groups:
            groups[key] = {
                "provider": entry["provider"],
                "stopId": entry["stopId"],
                "stopName": entry.get("stopName") or entry["stopId"],
                "filters": set(),
            }
            order.append(key)
        groups[key]["filters"].add((entry["lineName"], entry["direction"]))
    return [groups[key] for key in order]


def _parse_when(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_cancelled(dep):
    if dep.get("cancelled"):
        return True
    for remark in dep.get("remarks") or []:
        if remark.get("type") == "cancellation":
            return True
    return False


def _departures_cache_path(device_config, provider, stop_id):
    digest = hashlib.sha256(f"{provider}:{stop_id}".encode()).hexdigest()[:32]
    return os.path.join(
        device_config.plugin_image_dir, f"{_DEPARTURES_CACHE_PREFIX}{digest}.json"
    )


def _load_learned_results(device_config, provider, stop_id):
    """Returns the learned `results` page size for one stop, or the default.

    A stop shared by several frequent lines (trams, buses) can crowd a
    less-frequent line out of the default DEPARTURES_RESULTS page entirely -
    the API returns its next N departures across every line at the stop, not
    per configured filter. This persists a per-stop page size that
    _fetch_stop_departures grows once it actually observes that
    DEPARTURES_RESULTS wasn't enough, so the fix applies only to stops that
    need it rather than paying a bigger page for every stop up front.
    Best-effort: a missing/corrupt file just means "start from the default
    again", same as any other cache miss.
    """
    try:
        with open(_departures_cache_path(device_config, provider, stop_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, AttributeError):
        return DEPARTURES_RESULTS
    value = data.get("results") if isinstance(data, dict) else None
    if not isinstance(value, int) or value <= 0:
        return DEPARTURES_RESULTS
    return min(value, DEPARTURES_RESULTS_CEILING)


def _store_learned_results(device_config, provider, stop_id, results):
    try:
        path = _departures_cache_path(device_config, provider, stop_id)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f)
        os.replace(tmp_path, path)
    except (OSError, AttributeError) as e:
        logger.debug("Could not write departures cache for %s/%s: %s", provider, stop_id, e)


def _grow_results_cap(current):
    """Returns a bigger page size to try next time, capped at the ceiling."""
    return min(DEPARTURES_RESULTS_CEILING, max(current + 5, round(current * 1.5)))


def _group_into_rows(items, max_per_row=3):
    """Arranges items into a responsive grid: up to `max_per_row` side by
    side, wrapping to further rows for more, with each row's size as
    balanced as possible (not just "fill 3, dump the remainder in a
    trailing row") - e.g. 4 items -> [2, 2], not [3, 1]; 5 -> [3, 2];
    6 -> [3, 3]."""
    n = len(items)
    if n == 0:
        return []
    rows = math.ceil(n / max_per_row)
    base, remainder = divmod(n, rows)
    result = []
    idx = 0
    for r in range(rows):
        size = base + 1 if r < remainder else base
        result.append(items[idx:idx + size])
        idx += size
    return result


class Abfahrtzeiten(BasePlugin):
    def build_settings_schema(self):
        return schema(
            section(
                "Stops",
                widget("abfahrtzeiten-stops", template="widgets/abfahrtzeiten_stops.html"),
            ),
            section(
                "Display",
                row(
                    field(
                        "displayPlatform",
                        "checkbox",
                        label="Platform",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                        hint="Shows the departure platform/track (e.g. \"Gl. 3\") next to each departure, when the provider reports one.",
                    ),
                ),
            ),
        )

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        entries = _parse_entries(settings.get('entries[]'))
        if not entries:
            raise RuntimeError("At least one stop is required. Add a stop in the plugin settings.")

        stop_groups = _group_by_stop(entries)
        stops, any_succeeded, fetch_errors = self._fetch_all(stop_groups, device_config)

        if not any_succeeded:
            # All stops almost always fail for the same underlying reason (one
            # flaky/unreachable provider) - surface the first one so the
            # shared fallback-image sanitizer (utils/fallback_image.py) can
            # classify it (timeout, connection error, 404, ...) instead of
            # always falling through to its fully generic message.
            detail = fetch_errors[0] if fetch_errors else None
            message = "Unable to fetch departures from any configured stop"
            message += f": {detail}" if detail else ". Please try again later."
            raise RuntimeError(message)

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        time_format = device_config.get_config("time_format", default="12h")

        stop_rows = _group_into_rows(stops)
        # Each grid row eats into every stop's available vertical room, so
        # the more grid rows there are, the fewer departures a cell can
        # show without overflowing. Capacity is computed per ROW (not
        # once globally) because column count also matters: a 3-column
        # row's cells render smaller text (see abfahrtzeiten.css's cqw
        # clamp), so more departure rows fit in the same height than in a
        # 1- or 2-column row of the same grid.
        rendered_rows = []
        for row in stop_rows:
            capacity = _capacity_for_row(len(row), len(stop_rows))
            for stop in row:
                # Deliberately render one row past `capacity` and let CSS
                # clip it (.stop-section/.departure-rows: overflow:hidden)
                # instead of trying to size things to fit exactly - the
                # font-size clamp for narrow cells (abfahrtzeiten.css)
                # makes actual row heights a bit shorter than their
                # reserved "unit" height, so stopping at exactly
                # `capacity` rows left a sliver of empty padding at the
                # bottom. The overflowing extra row eats that leftover
                # space instead.
                stop["departures"] = stop["departures"][:capacity + 1]
            rendered_rows.append({"stops": row, "row_units": capacity + 1})

        template_params = {
            "stop_rows": rendered_rows,
            "plugin_settings": settings,
            "last_refresh_time": self._format_now(time_format),
        }
        image = self.render_image(dimensions, "abfahrtzeiten.html", "abfahrtzeiten.css", template_params)
        if not image:
            raise RuntimeError("Failed to render Abfahrtzeiten image, please check logs.")
        return image

    def _fetch_all(self, stop_groups, device_config):
        stops = []
        any_succeeded = False
        fetch_errors = []
        session = get_http_session()

        def fetch_one(group):
            try:
                return group, self._fetch_stop_departures(session, group, device_config), None
            except Exception as e:
                logger.warning(f"Abfahrtzeiten: departures fetch failed for stop {group['stopId']} ({group['provider']}): {e}")
                # Keep the raw exception text out of the per-stop dict (it's
                # too technical/long for the small in-template error slot -
                # see abfahrtzeiten.html's .stop-error) but hand it back so
                # generate_image() can use it if every stop ends up failing.
                return group, [], str(e)

        # Each stop is an independent request to a (often different)
        # transit API, previously fetched one at a time - with N stops
        # configured, total wait was N times the slowest provider's
        # latency instead of just the slowest one. Errors are still caught
        # per-stop inside fetch_one, so one slow/broken stop can't cancel
        # or fail any of the others - same isolation as the sequential
        # version, just concurrent.
        with ThreadPoolExecutor(max_workers=min(len(stop_groups), 8) or 1) as executor:
            results = list(executor.map(fetch_one, stop_groups))

        for group, rows, error in results:
            stops.append({
                "name": group["stopName"],
                "provider": PROVIDER_LABELS.get(group["provider"], group["provider"]),
                "departures": rows,
                "error": "Departures unavailable" if error else None,
            })
            if error is None:
                any_succeeded = True
            else:
                fetch_errors.append(error)
        return stops, any_succeeded, fetch_errors

    def _fetch_stop_departures(self, session, group, device_config):
        base = PROVIDER_BASES[group["provider"]]
        provider, stop_id = group["provider"], group["stopId"]
        results_cap = _load_learned_results(device_config, provider, stop_id)
        resp = session.get(
            f"{base}/stops/{quote(str(stop_id), safe='')}/departures",
            params={"duration": DEPARTURES_DURATION_MIN, "results": results_cap},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_departures = data.get("departures", [])

        rows = []
        for dep in raw_departures:
            line = (dep.get("line") or {}).get("name")
            direction = dep.get("direction")
            if (line, direction) not in group["filters"]:
                continue

            planned = _parse_when(dep.get("plannedWhen"))
            actual = _parse_when(dep.get("when")) or planned
            if actual is None:
                continue

            delay_seconds = dep.get("delay") or 0
            # Negative delay = running early; not worth a "late" badge.
            delay_minutes = max(0, round(delay_seconds / 60)) if delay_seconds else 0
            rows.append({
                "line": line,
                "direction": direction,
                "sort_time": actual,
                "planned_time": planned.strftime("%H:%M") if planned else actual.strftime("%H:%M"),
                "delay_minutes": delay_minutes,
                "platform": dep.get("platform"),
                "cancelled": _is_cancelled(dep),
            })

        rows.sort(key=lambda r: r["sort_time"])
        matched = rows[:MAX_FETCHED_DEPARTURES]

        # Adaptive page size: grow the learned cap only when it was actually
        # the bottleneck (the raw response came back full, i.e. there may be
        # more we didn't ask for) and it still wasn't enough. If the raw
        # response came back short of results_cap, this stop's schedule is
        # just genuinely sparse in the next hour - asking for a bigger page
        # wouldn't produce more matches, so leave the cap alone.
        if len(matched) < MAX_FETCHED_DEPARTURES and len(raw_departures) >= results_cap:
            grown = _grow_results_cap(results_cap)
            if grown > results_cap:
                _store_learned_results(device_config, provider, stop_id, grown)

        return matched

    def _format_now(self, time_format):
        now = datetime.now()
        return now.strftime("%H:%M") if time_format == "24h" else now.strftime("%I:%M %p")
