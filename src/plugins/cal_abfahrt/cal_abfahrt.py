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

Recommended playlist refresh interval: 2-5 minutes, driven by the departures
half (the calendar half is happy with far less).
"""

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
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
DEPARTURES_RESULTS = 15  # pre-filter cap; the API returns every line at the stop
MAX_FETCHED_DEPARTURES = 12  # per-stop cap, ahead of the layout's own truncation
DEPARTURES_TIMEOUT = 6  # short so a hung provider fails fast inside the executor's budget
CALENDAR_TIMEOUT = 20

# Query flags that strip parts of the departures response this panel never
# reads. `remarks` is deliberately NOT disabled - it is the second of the two
# signals for a cancelled trip (see _is_cancelled) and dropping it would trade
# correctness for a few more KB.
DEPARTURES_QUERY = {
    "duration": DEPARTURES_DURATION_MIN,
    "results": DEPARTURES_RESULTS,
    "pretty": "false",
    "linesOfStops": "false",
    "subStops": "false",
    "entrances": "false",
}

MAX_WORKERS = 8

# Trailing "(Berlin)" etc. on stop names, and a "via <route>" suffix on
# directions: both are noise once the panel is this narrow.
_PARENTHETICAL_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
_VIA_SUFFIX = re.compile(r"\s+via\s+.*$", re.IGNORECASE)


def _shorten_place(text):
    """Trims the parts of a stop/direction string that carry no information on
    a panel where every entry is in the same city anyway."""
    if not text:
        return ""
    text = _VIA_SUFFIX.sub("", text)
    text = _PARENTHETICAL_SUFFIX.sub("", text)
    return text.replace("S+U ", "").strip() or text.strip()


def _parse_stop_entries(raw_entries):
    """Parses the settings' `entries[]` list of JSON blobs into dicts, skipping
    any that fail to parse (e.g. a hand-edited config)."""
    entries = []
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


def _group_by_stop(entries):
    """Groups entries by (provider, stopId), preserving first-seen order, so
    a stop with five configured lines still costs exactly one HTTP request."""
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


def _is_cancelled(departure):
    if departure.get("cancelled"):
        return True
    for remark in departure.get("remarks") or []:
        if remark.get("type") == "cancellation":
            return True
    return False


def _fit_days(days, available_px, row_px, day_px, min_body_px=0):
    """Trims the agenda to the days that actually fit in `available_px`.

    A day is kept whole or dropped entirely - never sliced - because every
    pane clips with overflow:hidden and a row cut through its middle reads as
    a rendering bug rather than a deliberate cut-off.
    """
    kept = []
    used = 0
    for day in days:
        body = max(min_body_px, row_px * len(day["events"]))
        cost = day_px + body
        if used + cost > available_px and kept:
            break
        kept.append(day)
        used += cost
    return kept


class CalAbfahrt(BasePlugin):
    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------

    def build_settings_schema(self):
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

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        return template_params

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def generate_image(self, settings, device_config):
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

        days_ahead = self._int_setting(settings, "daysAhead", default=21, low=1, high=90)
        # Naive on purpose: recurring_ical_events compares this against each
        # event's own DTSTART, which for an all-day event is a plain date.
        range_start = datetime(now.year, now.month, now.day)  # noqa: DTZ001
        range_end = range_start + timedelta(days=days_ahead)

        # Resolve every credential BEFORE opening the pool: a missing or
        # malformed credential label is a configuration mistake the user needs
        # to see as an error, not something to swallow per-calendar the way a
        # flaky network is handled below.
        auths = [self._auth_for_entry(entry, device_config) for entry in calendars]

        events, stops, failures = self._fetch_all(
            calendars, auths, stop_groups, tz, range_start, range_end
        )

        if failures and not events and not stops_have_departures(stops):
            raise RuntimeError(
                f"Unable to load any calendar or departure data: {failures[0]}"
            )

        dimensions = self.get_oriented_dimensions(device_config)
        content_w, content_h = self._content_box(dimensions, settings)

        days = self._group_events_into_days(events, now, strings, time_format)
        template_params = {
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
    def _content_box(dimensions, settings):
        """Returns the (width, height) actually available to the layout, which
        is the panel minus base_plugin/render/plugin.html's own body chrome:
        the per-side margins from the shared style settings, and its 1.5vw
        padding. Getting this from the same numbers the template uses is what
        lets the budgets below be stated in real pixels."""
        width, height = dimensions

        def margin(key):
            raw = settings.get(key) or settings.get("margin") or 5
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 5

        padding = round(width * 0.015)
        available_w = width - margin("leftMargin") - margin("rightMargin") - 2 * padding
        available_h = height - margin("topMargin") - margin("bottomMargin") - 2 * padding
        return max(available_w, 1), max(available_h, 1)

    def _layout_board(self, days, stops, content_h, now, time_format):
        metrics = LAYOUT_METRICS["board"]
        rows = [
            dict(departure, stop=_shorten_place(stop["name"]))
            for stop in stops
            for departure in stop["departures"]
        ]
        rows.sort(key=lambda r: r["sort_time"])

        capacity = max(1, (content_h - metrics["dep_chrome"]) // metrics["dep_row"])
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

    def _layout_grid(self, days, stops, content_w, content_h, now, time_format):
        metrics = LAYOUT_METRICS["grid"]
        count = len(stops)
        columns = 1 if count <= 1 else (2 if count <= 6 else 3)
        grid_rows = max(1, math.ceil(count / columns)) if count else 1

        card_h = (content_h - (grid_rows - 1) * metrics["card_gap"]) / grid_rows
        capacity = int((card_h - metrics["card_chrome"]) // metrics["dep_row_min"])
        capacity = max(1, capacity)

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

    def _render_departure(self, departure, now, time_format):
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

    def _fetch_all(self, calendars, auths, stop_groups, tz, range_start, range_end):
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

        events = []
        stops = []
        failures = []

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
                )
                for entry, auth in zip(calendars, auths, strict=True)
            ]
            stop_futures = [
                executor.submit(self._fetch_stop_departures, group, session)
                for group in stop_groups
            ]

            for entry, future in zip(calendars, calendar_futures, strict=True):
                try:
                    events.extend(future.result())
                except Exception as e:
                    logger.warning("Calendar fetch failed for %s: %s", entry["url"], e)
                    failures.append(str(e))

            for group, future in zip(stop_groups, stop_futures, strict=True):
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

    def _fetch_calendar_events(self, entry, auth, session, tz, range_start, range_end):
        """Fetch, parse AND expand one calendar, all inside the worker thread.

        The two source plugins fetch concurrently but parse and expand back on
        the main thread afterwards; doing it here means one calendar's parse
        overlaps another's network wait instead of queueing behind it.
        """
        url = entry["url"]
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://") :]

        response = session.get(url, auth=auth, timeout=CALENDAR_TIMEOUT)
        response.raise_for_status()

        # Explicit UTF-8 instead of response.text: see the module docstring -
        # a missing charset header sends requests into full-body charset
        # detection, which costs more than everything after it. RFC 5545
        # mandates UTF-8, so errors="replace" only guards a broken server.
        raw = response.content.decode("utf-8", errors="replace")
        calendar = icalendar.Calendar.from_ical(raw)

        color = entry["color"]
        text_color = self._contrast_color(color)
        parsed = []
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
        return parsed

    def _fetch_stop_departures(self, group, session):
        base = PROVIDER_BASES[group["provider"]]
        response = session.get(
            f"{base}/stops/{quote(str(group['stopId']), safe='')}/departures",
            params=DEPARTURES_QUERY,
            timeout=DEPARTURES_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        rows = []
        for departure in data.get("departures", []):
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
            delay_minutes = (
                max(0, round(delay_seconds / 60)) if delay_seconds else 0
            )
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

        rows.sort(key=lambda r: r["sort_time"])
        return rows[:MAX_FETCHED_DEPARTURES]

    # ------------------------------------------------------------------
    # Calendar plumbing
    # ------------------------------------------------------------------

    def _parse_calendar_entries(self, settings):
        urls = settings.get("calendarAuthURLs[]") or []
        colors = settings.get("calendarAuthColors[]") or []
        usernames = settings.get("calendarAuthUsernames[]") or []
        labels = settings.get("calendarAuthLabels[]") or []

        def at(values, index, fallback):
            value = values[index] if index < len(values) else None
            return (value or fallback).strip() if isinstance(value, str) else fallback

        entries = []
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

    def _auth_for_entry(self, entry, device_config):
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
    def _parse_event_times(event, tz):
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

    def _group_events_into_days(self, events, now, strings, time_format):
        """Turns the flat event list into the agenda's day buckets.

        Anything already finished is dropped, and an event still running is
        pulled forward into today's bucket so a multi-day trip stays visible
        for its whole duration instead of scrolling off on day two.
        """
        today = now.date()
        items = []
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

        days = []
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
                            else strings["tomorrow"]
                            if delta == 1
                            else f"{weekday} {bucket.day:02d}.{bucket.month:02d}."
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
    def _int_setting(settings, key, default, low, high):
        try:
            value = int(str(settings.get(key) or default).strip())
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    @staticmethod
    def _format_time(value, time_format):
        if time_format == "12h":
            return value.strftime("%I:%M %p").lstrip("0")
        return value.strftime("%H:%M")

    @staticmethod
    def _contrast_color(color):
        """Black or white, whichever reads better on `color` (YIQ brightness)."""
        try:
            r, g, b = ImageColor.getrgb(color)[:3]
        except ValueError:
            return "#ffffff"
        return "#000000" if (r * 299 + g * 587 + b * 114) / 1000 >= 150 else "#ffffff"


def stops_have_departures(stops):
    return any(stop["departures"] for stop in stops)
