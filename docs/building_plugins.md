# Building InkyPi Plugins

> **New to the codebase?** Start with the [architecture overview](architecture.md) to see how plugins fit into the request and refresh flows.
>
> **Hitting a runtime error?** See [Troubleshooting: Plugin Development](troubleshooting.md#plugin-development-troubleshooting) for common failures: API key errors, fetch timeouts, dimension mismatches, memory pressure, screenshot issues, and Jinja2 gotchas.

## Contract

A plugin is a directory under `src/plugins/<id>/` containing:

```
src/plugins/{plugin_id}/
    {plugin_id}.py          # Main plugin class, extends BasePlugin
    plugin-info.json        # Plugin manifest
    icon.png                # 256x256 icon shown in the plugin picker
    render/                 # Optional: HTML/CSS templates, if using render_image()
```

The class extends `BasePlugin` (`src/plugins/base_plugin/base_plugin.py`) and implements:

```python
def generate_image(self, settings: dict, device_config) -> PIL.Image:
```

- `settings` — plugin configuration values, collected from the settings-schema form.
- `device_config` — the `Config` instance: `.get_resolution()`, `.get_config(key, default)`, `.load_env_key(name)`.
- Return a single `PIL.Image`. Its size must match `self.get_oriented_dimensions(device_config)` — a 90° transposition is auto-corrected, any other mismatch raises `OutputDimensionMismatch` and the previous image is kept.
- On unrecoverable failure, raise `RuntimeError` with a clear message — it's surfaced to the user in the web UI.

`plugin-info.json`:

```json
{
    "display_name": "Clock",
    "id": "clock",
    "class": "Clock",
    "api_version": "1.0",
    "version": "1.0.0"
}
```

`id` must match the directory name (lowercase, no spaces). Plugins are loaded on startup by `plugin_registry.load_plugins()`, which walks `src/plugins/`, reads each `plugin-info.json`, and imports the class.

## Settings — schema-driven, not hand-written HTML

Every plugin must implement `build_settings_schema()` — a plugin with no schema fails `tests/unit/test_legacy_settings_cleanup.py`. Do not create a `settings.html` file; the settings form is generated from the schema.

The DSL lives in `plugins.base_plugin.settings_schema`:

```python
from plugins.base_plugin.settings_schema import field, option, row, schema, section

class Clock(BasePlugin):
    def build_settings_schema(self) -> dict[str, object]:
        return schema(
            section(
                "Colors",
                row(
                    field("primaryColor", "color", label="Face accent color", default="#000000"),
                    field("secondaryColor", "color", label="Face background color", default="#ffffff"),
                ),
            ),
        )
```

- `schema(*sections)`, `section(title, *items)`, `row(*fields)`, `field(name, type, label=..., options=..., visible_if={...}, default=...)`, `option(value, label)`, `option_group(label, *options)`, `callout(text, tone=...)`, `widget(widget_type, template=..., config={...})`.
- Field types: `text`/`url`/`number`/etc, `checkbox` (needs `submit_unchecked`/`checked_value`/`unchecked_value` to submit a value when unchecked), `radio_segment`, `select` (supports `options_by_value`), `color`, `textarea`, `hidden`.
- `visible_if={"field": name, "equals"|"operator": ...}` is single-field only — no compound AND/OR. A field needing multiple conditions may just need to always show.
- A `foo[]` field name auto-collects into a Python list via `parse_form()`.
- Set `template_params['style_settings'] = True` in `generate_settings_template()` to add the shared text-color/background/margin/frame style controls to the page.

### Widgets — for anything a field type can't do

Maps, repeaters, live external lookups, or any custom interactive control need a widget instead of a plain field:

1. A template in `src/templates/widgets/<name>.html`, included via `{% include item.template %}` (has `plugin_settings` in scope).
2. A JS init function registered in `plugin_schema.js`'s `widgetInitializers` map: `init(widgetElement, config)`.

Reusable widgets: `weather-map` (lat/long picker), `calendar-repeater` (parallel bracket-array fields like `calendarURLs[]`/`calendarColors[]` — use this when fields parallelize cleanly; otherwise use one JSON blob per row). Modals inside a widget manage their own show/hide — there's no global `openModal()`/`closeModal()`.

### Conditional API key requirement

If a plugin's API key requirement depends on another setting, implement:

```python
def api_key_required_for_settings(self, settings) -> bool:
```

`blueprints/plugin.py::plugin_page()` calls it once real settings are known. This only affects page-load evaluation, not live reactivity to an unsaved dropdown change.

## Icon

Drop a 256x256 `icon.png` into the plugin directory. Custom icons without a `PLUGIN_ICON_MAP` entry (`src/templates/macros/icons.html`) render as a raster `<img>`, not the built-ins' `currentColor` inline SVG — flat black-line art has nothing to adapt with in dark mode, though `_plugins.css` gives plugin icon images a light backing plate across render contexts. Add a `PLUGIN_ICON_MAP` entry with an equivalent Phosphor icon if you want the built-in look instead.

## Generating images by rendering HTML and CSS

For dashboards or complex layouts, render HTML/CSS instead of drawing with Pillow directly. Call `BasePlugin.render_image()`:

```python
def render_image(self, dimensions, html_file, css_file=None, template_params=None):
```

- Place `.html`/`.css` files in the plugin's `render/` subdirectory.
- The HTML file should extend the base template:
  ```
  {% extends "plugin.html" %}
  {% block content %}
  <!-- content -->
  {% endblock %}
  ```
- `plugin.html` includes all font faces in `static/fonts/` and handles style settings (text color, background, margin, frame) when `template_params['plugin_settings']` is set from `settings`.
- If your template references `{{static_dir}}` (e.g. loading a JS asset), you must set `template_params["static_dir"] = self.to_file_url(resolve_path("static"))` in `generate_image()` — the Jinja env used for rendering has no Flask `static_dir`/`url_for` global. Missing this silently produces a blank image with no Python-side error.
- Under the hood, `render_image()` renders the template with Jinja2, then calls `take_screenshot_html()` (`utils/image_utils.py`), which drives headless Chromium to capture a screenshot.

For reference implementations, see `weather`, `year_progress`, or one of the custom plugins (`abfahrtzeiten`, `calendar_auth`, `regenalarm`) documented in the root `CLAUDE.md`.

## No plugin-level backend routes

Plugins don't get their own Flask blueprint. If the settings page needs live external data (autocomplete, lookups), call the external API directly from client-side JS — check it sends `Access-Control-Allow-Origin: *` first. If a plugin needs a secret beyond the standard API-key flow, use the custom-secrets mechanism at `/settings/api-keys` (see [api_keys.md](api_keys.md)) rather than adding a route.

## Testing your plugin

```bash
python scripts/plugin_validator.py <plugin_id>   # validate structure/manifest
```

Then in the dev server (`INKYPI_ENV=dev .venv/bin/python src/inkypi.py --dev`):

- Confirm the plugin loads without error and appears in the plugin picker with its icon.
- Generate an image via the Preview button (`/preview_now` — doesn't touch the real display or saved settings) for a few different display resolutions/orientations if relevant.
- Add it to a playlist, save, reload the settings page, and confirm the form is prepopulated from the saved instance.
- Trigger a real update (`/update_now` or waiting for the refresh tick) and confirm the mock display updates (`runtime/mock_display_output/latest.png`).

## Publishing a third-party plugin

Create a new repository (recommended name: `InkyPi-{plugin_name}`, for GitHub search discoverability) containing:

- A folder named after your `plugin_id`, with the structure above — this gets copied into `src/plugins/` on install.
- A README with: a one-sentence description, at least one screenshot, any external API dependencies (docs link, key setup, rate limits/cost), and current maintenance status.

See [InkyPi-Plugin-Template](https://github.com/fatihak/InkyPi-Plugin-Template) for a sample third-party plugin template.
