"""Calendar + Departures ("cal_abfahrt") plugin for InkyPi.

One panel combining what `calendar_auth` and `abfahrtzeiten` each show on
their own: an upcoming-events agenda next to live public-transit departures.
Both halves' data acquisition is folded into this plugin rather than imported
from the other two - plugin modules are hot-reloaded independently in dev mode
and are meant to stay self-contained, so a cross-plugin import would be a
fragile way to share a few dozen lines.

Two layouts, switchable in the settings page (the `cal-abfahrt-layout`
widget):

  board - agenda on the left, ONE merged, chronologically sorted departure
          board on the right with the stop as a column. No per-stop header
          or card means N stops cost nothing beyond their own rows, which is
          what makes this the layout that fits the most departures.
  grid  - narrow agenda rail on the left, one card per stop in a grid on the
          right, with the departure rows stretching to fill their card. Fewer
          rows, much more legible from across the room.

Rendering notes (this plugin is deliberately faster than the two it replaces):

  * No JavaScript at all. `calendar_auth` (and the built-in `calendar`) ship
    the ~200KB FullCalendar bundle into the headless browser and build the
    view client-side; here the agenda is plain server-rendered HTML, so the
    screenshot doesn't wait on a script parse/execute at all.
  * Calendar fetches and departure fetches share ONE thread pool, so total
    latency is the slowest single request rather than the sum of "all
    calendars" plus "all stops".
  * ICS bytes are decoded as UTF-8 explicitly instead of going through
    `response.text`. Many calendar servers omit a charset on the response, and
    requests then falls back to charset detection over the WHOLE body - which
    on a multi-hundred-KB .ics is far more expensive than the parse that
    follows. RFC 5545 mandates UTF-8, so there is nothing to detect.
  * Recurrence expansion (the real CPU cost of an .ics) happens inside the
    worker thread and only over the window actually rendered, not a fixed
    five weeks.
  * Departure requests ask the API to drop what this panel never reads
    (`pretty=false`, `linesOfStops=false`, `subStops=false`, `entrances=false`),
    which measured ~27% off the response body per stop.
  * **Calendar fetches use HTTP conditional GET (`If-None-Match`/
    `If-Modified-Since`), with the ETag/Last-Modified and last-known body
    persisted to a small JSON file under `device_config.plugin_image_dir`.**
    Measured against a real personal calendar: 727KB decompressed (134KB over
    the wire even with gzip already on) and ~3s just for one fetch - by far
    the largest cost in the whole render, dwarfing all four stops' departure
    fetches combined (~20KB / ~0.2s each). A calendar server that supports
    conditional GET (confirmed for SOGo) returns an empty-bodied 304 in a
    fraction of that time when nothing changed, which is the common case
    between the few-minutes playlist refresh interval. This is why the cache
    has to be on disk, not the in-memory `utils/http_cache.py`: plugin
    renders execute in a fresh subprocess each refresh (see base_plugin's
    docstring on this), so anything held only in process memory is gone by
    the next attempt. Best-effort throughout - any cache read/write failure
    just falls back to an unconditional fetch, never breaks the render.

Recommended playlist refresh interval: 2-5 minutes, driven by the departures
half (the calendar half is happy with far less).
"""

import hashlib
import json
import logging
import math
import os
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import quote

import icalendar
import recurring_ical_events
from PIL import ImageColor

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.settings_schema import (
    callout,
    field,
    option,
    row,
    schema,
    section,
    widget,
)
from plugins.cal_abfahrt.constants import (
    LANGUAGES,
    LAYOUT_METRICS,
    PROVIDER_BASES,
    PROVIDER_LABELS,
)
from utils.http_client import get_http_session
from utils.time_utils import get_timezone

logger = logging.getLogger(__name__)

VALID_LABEL = re.compile(r"^[A-Za-z0-9_]+$")

DEPARTURES_DURATION_MIN = 60
DEPARTURES_RESULTS = (
    15  # starting pre-filter cap; a busy multi-line stop learns a bigger one, see below
)
# Sanity ceiling on how many departures a single stop ever keeps, regardless
# of the current layout's computed capacity (_board_departure_capacity /
# _grid_departure_capacity) - guards a pathological _content_box() result,
# not something expected to bind for any real panel size/layout.
DEPARTURES_ABSOLUTE_MAX = 40
# Ceiling for the learned per-stop `results` cap below - comfortably inside
# what the API already handles today (the settings wizard's stop/line picker
# queries at results=100, see abfahrtzeitenLinesForStop in plugin_schema.js).
DEPARTURES_RESULTS_CEILING = 80
# Whenever a render finds its target, the learned cap is nudged toward
# `raw_scanned_for_target * DEPARTURES_HEADROOM` (extra room above the exact
# minimum, so next render's inevitably-slightly-different service pattern -
# a bidirectional line interleaves both directions, so how many raw entries
# it takes to find N matches shifts with the time of day - doesn't
# immediately fall short again), moving only DEPARTURES_SMOOTHING of the way
# there each cycle rather than snapping straight to the ideal. That damping
# is what keeps ordinary cycle-to-cycle timetable noise from bouncing the cap
# up and down every render, while still converging on the real typical need
# within a handful of cycles - unlike a fixed "only shrink once 3x+
# oversized" cliff, which left a cap that had ratcheted up to survive one
# unusually unfavorable cycle sitting well above the typical need
# indefinitely, with nothing ever pulling it back down.
DEPARTURES_HEADROOM = 1.3
DEPARTURES_SMOOTHING = 0.5
DEPARTURES_TIMEOUT = (
    6  # short so a hung provider fails fast inside the executor's budget
)
CALENDAR_TIMEOUT = 20

