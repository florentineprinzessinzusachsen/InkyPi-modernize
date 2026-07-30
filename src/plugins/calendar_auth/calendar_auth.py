"""Calendar (with Login) plugin for InkyPi.

A clone of the built-in `calendar` plugin (same FullCalendar rendering,
same view/language/font settings) with one addition: ICS fetches can be
sent with per-calendar HTTP Basic Auth, for calendar servers that require
login to reach the .ics feed at all (e.g. Mailcow's ICS export, which
needs the account email + an app-specific password) - and since different
calendars can belong to entirely different accounts, each configured
calendar gets its own independent username/password, not one shared pair.

Credential storage: each calendar entry (URL, color, username, and a
short user-chosen `credentialLabel`) is a normal per-instance plugin
setting, stored in device.json like the calendar plugin's URL/color list -
none of that is especially sensitive on its own. The PASSWORD is
deliberately kept out of that list entirely. It's read via
`device_config.load_env_key(f"CALENDAR_AUTH_PASSWORD_{label}")` from the
app's existing .env-backed secrets store (the same mechanism every other
plugin uses for API keys, managed through the site's own API keys page
at /settings/api-keys - the "Custom secrets" section there accepts any
key name, not just the fixed providers) - one such key per calendar that
has a username set, named after that calendar's chosen label. That page
never echoes real values back to the browser (only a masked placeholder),
and .env is gitignored.

This split exists specifically because InkyPi's core settings-save/-load
path (src/blueprints/plugin.py, not something a plugin can override)
always re-embeds a plugin instance's *entire* stored settings dict back
into the page as JSON whenever you reopen its settings for editing - so
anything stored directly in a calendar entry (like the URL/color already
are) gets shipped back to the browser in plaintext every time, regardless
of how the input field itself is rendered. Routing the password through
the env-key store instead of the entries list is the only way available
here to avoid that. It's still a plaintext-on-disk secret the app process
can read whenever it wants - not "secure" in an absolute sense, just the
best this app's existing tools allow.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.settings_schema import (
    callout,
    field,
    option,
    option_group,
    row,
    schema,
    section,
    widget,
)
from plugins.calendar_auth.constants import FONT_SIZES, LOCALE_GROUPS, LOCALE_MAP
from PIL import ImageColor
import icalendar
import re
import recurring_ical_events
import logging
import requests
from datetime import datetime, timedelta
from utils.time_utils import get_timezone

logger = logging.getLogger(__name__)

VALID_LABEL = re.compile(r"^[A-Za-z0-9_]+$")


class CalendarAuth(BasePlugin):
    def build_settings_schema(self):
        return schema(
            section(
                "Calendars",
                widget(
                    "calendar-auth-repeater",
                    template="widgets/calendar_auth_repeater.html",
                ),
                callout(
                    "Leave username blank for a calendar that doesn't need login. "
                    "When a username is set, give that calendar a short credential "
                    "label (letters/numbers/underscore only), then set its password "
                    "under CALENDAR_AUTH_PASSWORD_<LABEL> in the Custom secrets "
                    "section of the API keys page. The password itself is never "
                    "entered here and never stored in this plugin's settings.",
                ),
            ),
            section(
                "Layout",
                row(
                    field(
                        "viewMode",
                        "radio_segment",
                        label="View",
                        default="dayGridMonth",
                        options=[
                            option("timeGridDay", "Day"),
                            option("timeGridWeek", "Week"),
                            option("dayGrid", "Multi-Week"),
                            option("dayGridMonth", "Month"),
                            option("listMonth", "List"),
                        ],
                    ),
                    field(
                        "language",
                        "select",
                        label="Language",
                        default="en",
                        options=[
                            option_group(
                                group_label,
                                *[option(code, name) for code, name in locales],
                            )
                            for group_label, locales in LOCALE_GROUPS
                        ],
                    ),
                    field(
                        "fontSize",
                        "select",
                        label="Font Size",
                        default="normal",
                        options=[
                            option(key, key.replace("-", " ").title())
                            for key in FONT_SIZES
                        ],
                    ),
                ),
            ),
            section(
                "Display",
                row(
                    field(
                        "displayTitle",
                        "checkbox",
                        label="Title",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                    field(
                        "displayWeekends",
                        "checkbox",
                        label="Weekends",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                    field(
                        "displayEventTime",
                        "checkbox",
                        label="Event Time",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                ),
                row(
                    field(
                        "displayNowIndicator",
                        "checkbox",
                        label="Now Indicator",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                        visible_if={
                            "field": "viewMode",
                            "operator": "in",
                            "values": ["timeGridDay", "timeGridWeek"],
                        },
                    ),
                    field(
                        "nowIndicatorColor",
                        "color",
                        label="Now Indicator Color",
                        default="#007BFF",
                        visible_if={
                            "field": "viewMode",
                            "operator": "in",
                            "values": ["timeGridDay", "timeGridWeek"],
                        },
                    ),
                    field(
                        "displayPreviousDays",
                        "checkbox",
                        label="Include Previous Days",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                        visible_if={"field": "viewMode", "equals": "timeGridWeek"},
                    ),
                ),
                row(
                    field(
                        "weekStartDay",
                        "select",
                        label="Week Starts On",
                        default="1",
                        options=[
                            option("0", "Sunday"),
                            option("1", "Monday"),
                            option("2", "Tuesday"),
                            option("3", "Wednesday"),
                            option("4", "Thursday"),
                            option("5", "Friday"),
                            option("6", "Saturday"),
                        ],
                        visible_if={
                            "field": "viewMode",
                            "operator": "in",
                            "values": ["timeGridWeek", "dayGrid", "dayGridMonth"],
                        },
                    ),
                    field(
                        "startTimeInterval",
                        "time",
                        label="Start Time",
                        visible_if={
                            "field": "viewMode",
                            "operator": "in",
                            "values": ["timeGridDay", "timeGridWeek"],
                        },
                    ),
                    field(
                        "endTimeInterval",
                        "time",
                        label="End Time",
                        visible_if={
                            "field": "viewMode",
                            "operator": "in",
                            "values": ["timeGridDay", "timeGridWeek"],
                        },
                    ),
                ),
                row(
                    field(
                        "displayWeeks",
                        "number",
                        label="Weeks to Show",
                        min=1,
                        max=8,
                        default="4",
                        visible_if={"field": "viewMode", "equals": "dayGrid"},
                    ),
                ),
            ),
        )

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        template_params['locale_map'] = LOCALE_MAP
        return template_params

    def generate_image(self, settings, device_config):
        entries = self._parse_entries(
            settings.get('calendarAuthURLs[]'),
            settings.get('calendarAuthColors[]'),
            settings.get('calendarAuthUsernames[]'),
            settings.get('calendarAuthLabels[]'),
        )
        view = settings.get("viewMode")

        if not view:
            raise RuntimeError("View is required")
        elif view not in ["timeGridDay", "timeGridWeek", "dayGrid", "dayGridMonth", "listMonth"]:
            raise RuntimeError("Invalid view")

        if not entries:
            raise RuntimeError("At least one calendar URL is required")

        calendars = []
        for entry in entries:
            calendars.append((entry["url"], entry["color"], self._auth_for_entry(entry, device_config)))

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = get_timezone(timezone)

        current_dt = datetime.now(tz)
        start, end = self.get_view_range(view, current_dt, settings)
        logger.debug(f"Fetching events for {start} --> [{current_dt}] --> {end}")
        events = self.fetch_ics_events(calendars, tz, start, end)
        if not events:
            logger.warning("No events found for ics url")

        if view == 'timeGridWeek' and settings.get("displayPreviousDays") != "true":
            view = 'timeGrid'

        template_params = {
            "view": view,
            "events": events,
            "current_dt": current_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
            "timezone": timezone,
            "plugin_settings": settings,
            "time_format": time_format,
            "font_scale": FONT_SIZES.get(settings.get("fontSize", "normal"))
        }

        image = self.render_image(dimensions, "calendar_auth.html", "calendar_auth.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def _parse_entries(self, urls, colors, usernames, labels):
        urls = urls or []
        colors = colors or []
        usernames = usernames or []
        labels = labels or []
        entries = []
        for i, raw_url in enumerate(urls):
            url = (raw_url or "").strip()
            if not url:
                continue
            entries.append({
                "url": url,
                "color": (colors[i] if i < len(colors) and colors[i] else "#007BFF"),
                "username": (usernames[i].strip() if i < len(usernames) and usernames[i] else ""),
                "label": (labels[i].strip() if i < len(labels) and labels[i] else ""),
            })
        return entries

    def _auth_for_entry(self, entry, device_config):
        if not entry["username"]:
            return None
        label = entry["label"]
        if not label or not VALID_LABEL.match(label):
            raise RuntimeError(
                f"Calendar '{entry['url']}' has a username but no valid credential label "
                "(letters/numbers/underscore only) - set one so its password can be looked up."
            )
        key = f"CALENDAR_AUTH_PASSWORD_{label.upper()}"
        password = device_config.load_env_key(key)
        if not password:
            raise RuntimeError(f"Username set for '{entry['url']}' but {key} isn't configured. Set it on the API Keys page.")
        return (entry["username"], password)

    def fetch_ics_events(self, calendars, tz, start_range, end_range):
        parsed_events = []

        for calendar_url, color, auth in calendars:
            cal = self.fetch_calendar(calendar_url, auth)
            events = recurring_ical_events.of(cal).between(start_range, end_range)
            contrast_color = self.get_contrast_color(color)

            for event in events:
                start, end, all_day = self.parse_data_points(event, tz)
                parsed_event = {
                    "title": str(event.get("summary")),
                    "start": start,
                    "backgroundColor": color,
                    "textColor": contrast_color,
                    "allDay": all_day
                }
                if end:
                    parsed_event['end'] = end

                parsed_events.append(parsed_event)

        return parsed_events

    def get_view_range(self, view, current_dt, settings):
        start = datetime(current_dt.year, current_dt.month, current_dt.day)
        if view == "timeGridDay":
            end = start + timedelta(days=1)
        elif view == "timeGridWeek":
            if settings.get("displayPreviousDays") == "true":
                week_start_day = int(settings.get("weekStartDay", 1))
                python_week_start = (week_start_day - 1) % 7
                offset = (current_dt.weekday() - python_week_start) % 7
                start = current_dt - timedelta(days=offset)
                start = datetime(start.year, start.month, start.day)
            end = start + timedelta(days=7)
        elif view == "dayGrid":
            start = current_dt - timedelta(weeks=1)
            end = current_dt + timedelta(weeks=int(settings.get("displayWeeks") or 4))
        elif view == "dayGridMonth":
            start = datetime(current_dt.year, current_dt.month, 1) - timedelta(weeks=1)
            end = datetime(current_dt.year, current_dt.month, 1) + timedelta(weeks=6)
        elif view == "listMonth":
            end = start + timedelta(weeks=5)
        return start, end

    def parse_data_points(self, event, tz):
        all_day = False
        dtstart = event.decoded("dtstart")
        if isinstance(dtstart, datetime):
            start = dtstart.astimezone(tz).isoformat()
        else:
            start = dtstart.isoformat()
            all_day = True

        end = None
        if "dtend" in event:
            dtend = event.decoded("dtend")
            if isinstance(dtend, datetime):
                end = dtend.astimezone(tz).isoformat()
            else:
                end = dtend.isoformat()
        elif "duration" in event:
            duration = event.decoded("duration")
            end = (dtstart + duration).isoformat()
        return start, end, all_day

    def fetch_calendar(self, calendar_url, auth):
        # workaround for webcal urls
        if calendar_url.startswith("webcal://"):
            calendar_url = calendar_url.replace("webcal://", "https://")
        try:
            response = requests.get(calendar_url, auth=auth, timeout=30)
            response.raise_for_status()
            return icalendar.Calendar.from_ical(response.text)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch iCalendar url: {str(e)}")

    def get_contrast_color(self, color):
        """
        Returns '#000000' (black) or '#ffffff' (white) depending on the contrast
        against the given color.
        """
        r, g, b = ImageColor.getrgb(color)
        # YIQ formula to estimate brightness
        yiq = (r * 299 + g * 587 + b * 114) / 1000

        return '#000000' if yiq >= 150 else '#ffffff'
