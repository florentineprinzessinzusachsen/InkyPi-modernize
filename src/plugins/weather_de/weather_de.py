"""Weather (Germany) plugin for InkyPi.

A clone of the built-in `weather` plugin (same layout, same OpenWeatherMap
and Open-Meteo providers) with two additions aimed at better data quality
for German locations:

1. A third provider, Bright Sky (api.brightsky.dev) - a free, keyless,
   community-run wrapper around the DWD's (Deutscher Wetterdienst) own
   data: real SYNOP station observations for "current" conditions, and
   the DWD's MOSMIX model for the forecast/hourly data. Bright Sky has no
   sunrise/sunset, UV index, or air quality fields, so those are filled in
   from elsewhere: sunrise/sunset via the `astral` library (already a
   dependency, used here for moon-phase like the Open-Meteo path already
   does), and UV index / air quality by reusing the existing Open-Meteo
   air-quality endpoint (same one the Open-Meteo provider already calls).
   Bright Sky also has no "feels like" temperature field, so that value
   falls back to the actual temperature.

2. For the existing Open-Meteo provider, an added (opt-in, default
   unchanged) `openMeteoModel` setting to pin the forecast to DWD's
   ICON-D2 model (`models=icon_d2`, 2.2km resolution over Germany/Central
   Europe) instead of Open-Meteo's default `best_match` blend.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.settings_schema import field, option, row, schema, section, widget
from PIL import Image
import os
import requests
import logging
from datetime import datetime, timedelta, timezone, date
from astral import moon, Observer
from astral.sun import sun as astral_sun
from utils.time_utils import get_timezone
from utils.app_utils import resolve_path
from io import BytesIO
import math

logger = logging.getLogger(__name__)

def get_moon_phase_name(phase_age: float) -> str:
    """Determines the name of the lunar phase based on the age of the moon."""
    PHASES_THRESHOLDS = [
        (1.0, "newmoon"),
        (7.0, "waxingcrescent"),
        (8.5, "firstquarter"),
        (14.0, "waxinggibbous"),
        (15.5, "fullmoon"),
        (22.0, "waninggibbous"),
        (23.5, "lastquarter"),
        (29.0, "waningcrescent"),
    ]

    for threshold, phase_name in PHASES_THRESHOLDS:
        if phase_age <= threshold:
            return phase_name
    return "newmoon"

UNITS = {
    "standard": {
        "temperature": "K",
        "speed": "m/s",
        "distance":"km"
    },
    "metric": {
        "temperature": "°C",
        "speed": "m/s",
        "distance":"km"

    },
    "imperial": {
        "temperature": "°F",
        "speed": "mph",
        "distance":"mi"
    }
}

WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"
AIR_QUALITY_URL = "http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={long}&appid={api_key}"
GEOCODING_URL = "http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={long}&hourly=weather_code,temperature_2m,precipitation,precipitation_probability,relative_humidity_2m,surface_pressure,visibility&daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max&current=temperature,windspeed,winddirection,is_day,precipitation,weather_code,apparent_temperature&timezone=auto&models={model}&forecast_days={forecast_days}"
OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={long}&hourly=european_aqi,uv_index,uv_index_clear_sky&timezone=auto"
OPEN_METEO_UNIT_PARAMS = {
    "standard": "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",  # temperature is converted to Kelvin later
    "metric":   "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "imperial": "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
}
# Values valid for Open-Meteo's `models=` param. "best_match" (default) lets
# Open-Meteo pick whichever of its blended models best covers the queried
# location; "icon_d2" pins to the DWD's high-resolution (2.2km) model, which
# only covers Germany/Central Europe but is the most accurate option there.
OPEN_METEO_MODELS = ("best_match", "icon_d2")

BRIGHT_SKY_CURRENT_URL = "https://api.brightsky.dev/current_weather?lat={lat}&lon={long}"
BRIGHT_SKY_WEATHER_URL = "https://api.brightsky.dev/weather?lat={lat}&lon={long}&date={date}&last_date={last_date}"

# Bright Sky's `icon` field uses a fixed, Dark-Sky-compatible vocabulary.
# Conditions without a day/night distinction in that vocabulary (rain,
# snow, etc.) map to icon codes that only exist as "d" files in icons/ -
# there's nothing to lose by not having a night variant for those.
BRIGHT_SKY_ICON_MAP = {
    "clear-day": "01d",
    "clear-night": "01n",
    "partly-cloudy-day": "02d",
    "partly-cloudy-night": "02n",
    "cloudy": "04d",
    "fog": "50d",
    "wind": "03d",
    "rain": "10d",
    "sleet": "56d",
    "snow": "13d",
    "hail": "13d",
    "thunderstorm": "11d",
}

# Locale data for date/day name translation.
# days: full weekday names (0=Monday), days_short: abbreviated (0=Monday),
# months: full month names (0=January).
# ui: translated UI strings used in the weather template and data points.
# "en": None uses strftime/English directly.
LOCALE_DATA = {
    "de": {
        "days":       ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
        "days_short": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "months":     ["Januar", "Februar", "März", "April", "Mai", "Juni",
                       "Juli", "August", "September", "Oktober", "November", "Dezember"],
        "ui": {
            "last_refresh": "Letzte Aktualisierung",
            "feels_like": "Gefühlt",
            "sunrise": "Sonnenaufgang",
            "sunset": "Sonnenuntergang",
            "wind": "Wind",
            "humidity": "Luftfeuchtigkeit",
            "pressure": "Luftdruck",
            "uv_index": "UV-Index",
            "visibility": "Sichtweite",
            "air_quality": "Luftqualität",
            "aqi_scale": ["Gut", "Mäßig", "Mittelmäßig", "Schlecht", "Sehr schlecht"],
            "aqi_scale_om": ["Gut", "Mäßig", "Mittelmäßig", "Schlecht", "Sehr schlecht", "Extrem schlecht"],
        },
    },
    "en": None,  # English uses strftime directly
    "es": {
        "days":       ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
        "days_short": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "months":     ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                       "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
        "ui": {
            "last_refresh": "Última actualización",
            "feels_like": "Sensación",
            "sunrise": "Amanecer",
            "sunset": "Atardecer",
            "wind": "Viento",
            "humidity": "Humedad",
            "pressure": "Presión",
            "uv_index": "Índice UV",
            "visibility": "Visibilidad",
            "air_quality": "Calidad aire",
            "aqi_scale":    ["Buena", "Aceptable", "Moderada", "Mala", "Muy mala"],
            "aqi_scale_om": ["Buena", "Aceptable", "Moderada", "Mala", "Muy mala", "Extrema"],
        },
    },
    "fr": {
        "days":       ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"],
        "days_short": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
        "months":     ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                       "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"],
        "ui": {
            "last_refresh": "Dernière mise à jour",
            "feels_like": "Ressenti",
            "sunrise": "Lever du soleil",
            "sunset": "Coucher du soleil",
            "wind": "Vent",
            "humidity": "Humidité",
            "pressure": "Pression",
            "uv_index": "Indice UV",
            "visibility": "Visibilité",
            "air_quality": "Qualité de l'air",
            "aqi_scale": ["Bonne", "Correcte", "Moyenne", "Mauvaise", "Très mauvaise"],
            "aqi_scale_om": ["Bonne", "Correcte", "Moyenne", "Mauvaise", "Très mauvaise", "Extrêmement mauvaise"],
        },
    },
    "id": {
        "days":       ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"],
        "days_short": ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"],
        "months":     ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
                       "Juli", "Agustus", "September", "Oktober", "November", "Desember"],
        "ui": {
            "last_refresh": "Pembaruan terakhir",
            "feels_like": "Terasa",
            "sunrise": "Matahari terbit",
            "sunset": "Matahari terbenam",
            "wind": "Angin",
            "humidity": "Kelembaban",
            "pressure": "Tekanan",
            "uv_index": "Indeks UV",
            "visibility": "Jarak pandang",
            "air_quality": "Kualitas udara",
            "aqi_scale":    ["Baik", "Sedang", "Buruk ringan", "Buruk", "Sangat buruk"],
            "aqi_scale_om": ["Baik", "Sedang", "Buruk ringan", "Buruk", "Sangat buruk", "Berbahaya"],
        },
    },
    "it": {
        "days":       ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
        "days_short": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
        "months":     ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                       "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
        "ui": {
            "last_refresh": "Ultimo aggiornamento",
            "feels_like": "Percepita",
            "sunrise": "Alba",
            "sunset": "Tramonto",
            "wind": "Vento",
            "humidity": "Umidità",
            "pressure": "Pressione",
            "uv_index": "Indice UV",
            "visibility": "Visibilità",
            "air_quality": "Qualità aria",
            "aqi_scale":    ["Buona", "Discreta", "Moderata", "Scarsa", "Pessima"],
            "aqi_scale_om": ["Buona", "Discreta", "Moderata", "Scarsa", "Pessima", "Pericolosa"],
        },
    },
    "nl": {
        "days":       ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"],
        "days_short": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
        "months":     ["Januari", "Februari", "Maart", "April", "Mei", "Juni",
                       "Juli", "Augustus", "September", "Oktober", "November", "December"],
        "ui": {
            "last_refresh": "Laatste verversing",
            "feels_like": "Voelt als",
            "sunrise": "Zonsopgang",
            "sunset": "Zonsondergang",
            "wind": "Wind",
            "humidity": "Vochtigheid",
            "pressure": "Luchtdruk",
            "uv_index": "UV-index",
            "visibility": "Zichtbaarheid",
            "air_quality": "Luchtkwaliteit",
            "aqi_scale":    ["Goed", "Matig", "Onvoldoende", "Slecht", "Zeer slecht"],
            "aqi_scale_om": ["Goed", "Matig", "Onvoldoende", "Slecht", "Zeer slecht", "Gevaarlijk"],
        },
    },
    "pt": {
        "days":       ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"],
        "days_short": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
        "months":     ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                       "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
        "ui": {
            "last_refresh": "Última atualização",
            "feels_like": "Sensação",
            "sunrise": "Nascer do sol",
            "sunset": "Pôr do sol",
            "wind": "Vento",
            "humidity": "Umidade",
            "pressure": "Pressão",
            "uv_index": "Índice UV",
            "visibility": "Visibilidade",
            "air_quality": "Qualidade ar",
            "aqi_scale":    ["Boa", "Razoável", "Moderada", "Ruim", "Muito ruim"],
            "aqi_scale_om": ["Boa", "Razoável", "Moderada", "Ruim", "Muito ruim", "Péssima"],
        },
    },
}


def get_localized_date(dt, language):
    """Return a localized date string equivalent to strftime('%A, %B %d')."""
    locale = LOCALE_DATA.get(language)
    if locale:
        day_name = locale["days"][dt.weekday()]
        month_name = locale["months"][dt.month - 1]
        return f"{day_name}, {month_name} {dt.day:02d}"
    return dt.strftime("%A, %B %d")


def get_localized_day_short(dt, language):
    """Return a localized abbreviated weekday name equivalent to strftime('%a')."""
    locale = LOCALE_DATA.get(language)
    if locale:
        return locale["days_short"][dt.weekday()]
    return dt.strftime("%a")


def get_ui_label(key, language, default=None):
    """Return a translated UI string for the given key and language."""
    locale = LOCALE_DATA.get(language)
    if locale and "ui" in locale:
        return locale["ui"].get(key, default or key)
    return default or key


class WeatherDe(BasePlugin):
    def build_settings_schema(self):
        return schema(
            section(
                "Language",
                field(
                    "language",
                    "select",
                    label="Language",
                    default="de",
                    options=[
                        option("nl", "Dutch"),
                        option("en", "English"),
                        option("fr", "French"),
                        option("de", "German"),
                        option("id", "Indonesian"),
                        option("it", "Italian"),
                        option("pt", "Portuguese"),
                        option("es", "Spanish"),
                    ],
                ),
            ),
            section(
                "Location",
                widget("weather-map", template="widgets/weather_map.html"),
            ),
            section(
                "Data",
                row(
                    field(
                        "weatherProvider",
                        "select",
                        label="Weather Provider",
                        default="OpenMeteo",
                        options=[
                            option("OpenMeteo", "Open-Meteo"),
                            option("BrightSky", "Bright Sky (DWD, Germany)"),
                            option("OpenWeatherMap", "OpenWeatherMap"),
                        ],
                    ),
                    field(
                        "units",
                        "select",
                        label="Units",
                        default="imperial",
                        options=[
                            option("imperial", "Imperial (°F)"),
                            option("metric", "Metric (°C)"),
                            option("standard", "Standard (K)"),
                        ],
                    ),
                    field(
                        "weatherTimeZone",
                        "select",
                        label="Time Zone",
                        default="locationTimeZone",
                        options=[
                            option("locationTimeZone", "Use Location Time Zone"),
                            option("localTimeZone", "Use Local Time Zone"),
                        ],
                        visible_if={
                            "field": "weatherProvider",
                            "equals": "OpenWeatherMap",
                        },
                    ),
                ),
                row(
                    field(
                        "openMeteoModel",
                        "select",
                        label="Open-Meteo Model",
                        default="best_match",
                        options=[
                            option("best_match", "Best Match (default)"),
                            option("icon_d2", "DWD ICON-D2 (Germany/Central Europe, 2.2km)"),
                        ],
                        hint=(
                            "Best Match blends whichever model Open-Meteo rates highest "
                            "for this location. Pinning to ICON-D2 forces the DWD's own "
                            "high-resolution model - more accurate over Germany, but it "
                            "only covers Germany/Central Europe and only forecasts about "
                            "2 days ahead (the daily forecast will show fewer days than "
                            "configured once pinned)."
                        ),
                        visible_if={"field": "weatherProvider", "equals": "OpenMeteo"},
                    ),
                ),
            ),
            section(
                "Title",
                row(
                    field(
                        "titleSelection",
                        "radio_segment",
                        label="Title Source",
                        default="location",
                        options=[
                            option("location", "Location"),
                            option("custom", "Custom"),
                        ],
                        visible_if={
                            "field": "weatherProvider",
                            "equals": "OpenWeatherMap",
                        },
                    ),
                    field(
                        "customTitle",
                        label="Custom Title",
                        placeholder="Custom forecast title",
                    ),
                ),
            ),
            section(
                "Display",
                row(
                    field(
                        "displayRefreshTime",
                        "checkbox",
                        label="Refresh Time",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                    field(
                        "displayMetrics",
                        "checkbox",
                        label="Metrics",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                    field(
                        "displayGraph",
                        "checkbox",
                        label="Weather Graph",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                ),
                row(
                    field(
                        "displayRain",
                        "checkbox",
                        label="Rain Amount",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="false",
                    ),
                    field(
                        "moonPhase",
                        "checkbox",
                        label="Moon Phase",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="false",
                    ),
                ),
                row(
                    field(
                        "displayGraphIcons",
                        "checkbox",
                        label="Graph Icons",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="false",
                    ),
                    field(
                        "graphIconStep",
                        "select",
                        label="Icon Step (hours)",
                        default="2",
                        options=[
                            option("1", "1"),
                            option("2", "2"),
                            option("4", "4"),
                            option("6", "6"),
                            option("12", "12"),
                        ],
                        visible_if={"field": "displayGraphIcons", "equals": "true"},
                    ),
                ),
                row(
                    field(
                        "displayForecast",
                        "checkbox",
                        label="Forecast",
                        submit_unchecked=True,
                        checked_value="true",
                        unchecked_value="false",
                        default="true",
                    ),
                    field(
                        "forecastDays",
                        "select",
                        label="Forecast Days",
                        default="7",
                        options=[
                            option("3", "3"),
                            option("5", "5"),
                            option("7", "7"),
                        ],
                        visible_if={"field": "displayForecast", "equals": "true"},
                    ),
                ),
            ),
        )

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        lat = float(settings.get('latitude'))
        long = float(settings.get('longitude'))
        if not lat or not long:
            raise RuntimeError("Latitude and Longitude are required.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        weather_provider = settings.get('weatherProvider', 'OpenMeteo')
        title = settings.get('customTitle', '')
        language = settings.get('language', 'en')
        if language not in LOCALE_DATA:
            language = 'en'

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = get_timezone(timezone)

        try:
            if weather_provider == "OpenWeatherMap":
                api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
                if not api_key:
                    raise RuntimeError("Open Weather Map API Key not configured.")
                weather_data = self.get_weather_data(api_key, units, lat, long)
                aqi_data = self.get_air_quality(api_key, lat, long)
                if settings.get('titleSelection', 'location') == 'location':
                    title = self.get_location(api_key, lat, long)
                if settings.get('weatherTimeZone', 'locationTimeZone') == 'locationTimeZone':
                    logger.info("Using location timezone for OpenWeatherMap data.")
                    wtz = self.parse_timezone(weather_data)
                    template_params = self.parse_weather_data(weather_data, aqi_data, wtz, units, time_format, lat, language)
                else:
                    logger.info("Using configured timezone for OpenWeatherMap data.")
                    template_params = self.parse_weather_data(weather_data, aqi_data, tz, units, time_format, lat, language)
            elif weather_provider == "OpenMeteo":
                forecast_days = 7
                model = settings.get('openMeteoModel', 'best_match')
                if model not in OPEN_METEO_MODELS:
                    model = 'best_match'
                weather_data = self.get_open_meteo_data(lat, long, units, forecast_days + 1, model)
                aqi_data = self.get_open_meteo_air_quality(lat, long)
                template_params = self.parse_open_meteo_data(weather_data, aqi_data, tz, units, time_format, lat, language)
            elif weather_provider == "BrightSky":
                forecast_days = 7
                current_data = self.get_bright_sky_current(lat, long)
                forecast_data = self.get_bright_sky_forecast(lat, long, forecast_days + 1)
                aqi_data = self.get_open_meteo_air_quality(lat, long)
                template_params = self.parse_bright_sky_data(current_data, forecast_data.get("weather", []), aqi_data, tz, units, time_format, lat, long, language)
            else:
                raise RuntimeError(f"Unknown weather provider: {weather_provider}")

            template_params['title'] = title
        except Exception as e:
            logger.error(f"{weather_provider} request failed: {str(e)}")
            raise RuntimeError(f"{weather_provider} request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        template_params["plugin_settings"] = settings

        # weather_de.html references {{static_dir}}/scripts/chart.js for the
        # hourly-temperature graph, but nothing ever sets that variable -
        # same in the built-in weather plugin's own template, which has the
        # identical dead reference (not something specific to this plugin,
        # and there's no working version to copy). Chromium screenshots run
        # via file:// with no Flask request context, so url_for() isn't
        # available here either; resolve_path() + to_file_url() is the same
        # mechanism render_image() already uses for local fonts/CSS.
        template_params["static_dir"] = self.to_file_url(resolve_path("static"))

        # Add last refresh time
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh_time = now.strftime("%Y-%m-%d %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time

        image = self.render_image(dimensions, "weather_de.html", "weather_de.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def parse_weather_data(self, weather_data, aqi_data, tz, units, time_format, lat, language="en"):
        current = weather_data.get("current")
        daily_forecast = weather_data.get("daily", [])
        dt = datetime.fromtimestamp(current.get('dt'), tz=timezone.utc).astimezone(tz)
        current_icon = current.get("weather")[0].get("icon")
        icon_codes_to_preserve = ["01", "02", "10"]
        icon_code = current_icon[:2]
        current_suffix = current_icon[-1]

        if icon_code not in icon_codes_to_preserve:
            if current_icon.endswith('n'):
                current_icon = current_icon.replace("n", "d")
        data = {
            "current_date": get_localized_date(dt, language),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temp"))),
            "feels_like": str(round(current.get("feels_like"))),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }
        data['forecast'] = self.parse_forecast(weather_data.get('daily'), tz, current_suffix, lat, language)
        data['data_points'] = self.parse_data_points(weather_data, aqi_data, tz, units, time_format, language)
        data['feels_like_label'] = get_ui_label('feels_like', language, 'Feels Like')
        data['last_refresh_label'] = get_ui_label('last_refresh', language, 'Last refresh')

        data['hourly_forecast'] = self.parse_hourly(weather_data.get('hourly'), tz, time_format, units, daily_forecast)
        return data

    def parse_open_meteo_data(self, weather_data, aqi_data, tz, units, time_format, lat, language="en"):
        current = weather_data.get("current", {})
        daily = weather_data.get('daily', {})
        dt = datetime.fromisoformat(current.get('time')).astimezone(tz) if current.get('time') else datetime.now(tz)
        weather_code = current.get("weather_code", 0)
        is_day = current.get("is_day", 1)
        current_icon = self.map_weather_code_to_icon(weather_code, is_day)

        temperature_conversion = 273.15 if units == "standard" else 0.

        data = {
            "current_date": get_localized_date(dt, language),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temperature", 0) + temperature_conversion)),
            "feels_like": str(round(current.get("apparent_temperature", current.get("temperature", 0)) + temperature_conversion)),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }

        data['forecast'] = self.parse_open_meteo_forecast(weather_data.get('daily', {}), units, tz, is_day, lat, language)
        data['data_points'] = self.parse_open_meteo_data_points(weather_data, aqi_data, units, tz, time_format, language)
        data['feels_like_label'] = get_ui_label('feels_like', language, 'Feels Like')
        data['last_refresh_label'] = get_ui_label('last_refresh', language, 'Last refresh')

        data['hourly_forecast'] = self.parse_open_meteo_hourly(weather_data.get('hourly', {}), units, tz, time_format, daily.get('sunrise', []), daily.get('sunset', []))
        return data

    def parse_bright_sky_data(self, current_data, hourly_records, aqi_data, tz, units, time_format, lat, long, language="en"):
        current = current_data.get("weather", {})
        dt = datetime.fromisoformat(current.get("timestamp")).astimezone(tz) if current.get("timestamp") else datetime.now(tz)
        current_icon = self.map_bright_sky_icon(current.get("icon"))

        temp_c = current.get("temperature")
        # Bright Sky has no apparent/"feels like" temperature field.
        feels_like_c = temp_c

        data = {
            "current_date": get_localized_date(dt, language),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(self.convert_temp_c(temp_c, units))),
            "feels_like": str(round(self.convert_temp_c(feels_like_c, units))),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }

        sunrise_dt, sunset_dt = self.get_sun_times(lat, long, dt.date(), tz)

        data['forecast'] = self.parse_bright_sky_forecast(hourly_records, units, tz, lat, language)
        data['data_points'] = self.parse_bright_sky_data_points(current, aqi_data, units, tz, sunrise_dt, sunset_dt, time_format, language)
        data['feels_like_label'] = get_ui_label('feels_like', language, 'Feels Like')
        data['last_refresh_label'] = get_ui_label('last_refresh', language, 'Last refresh')
        data['hourly_forecast'] = self.parse_bright_sky_hourly(hourly_records, units, tz, time_format)
        return data

    def map_weather_code_to_icon(self, weather_code, is_day):

        icon = "01d" # Default to clear day icon

        if weather_code in [0]:   # Clear sky
            icon = "01d"
        elif weather_code in [1]: # Mainly clear
            icon = "022d"
        elif weather_code in [2]: # Partly cloudy
            icon = "02d"
        elif weather_code in [3]: # Overcast
            icon = "04d"
        elif weather_code in [51, 61, 80]: # Drizzle, showers, rain: Light
            icon = "51d"
        elif weather_code in [53, 63, 81]: # Drizzle, showers, rain: Moderatr
            icon = "53d"
        elif weather_code in [55, 65, 82]: # Drizzle, showers, rain: Heavy
            icon = "09d"
        elif weather_code in [45]: # Fog
            icon = "50d"
        elif weather_code in [48]: # Icy fog
            icon = "48d"
        elif weather_code in [56, 66]: # Light freezing Drizzle
            icon = "56d"
        elif weather_code in [57, 67]: # Freezing Drizzle
            icon = "57d"
        elif weather_code in [71, 85]: # Snow fall: Slight
            icon = "71d"
        elif weather_code in [73]:     # Snow fall: Moderate
            icon = "73d"
        elif weather_code in [75, 86]: # Snow fall: Heavy
            icon = "13d"
        elif weather_code in [77]:     # Snow grain
            icon = "77d"
        elif weather_code in [95]: # Thunderstorm
            icon = "11d"
        elif weather_code in [96, 99]: # Thunderstorm with slight and heavy hail
            icon = "11d"

        if is_day == 0:
            if icon == "01d":
                icon = "01n"      # Clear sky night
            elif icon == "022d":
                icon = "022n"     # Mainly clear night
            elif icon == "02d":
                icon = "02n"      # Partly cloudy night
            elif icon == "10d":
                icon = "10n"      # Rain night

        return icon

    def map_bright_sky_icon(self, icon_name):
        return BRIGHT_SKY_ICON_MAP.get(icon_name, "01d")

    def get_moon_phase_icon_path(self, phase_name: str, lat: float) -> str:
        """Determines the path to the moon icon, inverting it if the location is in the Southern Hemisphere."""
        # Waxing, Waning, First and Last quarter phases are inverted between hemispheres.
        if lat < 0: # Southern Hemisphere
            if phase_name == "waxingcrescent":
                phase_name = "waningcrescent"
            elif phase_name == "waxinggibbous":
                phase_name = "waninggibbous"
            elif phase_name == "waningcrescent":
                phase_name = "waxingcrescent"
            elif phase_name == "waninggibbous":
                phase_name = "waxinggibbous"
            elif phase_name == "firstquarter":
                phase_name = "lastquarter"
            elif phase_name == "lastquarter":
                phase_name = "firstquarter"

        return self.get_plugin_dir(f"icons/{phase_name}.png")

    def parse_forecast(self, daily_forecast, tz, current_suffix, lat, language="en"):
        """
        - daily_forecast: list of daily entries from One‑Call v3 (each has 'dt', 'weather', 'temp', 'moon_phase')
        - tz: your target tzinfo (e.g. from zoneinfo or pytz)
        """
        PHASES = [
            (0.0, "newmoon"),
            (0.25, "firstquarter"),
            (0.5, "fullmoon"),
            (0.75, "lastquarter"),
            (1.0, "newmoon"),
        ]

        def choose_phase_name(phase: float) -> str:
            for target, name in PHASES:
                if math.isclose(phase, target, abs_tol=1e-3):
                    return name
            if 0.0 < phase < 0.25:
                return "waxingcrescent"
            elif 0.25 < phase < 0.5:
                return "waxinggibbous"
            elif 0.5 < phase < 0.75:
                return "waninggibbous"
            else:
                return "waningcrescent"

        forecast = []
        icon_codes_to_apply_current_suffix = ["01", "02", "10"]
        for day in daily_forecast:
            # --- weather icon ---
            weather_icon = day["weather"][0]["icon"]  # e.g. "10d", "01n"
            icon_code = weather_icon[:2]
            if icon_code in icon_codes_to_apply_current_suffix:
                weather_icon_base = weather_icon[:-1]
                weather_icon = weather_icon_base + current_suffix
            else:
                if weather_icon.endswith('n'):
                    weather_icon = weather_icon.replace("n", "d")
            weather_icon = f"{icon_code}d"
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            # --- moon phase & icon ---
            moon_phase = float(day["moon_phase"])  # [0.0–1.0]
            phase_name_north_hemi = choose_phase_name(moon_phase)
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)
            # --- true illumination percent, no decimals ---
            illum_fraction = (1 - math.cos(2 * math.pi * moon_phase)) / 2
            moon_pct = f"{illum_fraction * 100:.0f}"

            # --- date & temps ---
            dt = datetime.fromtimestamp(day["dt"], tz=timezone.utc).astimezone(tz)
            day_label = get_localized_day_short(dt, language)

            pop = day.get("pop")

            forecast.append(
                {
                    "day": day_label,
                    "high": int(day["temp"]["max"]),
                    "low": int(day["temp"]["min"]),
                    "icon": weather_icon_path,
                    "moon_phase_pct": moon_pct,
                    "moon_phase_icon": moon_icon_path,
                    "rain_chance_pct": round(pop * 100) if pop is not None else None,
                }
            )

        return forecast

    def parse_open_meteo_forecast(self, daily_data, units, tz, is_day, lat, language="en"):
        """
        Parse the daily forecast from Open-Meteo API and calculate moon phase and illumination using the local 'astral' library.
        """
        times = daily_data.get('time', [])
        weather_codes = daily_data.get('weathercode', [])
        temp_max = daily_data.get('temperature_2m_max', [])
        temp_min = daily_data.get('temperature_2m_min', [])
        # ICON-D2 (pinned via the model toggle) never returns this field at
        # all, being a deterministic single run rather than an ensemble.
        rain_chances = daily_data.get('precipitation_probability_max', [])
        if units == "standard":
            temp_max = [T + 273.15 for T in temp_max]
            temp_min = [T + 273.15 for T in temp_min]

        forecast = []

        for i in range(0, len(times)):
            # Models with a short forecast horizon (e.g. ICON-D2, pinned via
            # the model toggle, only forecasts ~2 days ahead) return `null`
            # for days beyond what they cover - stop there instead of
            # showing a bogus 0deg entry for missing days.
            day_max = temp_max[i] if i < len(temp_max) else None
            day_min = temp_min[i] if i < len(temp_min) else None
            if day_max is None or day_min is None:
                break

            dt = datetime.fromisoformat(times[i]).replace(tzinfo=timezone.utc).astimezone(tz)
            day_label = get_localized_day_short(dt, language)

            code = weather_codes[i] if i < len(weather_codes) else 0
            weather_icon = self.map_weather_code_to_icon(code, is_day=1)
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            timestamp = int(dt.replace(hour=12, minute=0, second=0).timestamp())
            target_date: date = dt.date() + timedelta(days=1)

            try:
                phase_age = moon.phase(target_date)
                phase_name_north_hemi = get_moon_phase_name(phase_age)
                LUNAR_CYCLE_DAYS = 29.530588853
                phase_fraction = phase_age / LUNAR_CYCLE_DAYS
                illum_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
            except Exception as e:
                logger.error(f"Error calculating moon phase for {target_date}: {e}")
                illum_pct = 0
                phase_name_north_hemi = "newmoon"
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)

            rain_chance = rain_chances[i] if i < len(rain_chances) else None

            forecast.append({
                "day": day_label,
                "high": int(day_max),
                "low": int(day_min),
                "icon": weather_icon_path,
                "moon_phase_pct": f"{illum_pct:.0f}",
                "moon_phase_icon": moon_icon_path,
                "rain_chance_pct": round(rain_chance) if rain_chance is not None else None,
            })

        return forecast

    def parse_bright_sky_forecast(self, hourly_records, units, tz, lat, language="en"):
        """Aggregates Bright Sky's hourly records (SYNOP history + MOSMIX
        forecast, mixed) into one entry per local calendar day: high/low
        from the day's temperatures, condition icon from the record
        closest to local noon (always shown as the "day" icon variant,
        matching how the Open-Meteo forecast path always passes
        is_day=1), and moon phase computed the same way the Open-Meteo
        path already does (local 'astral' library, no API support for it)."""
        by_date = {}
        for rec in hourly_records:
            ts = rec.get("timestamp")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts).astimezone(tz)
            by_date.setdefault(dt.date(), []).append((dt, rec))

        forecast = []
        for day_date in sorted(by_date.keys()):
            day_records = by_date[day_date]
            temps = [rec.get("temperature") for _, rec in day_records if rec.get("temperature") is not None]
            if not temps:
                continue
            temp_max = self.convert_temp_c(max(temps), units)
            temp_min = self.convert_temp_c(min(temps), units)

            noon = datetime(day_date.year, day_date.month, day_date.day, 12, tzinfo=tz)
            _, noon_rec = min(day_records, key=lambda pair: abs((pair[0] - noon).total_seconds()))
            weather_icon = self.map_bright_sky_icon(noon_rec.get("icon"))
            if weather_icon.endswith('n'):
                weather_icon = weather_icon[:-1] + 'd'
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            day_label = get_localized_day_short(day_date, language)
            target_date: date = day_date + timedelta(days=1)

            try:
                phase_age = moon.phase(target_date)
                phase_name_north_hemi = get_moon_phase_name(phase_age)
                LUNAR_CYCLE_DAYS = 29.530588853
                phase_fraction = phase_age / LUNAR_CYCLE_DAYS
                illum_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
            except Exception as e:
                logger.error(f"Error calculating moon phase for {target_date}: {e}")
                illum_pct = 0
                phase_name_north_hemi = "newmoon"
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)

            # Bright Sky only gives precipitation_probability for MOSMIX
            # forecast hours (null for past/current-observation hours), so
            # a day made up entirely of already-passed hours has none.
            rain_chances = [rec.get("precipitation_probability") for _, rec in day_records if rec.get("precipitation_probability") is not None]
            rain_chance_pct = max(rain_chances) if rain_chances else None

            forecast.append({
                "day": day_label,
                "high": int(round(temp_max)),
                "low": int(round(temp_min)),
                "icon": weather_icon_path,
                "moon_phase_pct": f"{illum_pct:.0f}",
                "moon_phase_icon": moon_icon_path,
                "rain_chance_pct": rain_chance_pct,
            })

        return forecast

    def parse_hourly(self, hourly_forecast, tz, time_format, units, daily_forecast):
        hourly = []
        icon_codes_to_preserve = ["01", "02", "10"]

        sun_map = {}
        for day in daily_forecast:
            day_date = datetime.fromtimestamp(day['dt'], tz=timezone.utc).astimezone(tz).date()
            sun_map[day_date] = (day['sunrise'], day['sunset'])

        for hour in hourly_forecast[:24]:
            dt_epoch = hour.get('dt')
            dt = datetime.fromtimestamp(dt_epoch, tz=timezone.utc).astimezone(tz)
            rain_mm = hour.get("rain", {}).get("1h", 0.0)
            snow_mm = hour.get("snow", {}).get("1h", 0.0)
            total_precip_mm = rain_mm + snow_mm
            sunrise, sunset = sun_map.get(dt.date(), (0, 0))

            is_day = sunrise <= dt_epoch < sunset
            suffix = 'd' if is_day else 'n'

            raw_icon = hour.get("weather", [{}])[0].get("icon", "01d")
            icon_base = raw_icon[:2]
            icon_name = f"{icon_base}{suffix}" if icon_base in icon_codes_to_preserve else f"{icon_base}d"

            if units == "imperial":
                precip_value = total_precip_mm / 25.4
            else:
                precip_value = total_precip_mm
            hour_forecast = {
                "time": self.format_time(dt, time_format, hour_only=True),
                "temperature": int(hour.get("temp")),
                "precipitation": hour.get("pop"),
                "rain": round(precip_value, 2),
                "icon": self.get_plugin_dir(f'icons/{icon_name}.png')
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_open_meteo_hourly(self, hourly_data, units, tz, time_format, sunrises, sunsets):
        hourly = []
        times = hourly_data.get('time', [])
        temperatures = hourly_data.get('temperature_2m', [])
        if units == "standard":
            temperatures = [temperature + 273.15 for temperature in temperatures]
        precipitation_probabilities = hourly_data.get('precipitation_probability', [])
        rain = hourly_data.get('precipitation', [])
        codes = hourly_data.get('weather_code', [])

        sun_map = {}
        for sr_s, ss_s in zip(sunrises, sunsets):
            sr_dt = datetime.fromisoformat(sr_s).astimezone(tz)
            ss_dt = datetime.fromisoformat(ss_s).astimezone(tz)
            sun_map[sr_dt.date()] = (sr_dt, ss_dt)

        current_time_in_tz = datetime.now(tz)
        start_index = 0
        for i, time_str in enumerate(times):
            try:
                dt_hourly = datetime.fromisoformat(time_str).astimezone(tz)
                if dt_hourly.date() == current_time_in_tz.date() and dt_hourly.hour >= current_time_in_tz.hour:
                    start_index = i
                    break
                if dt_hourly.date() > current_time_in_tz.date():
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} in hourly data.")
                continue

        sliced_times = times[start_index:]
        sliced_temperatures = temperatures[start_index:]
        sliced_precipitation_probabilities = precipitation_probabilities[start_index:]
        sliced_rain = rain[start_index:]
        sliced_codes = codes[start_index:]

        for i in range(min(24, len(sliced_times))):
            # Short-horizon models (e.g. ICON-D2, pinned via the model
            # toggle) stop returning data - and ICON-D2 specifically never
            # returns precipitation_probability at all, being a
            # deterministic single run rather than an ensemble - so treat
            # missing/null values as "no data" (0) rather than crashing.
            temperature = sliced_temperatures[i] if i < len(sliced_temperatures) else None
            if temperature is None:
                break

            dt = datetime.fromisoformat(sliced_times[i]).astimezone(tz)
            sunrise, sunset = sun_map.get(dt.date(), (None, None))
            is_day = 0
            if sunrise and sunset:
                is_day = 1 if sunrise <= dt < sunset else 0
            code = sliced_codes[i] if i < len(sliced_codes) else 0
            icon_name = self.map_weather_code_to_icon(code, is_day)

            precip_prob = sliced_precipitation_probabilities[i] if i < len(sliced_precipitation_probabilities) else None
            rain_amount = sliced_rain[i] if i < len(sliced_rain) else None

            hour_forecast = {
                "time": self.format_time(dt, time_format, True),
                "temperature": int(temperature),
                "precipitation": (precip_prob / 100) if precip_prob is not None else 0,
                "rain": rain_amount if rain_amount is not None else 0,
                "icon": self.get_plugin_dir(f"icons/{icon_name}.png")
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_bright_sky_hourly(self, hourly_records, units, tz, time_format):
        """Bright Sky's `icon` field already encodes real day/night for the
        conditions that distinguish it (clear/partly-cloudy), computed by
        Bright Sky itself from actual sun position - no local is_day
        computation is needed here, unlike the Open-Meteo path."""
        parsed = []
        for rec in hourly_records:
            ts = rec.get("timestamp")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts).astimezone(tz)
            parsed.append((dt, rec))
        parsed.sort(key=lambda pair: pair[0])

        current_hour = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
        start_index = len(parsed)
        for i, (dt, _) in enumerate(parsed):
            if dt >= current_hour:
                start_index = i
                break

        hourly = []
        for dt, rec in parsed[start_index:start_index + 24]:
            temp_c = rec.get("temperature")
            temperature = self.convert_temp_c(temp_c, units) if temp_c is not None else 0.0
            precip_mm = rec.get("precipitation") or 0.0
            precip_value = precip_mm / 25.4 if units == "imperial" else precip_mm
            precip_prob = rec.get("precipitation_probability")
            icon_name = self.map_bright_sky_icon(rec.get("icon"))

            hourly.append({
                "time": self.format_time(dt, time_format, hour_only=True),
                "temperature": int(round(temperature)),
                "precipitation": (precip_prob / 100) if precip_prob is not None else 0,
                "rain": round(precip_value, 2),
                "icon": self.get_plugin_dir(f'icons/{icon_name}.png')
            })
        return hourly

    def parse_data_points(self, weather, air_quality, tz, units, time_format, language="en"):
        data_points = []
        sunrise_epoch = weather.get('current', {}).get("sunrise")

        if sunrise_epoch:
            sunrise_dt = datetime.fromtimestamp(sunrise_epoch, tz=timezone.utc).astimezone(tz)
            data_points.append({
                "key": "Sunrise",
                "label": get_ui_label("sunrise", language, "Sunrise"),
                "measurement": self.format_time(sunrise_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunrise_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logger.error(f"Sunrise not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        sunset_epoch = weather.get('current', {}).get("sunset")
        if sunset_epoch:
            sunset_dt = datetime.fromtimestamp(sunset_epoch, tz=timezone.utc).astimezone(tz)
            data_points.append({
                "key": "Sunset",
                "label": get_ui_label("sunset", language, "Sunset"),
                "measurement": self.format_time(sunset_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunset_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logger.error(f"Sunset not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        wind_deg = weather.get('current', {}).get("wind_deg", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        data_points.append({
            "key": "Wind",
            "label": get_ui_label("wind", language, "Wind"),
            "measurement": weather.get('current', {}).get("wind_speed"),
            "unit": UNITS[units]["speed"],
            "icon": self.get_plugin_dir('icons/wind.png'),
            "arrow": wind_arrow
        })

        data_points.append({
            "key": "Humidity",
            "label": get_ui_label("humidity", language, "Humidity"),
            "measurement": weather.get('current', {}).get("humidity"),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        data_points.append({
            "key": "Pressure",
            "label": get_ui_label("pressure", language, "Pressure"),
            "measurement": weather.get('current', {}).get("pressure"),
            "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        data_points.append({
            "key": "UV Index",
            "label": get_ui_label("uv_index", language, "UV Index"),
            "measurement": weather.get('current', {}).get("uvi"),
            "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        visibility = weather.get('current', {}).get("visibility")
        if units == "imperial":
            # convert from m to mi
            visibility /= 1609.
            at_max_visibility = visibility >= 6.2
        else:
            # convert from m to km
            visibility /= 1000.
            at_max_visibility = visibility >= 10
        visibility_str = f"{visibility:.1f}"
        if at_max_visibility:
            visibility_str = u"≥" + visibility_str
        data_points.append({
            "key": "Visibility",
            "label": get_ui_label("visibility", language, "Visibility"),
            "measurement": visibility_str,
            "unit": UNITS[units]["distance"],
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        aqi = (air_quality.get('list') or [{}])[0].get("main", {}).get("aqi")
        locale = LOCALE_DATA.get(language)
        aqi_scale = locale["ui"]["aqi_scale"] if locale and "ui" in locale else ["Good", "Fair", "Moderate", "Poor", "Very Poor"]
        data_points.append({
            "key": "Air Quality",
            "label": get_ui_label("air_quality", language, "Air Quality"),
            "measurement": aqi,
            "unit": aqi_scale[int(aqi)-1] if (aqi is not None and str(aqi).isdigit() and 1 <= int(aqi) <= len(aqi_scale)) else "N/A",
            "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def parse_open_meteo_data_points(self, weather_data, aqi_data, units, tz, time_format, language="en"):
        """Parses current data points from Open-Meteo API response."""
        data_points = []
        daily_data = weather_data.get('daily', {})
        current_data = weather_data.get('current', {})
        hourly_data = weather_data.get('hourly', {})

        current_time = datetime.now(tz)

        # Sunrise
        sunrise_times = daily_data.get('sunrise', [])
        if sunrise_times:
            sunrise_dt = datetime.fromisoformat(sunrise_times[0]).astimezone(tz)
            data_points.append({
                "key": "Sunrise",
                "label": get_ui_label("sunrise", language, "Sunrise"),
                "measurement": self.format_time(sunrise_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunrise_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logger.error(f"Sunrise not found in Open-Meteo response, this is expected for polar areas in midnight sun and polar night periods.")

        # Sunset
        sunset_times = daily_data.get('sunset', [])
        if sunset_times:
            sunset_dt = datetime.fromisoformat(sunset_times[0]).astimezone(tz)
            data_points.append({
                "key": "Sunset",
                "label": get_ui_label("sunset", language, "Sunset"),
                "measurement": self.format_time(sunset_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunset_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logger.error(f"Sunset not found in Open-Meteo response, this is expected for polar areas in midnight sun and polar night periods.")

        # Wind
        wind_speed = current_data.get("windspeed", 0)
        wind_deg = current_data.get("winddirection", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        wind_unit = UNITS[units]["speed"]
        data_points.append({
            "key": "Wind", "label": get_ui_label("wind", language, "Wind"),
            "measurement": wind_speed, "unit": wind_unit,
            "icon": self.get_plugin_dir('icons/wind.png'), "arrow": wind_arrow
        })

        # Humidity
        current_humidity = "N/A"
        humidity_hourly_times = hourly_data.get('time', [])
        humidity_values = hourly_data.get('relative_humidity_2m', [])
        for i, time_str in enumerate(humidity_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_humidity = int(humidity_values[i])
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for humidity.")
                continue
        data_points.append({
            "key": "Humidity", "label": get_ui_label("humidity", language, "Humidity"),
            "measurement": current_humidity, "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        # Pressure
        current_pressure = "N/A"
        pressure_hourly_times = hourly_data.get('time', [])
        pressure_values = hourly_data.get('surface_pressure', [])
        for i, time_str in enumerate(pressure_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_pressure = int(pressure_values[i])
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for pressure.")
                continue
        data_points.append({
            "key": "Pressure", "label": get_ui_label("pressure", language, "Pressure"),
            "measurement": current_pressure, "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        # UV Index
        uv_index_hourly_times = aqi_data.get('hourly', {}).get('time', [])
        uv_index_values = aqi_data.get('hourly', {}).get('uv_index', [])
        current_uv_index = "N/A"
        for i, time_str in enumerate(uv_index_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_uv_index = uv_index_values[i]
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for UV Index.")
                continue
        data_points.append({
            "key": "UV Index", "label": get_ui_label("uv_index", language, "UV Index"),
            "measurement": current_uv_index, "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        # Visibility
        current_visibility = None
        at_max_visibility = False
        visibility_hourly_times = hourly_data.get('time', [])
        visibility_values = hourly_data.get('visibility', [])
        if units == "imperial":
            visibility_conversion = 1/5280.     # ft to mi
            visibility_max = 6.2                # mi
        else:
            visibility_conversion = 0.001       # m to km
            visibility_max = 10.                # km
        for i, time_str in enumerate(visibility_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_visibility = visibility_values[i]*visibility_conversion
                    at_max_visibility = current_visibility >= visibility_max
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for visibility.")
                continue
        if current_visibility is None:
            visibility_str = "N/A"
        else:
            visibility_str = f"{current_visibility:.1f}"
            if at_max_visibility:
                visibility_str = u"≥" + visibility_str
        data_points.append({
            "key": "Visibility",
            "label": get_ui_label("visibility", language, "Visibility"),
            "measurement": visibility_str, 
            "unit": UNITS[units]["distance"],
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        # Air Quality
        aqi_hourly_times = aqi_data.get('hourly', {}).get('time', [])
        aqi_values = aqi_data.get('hourly', {}).get('european_aqi', [])
        current_aqi = "N/A"
        for i, time_str in enumerate(aqi_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_aqi = round(aqi_values[i], 1)
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for AQI.")
                continue
        scale = ""
        if current_aqi and current_aqi != "N/A":
            locale = LOCALE_DATA.get(language)
            aqi_scale_om = locale["ui"]["aqi_scale_om"] if locale and "ui" in locale else ["Good","Fair","Moderate","Poor","Very Poor","Ext Poor"]
            scale = aqi_scale_om[min(int(current_aqi)//20, 5)]
        data_points.append({
            "key": "Air Quality", "label": get_ui_label("air_quality", language, "Air Quality"),
            "measurement": current_aqi,
            "unit": scale, "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def parse_bright_sky_data_points(self, current, aqi_data, units, tz, sunrise_dt, sunset_dt, time_format, language="en"):
        """Parses current data points for the Bright Sky provider.

        Sunrise/sunset come from `astral` (Bright Sky doesn't provide
        them). UV index and air quality come from Open-Meteo's
        air-quality endpoint (the same one the Open-Meteo provider uses),
        since Bright Sky/DWD doesn't publish either."""
        data_points = []

        if sunrise_dt:
            data_points.append({
                "key": "Sunrise",
                "label": get_ui_label("sunrise", language, "Sunrise"),
                "measurement": self.format_time(sunrise_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunrise_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logger.error("Sunrise could not be calculated (astral), this is expected for polar areas in midnight sun and polar night periods.")

        if sunset_dt:
            data_points.append({
                "key": "Sunset",
                "label": get_ui_label("sunset", language, "Sunset"),
                "measurement": self.format_time(sunset_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunset_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logger.error("Sunset could not be calculated (astral), this is expected for polar areas in midnight sun and polar night periods.")

        wind_speed_kmh = current.get("wind_speed_10", current.get("wind_speed"))
        wind_deg = current.get("wind_direction_10", current.get("wind_direction")) or 0
        wind_arrow = self.get_wind_arrow(wind_deg)
        wind_speed = self.convert_wind_kmh(wind_speed_kmh, units)
        data_points.append({
            "key": "Wind",
            "label": get_ui_label("wind", language, "Wind"),
            "measurement": round(wind_speed, 1) if wind_speed is not None else "N/A",
            "unit": UNITS[units]["speed"],
            "icon": self.get_plugin_dir('icons/wind.png'),
            "arrow": wind_arrow
        })

        data_points.append({
            "key": "Humidity",
            "label": get_ui_label("humidity", language, "Humidity"),
            "measurement": current.get("relative_humidity", "N/A"),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        pressure = current.get("pressure_msl")
        data_points.append({
            "key": "Pressure",
            "label": get_ui_label("pressure", language, "Pressure"),
            "measurement": round(pressure) if pressure is not None else "N/A",
            "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        current_time = datetime.now(tz)

        uv_times = aqi_data.get('hourly', {}).get('time', [])
        uv_values = aqi_data.get('hourly', {}).get('uv_index', [])
        current_uv_index = "N/A"
        for i, time_str in enumerate(uv_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_uv_index = uv_values[i]
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for UV Index.")
                continue
        data_points.append({
            "key": "UV Index", "label": get_ui_label("uv_index", language, "UV Index"),
            "measurement": current_uv_index, "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        visibility_m = current.get("visibility")
        at_max_visibility = False
        if visibility_m is not None:
            if units == "imperial":
                visibility = visibility_m / 1609.
                at_max_visibility = visibility >= 6.2
            else:
                visibility = visibility_m / 1000.
                at_max_visibility = visibility >= 10
            visibility_str = f"{visibility:.1f}"
            if at_max_visibility:
                visibility_str = u"≥" + visibility_str
        else:
            visibility_str = "N/A"
        data_points.append({
            "key": "Visibility",
            "label": get_ui_label("visibility", language, "Visibility"),
            "measurement": visibility_str,
            "unit": UNITS[units]["distance"],
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        aqi_times = aqi_data.get('hourly', {}).get('time', [])
        aqi_values = aqi_data.get('hourly', {}).get('european_aqi', [])
        current_aqi = "N/A"
        for i, time_str in enumerate(aqi_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_aqi = round(aqi_values[i], 1)
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for AQI.")
                continue
        scale = ""
        if current_aqi and current_aqi != "N/A":
            locale = LOCALE_DATA.get(language)
            aqi_scale_om = locale["ui"]["aqi_scale_om"] if locale and "ui" in locale else ["Good", "Fair", "Moderate", "Poor", "Very Poor", "Ext Poor"]
            scale = aqi_scale_om[min(int(current_aqi) // 20, 5)]
        data_points.append({
            "key": "Air Quality", "label": get_ui_label("air_quality", language, "Air Quality"),
            "measurement": current_aqi,
            "unit": scale, "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def get_wind_arrow(self, wind_deg: float) -> str:
        DIRECTIONS = [
            ("↓", 22.5),    # North (N)
            ("↙", 67.5),    # North-East (NE)
            ("←", 112.5),   # East (E)
            ("↖", 157.5),   # South-East (SE)
            ("↑", 202.5),   # South (S)
            ("↗", 247.5),   # South-West (SW)
            ("→", 292.5),   # West (W)
            ("↘", 337.5),   # North-West (NW)
            ("↓", 360.0)    # Wrap back to North
        ]
        wind_deg = wind_deg % 360
        for arrow, upper_bound in DIRECTIONS:
            if wind_deg < upper_bound:
                return arrow

        return "↑"

    def convert_temp_c(self, temp_c, units):
        """Converts a Celsius reading (Bright Sky's only unit) to the
        display unit, mirroring the conversions the other providers get
        for free from their own unit-aware API params."""
        if temp_c is None:
            return 0.0
        if units == "imperial":
            return temp_c * 9 / 5 + 32
        if units == "standard":
            return temp_c + 273.15
        return temp_c

    def convert_wind_kmh(self, speed_kmh, units):
        """Converts a km/h reading (Bright Sky's only unit) to the display
        unit - m/s for metric/standard, mph for imperial."""
        if speed_kmh is None:
            return None
        if units == "imperial":
            return speed_kmh / 1.60934
        return speed_kmh / 3.6

    def get_sun_times(self, lat, long, target_date, tz):
        """Computes sunrise/sunset locally via astral, since Bright Sky
        doesn't provide them. Returns (None, None) for polar day/night,
        where no sunrise/sunset occurs."""
        try:
            observer = Observer(latitude=lat, longitude=long)
            s = astral_sun(observer, date=target_date, tzinfo=tz)
            return s['sunrise'], s['sunset']
        except Exception as e:
            logger.warning(f"Could not compute sunrise/sunset for {lat},{long} on {target_date}: {e}")
            return None, None

    def get_weather_data(self, api_key, units, lat, long):
        url = WEATHER_URL.format(lat=lat, long=long, units=units, api_key=api_key)
        response = requests.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve weather data: {response.content}")
            raise RuntimeError("Failed to retrieve weather data.")

        return response.json()

    def get_air_quality(self, api_key, lat, long):
        url = AIR_QUALITY_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to get air quality data: {response.content}")
            raise RuntimeError("Failed to retrieve air quality data.")

        return response.json()

    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to get location: {response.content}")
            raise RuntimeError("Failed to retrieve location.")

        location_data = response.json()[0]
        location_str = f"{location_data.get('name')}, {location_data.get('state', location_data.get('country'))}"

        return location_str

    def get_open_meteo_data(self, lat, long, units, forecast_days, model="best_match"):
        unit_params = OPEN_METEO_UNIT_PARAMS[units]
        url = OPEN_METEO_FORECAST_URL.format(lat=lat, long=long, forecast_days=forecast_days, model=model) + f"&{unit_params}"
        response = requests.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo weather data: {response.content}")
            raise RuntimeError("Failed to retrieve Open-Meteo weather data.")

        return response.json()

    def get_open_meteo_air_quality(self, lat, long):
        url = OPEN_METEO_AIR_QUALITY_URL.format(lat=lat, long=long)
        response = requests.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo air quality data: {response.content}")
            raise RuntimeError("Failed to retrieve Open-Meteo air quality data.")

        return response.json()

    def get_bright_sky_current(self, lat, long):
        url = BRIGHT_SKY_CURRENT_URL.format(lat=lat, long=long)
        response = requests.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Bright Sky current weather: {response.content}")
            raise RuntimeError("Failed to retrieve Bright Sky current weather.")

        return response.json()

    def get_bright_sky_forecast(self, lat, long, days):
        today = date.today()
        last = today + timedelta(days=days)
        url = BRIGHT_SKY_WEATHER_URL.format(lat=lat, long=long, date=today.isoformat(), last_date=last.isoformat())
        response = requests.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Bright Sky forecast: {response.content}")
            raise RuntimeError("Failed to retrieve Bright Sky forecast.")

        return response.json()

    def format_time(self, dt, time_format, hour_only=False, include_am_pm=True):
        """Format datetime based on 12h or 24h preference"""
        if time_format == "24h":
            return dt.strftime("%H:00" if hour_only else "%H:%M")

        if include_am_pm:
            fmt = "%I %p" if hour_only else "%I:%M %p"
        else:
            fmt = "%I" if hour_only else "%I:%M"

        return dt.strftime(fmt).lstrip("0")

    def parse_timezone(self, weatherdata):
        """Parse timezone from weather data"""
        if 'timezone' in weatherdata:
            logger.info(f"Using timezone from weather data: {weatherdata['timezone']}")
            return get_timezone(weatherdata['timezone'])
        else:
            logger.error("Failed to retrieve Timezone from weather data")
            raise RuntimeError("Timezone not found in weather data.")