# Query flags that strip parts of the departures response this panel never
# reads. `remarks` is deliberately NOT disabled - it is the second of the two
# signals for a cancelled trip (see _is_cancelled) and dropping it would trade
# correctness for a few more KB. `results` is deliberately absent - it's
# per-stop and learned, see _load_learned_results below.
DEPARTURES_QUERY = {
    "duration": DEPARTURES_DURATION_MIN,
    "pretty": "false",
    "linesOfStops": "false",
    "subStops": "false",
    "entrances": "false",
}

MAX_WORKERS = 8

# Filename prefix for the on-disk ICS conditional-GET cache - see the module
# docstring's "Calendar fetches use HTTP conditional GET" note.
_ICS_CACHE_PREFIX = "_cal_abfahrt_ics_cache_"

# Filename prefix for the on-disk "learned results cap" cache - see
# _load_learned_results's docstring.
_DEPARTURES_CACHE_PREFIX = "_cal_abfahrt_departures_cache_"


def _calendar_cache_path(device_config: Any, url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(
        device_config.plugin_image_dir, f"{_ICS_CACHE_PREFIX}{digest}.json"
    )


def _load_calendar_cache(device_config: Any, url: str) -> dict[str, Any] | None:
    """Returns the cached {etag, last_modified, body, expanded} dict for
    `url`, or None.

    Best-effort: a missing, corrupt, or unreadable cache file is treated the
    same as "no cache" rather than raised - caching is an optimization, never
    something a render should fail over.
    """
    try:
        with open(_calendar_cache_path(device_config, url), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(data, dict) or not data.get("body"):
        return None
    return data


def _store_calendar_cache(
    device_config: Any,
    url: str,
    etag: str | None,
    last_modified: str | None,
    body: str,
    expanded: dict[str, Any] | None = None,
) -> None:
    """Writes the cache file via a temp-file-plus-rename so a concurrent
    reader (another refresh, or this same file mid-read) never sees a
    partially-written file.

    `expanded`, when given, is the {date, events} same-day parse cache (see
    _serialize_expanded_events) - stored alongside the raw body so a network
    cache hit (304, body unchanged) can also skip re-parsing.
    """
    try:
        path = _calendar_cache_path(device_config, url)
        tmp_path = f"{path}.tmp"
        payload: dict[str, Any] = {
            "etag": etag,
            "last_modified": last_modified,
            "body": body,
        }
        if expanded is not None:
            payload["expanded"] = expanded
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except (OSError, AttributeError) as e:
        logger.debug("Could not write calendar cache for %s: %s", url, e)


def _departures_cache_path(device_config: Any, provider: str, stop_id: str) -> str:
    digest = hashlib.sha256(f"{provider}:{stop_id}".encode()).hexdigest()[:32]
    return os.path.join(
        device_config.plugin_image_dir, f"{_DEPARTURES_CACHE_PREFIX}{digest}.json"
    )


def _load_learned_results(device_config: Any, provider: str, stop_id: str) -> int:
    """Returns the learned `results` page size for one stop, or the default.

    A stop shared by several frequent lines (trams, buses) can crowd a
    less-frequent line (e.g. one direction of a ring S-Bahn line) out of the
    default DEPARTURES_RESULTS page entirely - the API returns its next N
    departures across every line at the stop, not per configured filter. This
    persists a per-stop page size that _fetch_stop_departures grows once it
    actually observes that DEPARTURES_RESULTS wasn't enough, so the fix
    applies only to stops that need it rather than paying a bigger page for
    every stop up front. Best-effort like the calendar cache above: a
    missing/corrupt file just means "start from the default again".
    """
    try:
        with open(
            _departures_cache_path(device_config, provider, stop_id), encoding="utf-8"
        ) as f:
            data = json.load(f)
    except (OSError, ValueError, AttributeError):
        return DEPARTURES_RESULTS
    value = data.get("results") if isinstance(data, dict) else None
    if not isinstance(value, int) or value <= 0:
        return DEPARTURES_RESULTS
    return min(value, DEPARTURES_RESULTS_CEILING)


def _store_learned_results(
    device_config: Any, provider: str, stop_id: str, results: int
) -> None:
    try:
        path = _departures_cache_path(device_config, provider, stop_id)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f)
        os.replace(tmp_path, path)
    except (OSError, AttributeError) as e:
        logger.debug(
            "Could not write departures cache for %s/%s: %s", provider, stop_id, e
        )


def _grow_results_cap(current: int) -> int:
    """Returns a bigger page size to try next time, capped at the ceiling."""
    return min(DEPARTURES_RESULTS_CEILING, max(current + 5, round(current * 1.5)))


def _serialize_expanded_events(
    window_date: date, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Packs a calendar's already-parsed-and-expanded event list for the
    disk cache. `window_date` is range_start.date() - the expansion window
    only changes once a day (see generate_image), so a cache entry is only
    reused for a repeat render on the SAME calendar day."""
    return {
        "date": window_date.isoformat(),
        "events": [
            {
                "title": e["title"],
                "start": e["start"].isoformat(),
                "end": e["end"].isoformat() if e["end"] else None,
                "all_day": e["all_day"],
            }
            for e in events
        ],
    }


def _deserialize_expanded_events(
    expanded: Any, window_date: date, color: str, text_color: str
) -> list[dict[str, Any]] | None:
    """Inverse of _serialize_expanded_events, reattaching this render's
    color/text_color (per-instance display settings, not calendar data, so
    never part of the cached payload). Returns None if `expanded` is missing,
    malformed, or was computed for a different day's window."""
    if (
        not isinstance(expanded, dict)
        or expanded.get("date") != window_date.isoformat()
    ):
        return None
    try:
        events: list[dict[str, Any]] = []
        for e in expanded["events"]:
            all_day = e["all_day"]
            start = (
                date.fromisoformat(e["start"])
                if all_day
                else datetime.fromisoformat(e["start"])
            )
            end: date | None = None
            if e["end"]:
                end = (
                    date.fromisoformat(e["end"])
                    if all_day
                    else datetime.fromisoformat(e["end"])
                )
            events.append(
                {
                    "title": e["title"],
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "color": color,
                    "text_color": text_color,
                }
            )
    except (KeyError, TypeError, ValueError):
        return None
    return events


# Trailing "(Berlin)" etc. on stop names, and a "via <route>" suffix on
# directions: both are noise once the panel is this narrow.
_PARENTHETICAL_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
_VIA_SUFFIX = re.compile(r"\s+via\s+.*$", re.IGNORECASE)


def _shorten_place(text: str | None) -> str:
    """Trims the parts of a stop/direction string that carry no information on
    a panel where every entry is in the same city anyway."""
    if not text:
        return ""
    text = _VIA_SUFFIX.sub("", text)
    text = _PARENTHETICAL_SUFFIX.sub("", text)
    return text.replace("S+U ", "").strip() or text.strip()


def _parse_stop_entries(raw_entries: Any) -> list[dict[str, Any]]:
    """Parses the settings' `entries[]` list of JSON blobs into dicts, skipping
    any that fail to parse (e.g. a hand-edited config)."""
    entries: list[dict[str, Any]] = []
    for raw in raw_entries or []:
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Skipping unparseable stop entry: %r", raw)
            continue
        if (
            entry.get("provider") in PROVIDER_BASES
            and entry.get("stopId")
            and entry.get("lineName")
            and entry.get("direction")
        ):
            entries.append(entry)
    return entries


def _group_by_stop(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups entries by (provider, stopId), preserving first-seen order, so
    a stop with five configured lines still costs exactly one HTTP request."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
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


def _parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_cancelled(departure: Mapping[str, Any]) -> bool:
    if departure.get("cancelled"):
        return True
    for remark in departure.get("remarks") or []:
        if remark.get("type") == "cancellation":
            return True
    return False


def _fit_days(
    days: list[dict[str, Any]],
    available_px: int,
    row_px: int,
    day_px: int,
    min_body_px: int = 0,
) -> list[dict[str, Any]]:
    """Trims the agenda to the days that actually fit in `available_px`.

    A day is kept whole or dropped entirely - never sliced - because every
    pane clips with overflow:hidden and a row cut through its middle reads as
    a rendering bug rather than a deliberate cut-off.
    """
    kept: list[dict[str, Any]] = []
    used = 0
    for day in days:
        body = max(min_body_px, row_px * len(day["events"]))
        cost = day_px + body
        if used + cost > available_px and kept:
            break
        kept.append(day)
        used += cost
    return kept


def _board_departure_capacity(content_h: int) -> int:
    """Row budget for the merged board layout's departure column.

    A pure function of the panel's available height alone - the calendar and
    departure panes are independent side-by-side columns (see
    CalAbfahrt._layout_board), so this doesn't depend on how many calendar
    days end up shown. Pulled out so the pre-fetch learning target in
    _fetch_stop_departures and the post-fetch truncation in _layout_board use
    the exact same number instead of two formulas that could drift apart.
    """
    metrics = LAYOUT_METRICS["board"]
    return max(1, (content_h - metrics["dep_chrome"]) // metrics["dep_row"])


def _grid_departure_capacity(stop_count: int, content_h: int) -> int:
    """Per-card row budget for the grid layout - see CalAbfahrt._layout_grid.

    Depends on how many stops are configured (more stops -> more grid rows ->
    a shorter card each), not on any fetched data, so - like
    _board_departure_capacity - it's computable before any network fetch.
    """
    if stop_count <= 0:
        return 1
    metrics = LAYOUT_METRICS["grid"]
    columns = 1 if stop_count <= 1 else (2 if stop_count <= 6 else 3)
    grid_rows = max(1, math.ceil(stop_count / columns))
    card_h = (content_h - (grid_rows - 1) * metrics["card_gap"]) / grid_rows
    return max(1, int((card_h - metrics["card_chrome"]) // metrics["dep_row_min"]))


class CalAbfahrt(BasePlugin):
    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------

    def build_settings_schema(self) -> Any:
        return schema(
            section(
                "Layout",
                widget(
                    "cal-abfahrt-layout",
                    template="widgets/cal_abfahrt_layout.html",
                ),
            ),
            section(
                "Calendars",
                widget(
                    "calendar-auth-repeater",
                    template="widgets/calendar_auth_repeater.html",
                ),
                callout(
                    "Leave username blank for a calendar that doesn't need login. "
                    "If set, give that calendar a credential label "
                    "(letters/numbers/underscore only), then set "
                    "CALENDAR_AUTH_PASSWORD_<LABEL> with the password as a custom "
                    "secret on the API keys page. These are the same secrets the "
                    "Calendar (with Login) plugin uses, so an existing label works "
                    "here unchanged.",
                ),
            ),
            section(
                "Stops",
                widget(
                    "abfahrtzeiten-stops",
                    template="widgets/abfahrtzeiten_stops.html",
                ),
            ),
            section(
                "Display",
                row(
                    field(
                        "language",
                        "select",
                        label="Language",
                        default="de",
                        options=[option("de", "Deutsch"), option("en", "English")],
                        hint="Applies to weekday names and the panel's own labels only; "
                        "event titles and transit data come through as-is.",
                    ),
                    field(
                        "daysAhead",
                        "number",
                        label="Days Ahead",
                        min=1,
                        max=90,
                        default="21",
                        hint="How far ahead recurring events are expanded. Lower is "
                        "faster; there is no point setting it beyond what the agenda "
                        "can physically show.",
                    ),
                ),
                row(
                    field(
                        "displayPlatform",
                        "checkbox",
                        label="Platform",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="false",
                        hint="Shows the departure platform/track when the provider "
                        "reports one. Stop cards only - the merged board has no room.",
                    ),
                    field(
                        "displayRefreshTime",
                        "checkbox",
                        label="Refresh Time",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                ),
            ),
        )

    def generate_settings_template(self) -> Any:
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        return template_params

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def generate_image(self, settings: Mapping[str, Any], device_config: Any) -> Any:
        calendars = self._parse_calendar_entries(settings)
        stop_groups = _group_by_stop(_parse_stop_entries(settings.get("entries[]")))

        if not calendars and not stop_groups:
            raise RuntimeError(
                "Add at least one calendar or one stop in the plugin settings."
            )

        layout = "grid" if settings.get("layout") == "grid" else "board"
        strings = LANGUAGES.get(settings.get("language") or "de", LANGUAGES["de"])

        timezone = device_config.get_config("timezone", default="Europe/Berlin")
        time_format = device_config.get_config("time_format", default="24h")
        tz = get_timezone(timezone)
        now = datetime.now(tz)

        days_ahead = self._int_setting(
            settings, "daysAhead", default=21, low=1, high=90
        )
        # Naive on purpose: recurring_ical_events compares this against each
        # event's own DTSTART, which for an all-day event is a plain date.
        range_start = datetime(now.year, now.month, now.day)  # noqa: DTZ001
        range_end = range_start + timedelta(days=days_ahead)

        # Resolve every credential BEFORE opening the pool: a missing or
        # malformed credential label is a configuration mistake the user needs
        # to see as an error, not something to swallow per-calendar the way a
        # flaky network is handled below.
        auths = [self._auth_for_entry(entry, device_config) for entry in calendars]

        # Computed before fetching (depends only on settings/resolution, not
        # on fetched data) so each stop's departures fetch knows how many
        # departures the CURRENT layout can actually show, instead of
        # fetching toward a fixed guess unrelated to the real screen budget -
        # see _fetch_stop_departures's target_capacity parameter.
        dimensions = self.get_oriented_dimensions(device_config)
        content_w, content_h = self._content_box(dimensions, settings)
        target_capacity = (
            _grid_departure_capacity(len(stop_groups), content_h)
            if layout == "grid"
            else _board_departure_capacity(content_h)
        )

        events, stops, failures = self._fetch_all(
            calendars,
            auths,
            stop_groups,
            tz,
            range_start,
            range_end,
            device_config,
            target_capacity,
        )

        if failures and not events and not stops_have_departures(stops):
            raise RuntimeError(
                f"Unable to load any calendar or departure data: {failures[0]}"
            )

        days = self._group_events_into_days(events, now, strings, time_format)
        template_params: dict[str, Any] = {
            "layout": layout,
            "strings": strings,
            "plugin_settings": settings,
            "last_refresh_time": self._format_time(now, time_format),
            "show_refresh_time": settings.get("displayRefreshTime") != "false",
            "show_platform": settings.get("displayPlatform") == "true",
            "has_calendars": bool(calendars),
            "has_stops": bool(stop_groups),
        }

        if layout == "grid":
            template_params.update(
                self._layout_grid(days, stops, content_w, content_h, now, time_format)
            )
        else:
            template_params.update(
                self._layout_board(days, stops, content_h, now, time_format)
            )

        css_file = f"cal_abfahrt_{layout}.css"
        image = self.render_image(
            dimensions, "cal_abfahrt.html", css_file, template_params
        )
        if not image:
            raise RuntimeError(
                "Failed to render Calendar + Departures image, please check logs."
            )
        return image

    # ------------------------------------------------------------------
    # Layout budgeting
    #
    # Everything below decides how much content to hand the template, in
    # literal pixels measured against the real render. See LAYOUT_METRICS.
    # ------------------------------------------------------------------

    @staticmethod
    def _content_box(
        dimensions: tuple[int, int], settings: Mapping[str, Any]
    ) -> tuple[int, int]:
        """Returns the (width, height) actually available to the layout, which
        is the panel minus base_plugin/render/plugin.html's own body chrome:
        the per-side margins from the shared style settings, and its 1.5vw
        padding. Getting this from the same numbers the template uses is what
        lets the budgets below be stated in real pixels."""
        width, height = dimensions

        def margin(key: str) -> int:
            raw = settings.get(key) or settings.get("margin") or 5
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 5

        padding = round(width * 0.015)
        available_w = width - margin("leftMargin") - margin("rightMargin") - 2 * padding
        available_h = (
            height - margin("topMargin") - margin("bottomMargin") - 2 * padding
        )
        return max(available_w, 1), max(available_h, 1)

    def _layout_board(
        self,
        days: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        content_h: int,
        now: datetime,
        time_format: str,
    ) -> dict[str, Any]:
        metrics = LAYOUT_METRICS["board"]
        rows = [
            dict(departure, stop=_shorten_place(stop["name"]))
            for stop in stops
            for departure in stop["departures"]
        ]
        rows.sort(key=lambda r: r["sort_time"])

        capacity = _board_departure_capacity(content_h)
        return {
            "days": _fit_days(
                days,
                content_h - metrics["cal_chrome"],
                metrics["cal_row"],
                metrics["cal_day"],
            ),
            "board_rows": [
                self._render_departure(r, now, time_format) for r in rows[:capacity]
            ],
            "stop_errors": [s["name"] for s in stops if s["error"]],
        }

    def _layout_grid(
        self,
        days: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        content_w: int,
        content_h: int,
        now: datetime,
        time_format: str,
    ) -> dict[str, Any]:
        metrics = LAYOUT_METRICS["grid"]
        count = len(stops)
        columns = 1 if count <= 1 else (2 if count <= 6 else 3)

        capacity = _grid_departure_capacity(count, content_h)

        cards = [
            {
                "name": _shorten_place(stop["name"]),
                "provider": stop["provider"],
                "error": stop["error"],
                "departures": [
                    self._render_departure(d, now, time_format)
                    for d in stop["departures"][:capacity]
                ],
            }
            for stop in stops
        ]

        return {
            "days": _fit_days(
                days,
                content_h - metrics["cal_chrome"],
                metrics["cal_row"],
                metrics["cal_day"],
                min_body_px=metrics["cal_day_min_body"],
            ),
            "stop_cards": cards,
            "grid_columns": columns,
            # An odd stop out would otherwise leave a hole in the last row;
            # letting it span the full width keeps the block rectangular.
            "grid_span_last": count > columns and count % columns == 1,
        }

    def _render_departure(
        self, departure: Mapping[str, Any], now: datetime, time_format: str
    ) -> dict[str, Any]:
        minutes = max(0, round((departure["sort_time"] - now).total_seconds() / 60))
        return {
            "line": departure["line"],
            "direction": _shorten_place(departure["direction"]),
            "time": self._format_time(departure["display_time"], time_format),
            "delay": departure["delay_minutes"],
            "minutes": minutes,
            "platform": departure["platform"],
            "cancelled": departure["cancelled"],
            "stop": departure.get("stop", ""),
        }

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_all(
        self,
        calendars: list[dict[str, Any]],
        auths: list[tuple[str, str] | None],
        stop_groups: list[dict[str, Any]],
        tz: tzinfo,
        range_start: datetime,
        range_end: datetime,
        device_config: Any,
        target_capacity: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """Runs every calendar and every stop request through ONE pool.

        Splitting them (as the two source plugins necessarily do) makes total
        latency "slowest calendar + slowest stop"; sharing the pool makes it
        "slowest request", which on a typical config roughly halves the wait.
        Failures are isolated per source so one unreachable server can't blank
        the whole panel - only a total wipeout raises, back in generate_image.
        """
        session = get_http_session()
        total = len(calendars) + len(stop_groups)
        if not total:
            return [], [], []

        events: list[dict[str, Any]] = []
        stops: list[dict[str, Any]] = []
        failures: list[str] = []

        with ThreadPoolExecutor(max_workers=min(total, MAX_WORKERS)) as executor:
            calendar_futures = [
                executor.submit(
                    self._fetch_calendar_events,
                    entry,
                    auth,
                    session,
                    tz,
                    range_start,
                    range_end,
                    device_config,
                )
                for entry, auth in zip(calendars, auths, strict=True)
            ]
            stop_futures = [
                executor.submit(
                    self._fetch_stop_departures,
                    group,
                    session,
                    device_config,
                    target_capacity,
                )
                for group in stop_groups
            ]

            for entry, future in zip(calendars, calendar_futures, strict=True):
                try:
                    events.extend(future.result())
                except Exception as e:
                    logger.warning("Calendar fetch failed for %s: %s", entry["url"], e)
                    failures.append(str(e))

            for group, future in zip(stop_groups, stop_futures, strict=True):
                departures: list[dict[str, Any]]
                error: str | None
                try:
                    departures = future.result()
                    error = None
                except Exception as e:
                    logger.warning(
                        "Departures fetch failed for stop %s (%s): %s",
                        group["stopId"],
                        group["provider"],
                        e,
                    )
                    departures = []
                    error = str(e)
                    failures.append(error)
                stops.append(
                    {
                        "name": group["stopName"],
                        "provider": PROVIDER_LABELS.get(
                            group["provider"], group["provider"]
                        ),
                        "departures": departures,
                        "error": bool(error),
                    }
                )

        return events, stops, failures

    def _fetch_calendar_events(
        self,
        entry: dict[str, Any],
        auth: tuple[str, str] | None,
        session: Any,
        tz: tzinfo,
        range_start: datetime,
        range_end: datetime,
        device_config: Any,
    ) -> list[dict[str, Any]]:
        """Fetch, parse AND expand one calendar, all inside the worker thread.

        The two source plugins fetch concurrently but parse and expand back on
        the main thread afterwards; doing it here means one calendar's parse
        overlaps another's network wait instead of queueing behind it.

        Two-tier cache (see the module docstring): a conditional GET can skip
        the network transfer, but `icalendar.Calendar.from_ical()` measured
        7+ seconds on a large real personal calendar - order of magnitude
        more than the network fetch it was replacing. Since the expansion
        window (range_start/range_end) only shifts once a day, a confirmed-
        unchanged calendar (304) whose cached expansion was computed for
        today's window can skip parsing AND expansion entirely, not just the
        download.
        """
        url = entry["url"]
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://") :]
        color = entry["color"]
        text_color = self._contrast_color(color)
        window_date = range_start.date()

        # Conditional GET - only sent when we actually have a prior body to
        # fall back on, so a 304 with no local cache (cache file deleted,
        # first run raced, etc.) is treated as the genuine error it would
        # be, not a mystery empty calendar.
        cached = _load_calendar_cache(device_config, url)
        headers: dict[str, str] = {}
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

        response = session.get(
            url, auth=auth, timeout=CALENDAR_TIMEOUT, headers=headers or None
        )

        if response.status_code == 304:
            if not cached:
                raise RuntimeError(
                    f"Server returned 304 Not Modified for {url} but no local "
                    "cache exists to serve"
                )
            reused = _deserialize_expanded_events(
                cached.get("expanded"), window_date, color, text_color
            )
            if reused is not None:
                return reused
            raw = cached["body"]
            etag = cached.get("etag")
            last_modified = cached.get("last_modified")
        else:
            response.raise_for_status()
            # Explicit UTF-8 instead of response.text: see the module
            # docstring - a missing charset header sends requests into
            # full-body charset detection, which costs more than everything
            # after it. RFC 5545 mandates UTF-8, so errors="replace" only
            # guards a broken server.
            raw = response.content.decode("utf-8", errors="replace")
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")

        calendar = icalendar.Calendar.from_ical(raw)

        parsed: list[dict[str, Any]] = []
        for event in recurring_ical_events.of(calendar).between(range_start, range_end):
            start, end, all_day = self._parse_event_times(event, tz)
            parsed.append(
                {
                    "title": str(event.get("summary") or ""),
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "color": color,
                    "text_color": text_color,
                }
            )

        if etag or last_modified:
            expanded = _serialize_expanded_events(window_date, parsed)
            _store_calendar_cache(
                device_config, url, etag, last_modified, raw, expanded
            )

        return parsed

    def _fetch_stop_departures(
        self,
        group: dict[str, Any],
        session: Any,
        device_config: Any,
        target_capacity: int,
    ) -> list[dict[str, Any]]:
        """Fetches, filters, and adaptively re-pages one stop's departures.

        `target_capacity` is how many departures the CURRENT render's layout
        can actually show for this stop (see _board_departure_capacity /
        _grid_departure_capacity in generate_image) - not a fixed guess. Two
        independent adaptive mechanisms both key off it:

          * the learned `results` page size (_load_learned_results /
            _store_learned_results), grown when the page - not a genuinely
            sparse schedule - left this stop short of target_capacity, and
            shrunk back when it's clearly bigger than this stop needs (e.g.
            after more stops got added to a grid layout, shrinking every
            card's row budget);
          * how many matched departures are kept at all, which now tracks
            target_capacity directly instead of the old fixed
            MAX_FETCHED_DEPARTURES guess - a spacious single-stop board can
            show more than that guess allowed for; a cramped many-stop grid
            card can hold far fewer.
        """
        base = PROVIDER_BASES[group["provider"]]
        provider, stop_id = group["provider"], group["stopId"]
        results_cap = _load_learned_results(device_config, provider, stop_id)
        effective_target = max(1, min(target_capacity, DEPARTURES_ABSOLUTE_MAX))
        response = session.get(
            f"{base}/stops/{quote(str(stop_id), safe='')}/departures",
            params={**DEPARTURES_QUERY, "results": results_cap},
            timeout=DEPARTURES_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        raw_departures = data.get("departures", [])

        rows: list[dict[str, Any]] = []
        # Tracks how many raw (pre-filter) entries it took, in the order the
        # API returned them, to accumulate `effective_target` matches - used
        # below to decide whether results_cap has more headroom than this
        # stop actually needs.
        raw_scanned_for_target: int | None = None
        for i, departure in enumerate(raw_departures, start=1):
            line = (departure.get("line") or {}).get("name")
            direction = departure.get("direction")
            if (line, direction) not in group["filters"]:
                continue

            planned = _parse_when(departure.get("plannedWhen"))
            actual = _parse_when(departure.get("when")) or planned
            if actual is None:
                continue

            delay_seconds = departure.get("delay") or 0
            # Negative delay = running early; not worth a "late" badge.
            delay_minutes = max(0, round(delay_seconds / 60)) if delay_seconds else 0
            rows.append(
                {
                    "line": line,
                    "direction": direction,
                    "sort_time": actual,
                    "display_time": planned or actual,
                    "delay_minutes": delay_minutes,
                    "platform": departure.get("platform"),
                    "cancelled": _is_cancelled(departure),
                }
            )
            if raw_scanned_for_target is None and len(rows) >= effective_target:
                raw_scanned_for_target = i

        rows.sort(key=lambda r: r["sort_time"])
        matched = rows[:effective_target]

        if len(matched) < effective_target and len(raw_departures) >= results_cap:
            # The results cap - not a genuinely sparse schedule - left this
            # stop short of what the current layout can display, and there's
            # no observed "where would the target have landed" to converge
            # toward (see the elif below) since it was never reached. Grow
            # blindly.
            grown = _grow_results_cap(results_cap)
            if grown > results_cap:
                logger.info(
                    "cal_abfahrt: departures cap for %s/%s grown %d -> %d "
                    "(only %d/%d matches within the page)",
                    provider,
                    stop_id,
                    results_cap,
                    grown,
                    len(matched),
                    effective_target,
                )
                _store_learned_results(device_config, provider, stop_id, grown)
        elif raw_scanned_for_target is not None:
            # Target was reached - nudge the cap toward what this cycle
            # actually needed (with headroom), rather than only correcting
            # once grossly oversized. See DEPARTURES_HEADROOM/_SMOOTHING.
            ideal = max(
                DEPARTURES_RESULTS, round(raw_scanned_for_target * DEPARTURES_HEADROOM)
            )
            nudged = round(
                DEPARTURES_SMOOTHING * results_cap + (1 - DEPARTURES_SMOOTHING) * ideal
            )
            nudged = max(DEPARTURES_RESULTS, min(nudged, DEPARTURES_RESULTS_CEILING))
            if nudged != results_cap:
                logger.info(
                    "cal_abfahrt: departures cap for %s/%s adjusted %d -> %d "
                    "(needed %d raw entries for %d matches this cycle)",
                    provider,
                    stop_id,
                    results_cap,
                    nudged,
                    raw_scanned_for_target,
                    effective_target,
                )
                _store_learned_results(device_config, provider, stop_id, nudged)

        return matched

    # ------------------------------------------------------------------
    # Calendar plumbing
    # ------------------------------------------------------------------

    def _parse_calendar_entries(
        self, settings: Mapping[str, Any]
    ) -> list[dict[str, str]]:
        urls = settings.get("calendarAuthURLs[]") or []
        colors = settings.get("calendarAuthColors[]") or []
        usernames = settings.get("calendarAuthUsernames[]") or []
        labels = settings.get("calendarAuthLabels[]") or []

        def at(values: Any, index: int, fallback: str) -> str:
            value = values[index] if index < len(values) else None
            return (value or fallback).strip() if isinstance(value, str) else fallback

        entries: list[dict[str, str]] = []
        for index, raw_url in enumerate(urls):
            url = (raw_url or "").strip()
            if not url:
                continue
            entries.append(
                {
                    "url": url,
                    "color": at(colors, index, "#007BFF") or "#007BFF",
                    "username": at(usernames, index, ""),
                    "label": at(labels, index, ""),
                }
            )
        return entries

    def _auth_for_entry(
        self, entry: dict[str, Any], device_config: Any
    ) -> tuple[str, str] | None:
        """Resolves one calendar's HTTP Basic Auth pair, or None.

        The password never lives in plugin settings - only a credential label,
        used to look up CALENDAR_AUTH_PASSWORD_<LABEL> from the .env-backed
        secret store. The settings route re-embeds a plugin instance's entire
        stored settings into the page as JSON on every edit, so anything kept
        in the entries list round-trips to the browser in plaintext. Sharing
        the key name with the calendar_auth plugin is deliberate: an existing
        secret works here without being re-entered.
        """
        if not entry["username"]:
            return None
        label = entry["label"]
        if not label or not VALID_LABEL.match(label):
            raise RuntimeError(
                f"Calendar '{entry['url']}' has a username but no valid credential "
                "label (letters/numbers/underscore only) - set one so its password "
                "can be looked up."
            )
        key = f"CALENDAR_AUTH_PASSWORD_{label.upper()}"
        password = device_config.load_env_key(key)
        if not password:
            raise RuntimeError(
                f"Username set for '{entry['url']}' but {key} isn't configured. "
                "Set it on the API Keys page."
            )
        return (entry["username"], password)

    @staticmethod
    def _parse_event_times(event: Any, tz: tzinfo) -> tuple[Any, Any, bool]:
        all_day = False
        dtstart = event.decoded("dtstart")
        if isinstance(dtstart, datetime):
            start = dtstart.astimezone(tz)
        else:
            start = dtstart
            all_day = True

        end = None
        if "dtend" in event:
            dtend = event.decoded("dtend")
            end = dtend.astimezone(tz) if isinstance(dtend, datetime) else dtend
        elif "duration" in event:
            end = dtstart + event.decoded("duration")
        return start, end, all_day

    def _group_events_into_days(
        self,
        events: list[dict[str, Any]],
        now: datetime,
        strings: Any,
        time_format: str,
    ) -> list[dict[str, Any]]:
        """Turns the flat event list into the agenda's day buckets.

        Anything already finished is dropped, and an event still running is
        pulled forward into today's bucket so a multi-day trip stays visible
        for its whole duration instead of scrolling off on day two.
        """
        today = now.date()
        items: list[dict[str, Any]] = []
        for event in events:
            start = event["start"]
            end = event["end"]
            if event["all_day"]:
                start_date = start if isinstance(start, date) else start.date()
                # An ICS all-day DTEND is exclusive: a one-day event ends the
                # following morning, so the last covered day is one back.
                last_date = (
                    (end - timedelta(days=1))
                    if isinstance(end, date) and not isinstance(end, datetime)
                    else start_date
                )
                if last_date < today:
                    continue
                bucket = max(start_date, today)
                sort_key = (bucket, 0, datetime.min.time())
                time_label = ""
            else:
                if (end or start) < now:
                    continue
                start_date = start.date()
                last_date = start_date
                bucket = max(start_date, today)
                sort_key = (bucket, 1, start.timetz())
                time_label = self._format_time(start, time_format)

            title = event["title"]
            if last_date > start_date:
                title = f"{title} ({strings['until']} {last_date.day:02d}.{last_date.month:02d}.)"

            items.append(
                {
                    "bucket": bucket,
                    "sort_key": sort_key,
                    "all_day": event["all_day"],
                    "time": time_label,
                    "title": title,
                    "color": event["color"],
                }
            )

        items.sort(key=lambda i: i["sort_key"])

        days: list[dict[str, Any]] = []
        for item in items:
            if not days or days[-1]["date"] != item["bucket"]:
                bucket = item["bucket"]
                delta = (bucket - today).days
                weekday = strings["weekdays"][bucket.weekday()]
                days.append(
                    {
                        "date": bucket,
                        "weekday": weekday,
                        "daynum": f"{bucket.day:02d}",
                        "label": (
                            strings["today"]
                            if delta == 0
                            else (
                                strings["tomorrow"]
                                if delta == 1
                                else f"{weekday} {bucket.day:02d}.{bucket.month:02d}."
                            )
                        ),
                        "is_today": delta == 0,
                        "is_weekend": bucket.weekday() >= 5,
                        "events": [],
                    }
                )
            days[-1]["events"].append(item)
        return days

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _int_setting(
        settings: Mapping[str, Any], key: str, default: int, low: int, high: int
    ) -> int:
        try:
            value = int(str(settings.get(key) or default).strip())
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    @staticmethod
    def _format_time(value: datetime, time_format: str) -> str:
        if time_format == "12h":
            return value.strftime("%I:%M %p").lstrip("0")
        return value.strftime("%H:%M")

    @staticmethod
    def _contrast_color(color: str) -> str:
        """Black or white, whichever reads better on `color` (YIQ brightness)."""
        try:
            r, g, b = ImageColor.getrgb(color)[:3]
        except ValueError:
            return "#ffffff"
        return "#000000" if (r * 299 + g * 587 + b * 114) / 1000 >= 150 else "#ffffff"


def stops_have_departures(stops: list[dict[str, Any]]) -> bool:
    return any(stop["departures"] for stop in stops)
