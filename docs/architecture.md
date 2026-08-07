# InkyPi Architecture

A high-level map of how requests flow through the app and how the refresh loop drives the e-ink display.

## Overview

InkyPi is a Flask web app + a background refresh task that runs in the same process. The web UI lets the user configure plugins and assemble them into playlists; the refresh task picks the next plugin from the playlist on a schedule, runs it, and pushes the resulting image to the display.

## Component diagram

```mermaid
flowchart TD
    Browser([Browser]) -->|HTTP| Flask
    Flask[Flask app<br/>src/inkypi.py]
    Flask -->|registers| BP[Blueprints<br/>src/blueprints/]

    BP --> MainBP[main.py — dashboard]
    BP --> PluginBP[plugin.py — plugin config]
    BP --> PlaylistBP[playlist.py — playlist mgmt]
    BP --> SettingsBP[settings/ — device settings, API keys]
    BP --> HistoryBP[history.py — history view]
    BP --> EventsBP[events.py — SSE dashboard updates]

    PluginBP -->|loads| Registry[Plugin Registry<br/>src/plugins/plugin_registry.py]
    Registry -->|imports| Plugins[Plugin classes<br/>src/plugins/&lt;name&gt;/]
    Plugins -.->|extend| BasePlugin[BasePlugin<br/>src/plugins/base_plugin/]

    Flask -->|reads| Config[Config<br/>src/config.py]
    Config -->|loads| DeviceJSON[(device.json /<br/>device_dev.json)]

    Config -->|owns| PlaylistMgr[PlaylistManager<br/>src/model.py]
    PlaylistMgr -->|stores| PlaylistData[Playlist + PluginInstance<br/>src/model.py]

    Flask -->|starts| RefreshTask[RefreshTask<br/>src/refresh_task/task.py]
    RefreshTask -->|polls| PlaylistMgr
    RefreshTask -->|spawns| Worker[Subprocess worker<br/>runs plugin in isolation]
    Worker -->|calls| PluginGen[plugin.generate_image<br/>returns PIL.Image]
    PluginGen -->|returns to| RefreshTask
    RefreshTask -->|pushes to| DisplayMgr[DisplayManager<br/>src/display/display_manager.py]
    DisplayMgr -->|drives| Display([E-ink display<br/>or mock])

    RefreshTask -->|tracks health| Health[plugin_health<br/>circuit breaker state]
```

## Request flow (web UI)

1. Browser sends an HTTP request to a Flask route registered by one of the blueprints.
2. The blueprint reads/writes `Config` and `PlaylistManager` (both backed by `device.json`).
3. The blueprint may call into a plugin's `generate_settings_template()` / `build_settings_schema()` to render its config form, but it does **not** run `generate_image()` synchronously — that happens in the refresh task.
4. The response is rendered with Jinja2 templates from `src/templates/`.

## Refresh flow (background)

1. `RefreshTask` (`src/refresh_task/task.py`) runs in a background thread started during app init.
2. On each tick, it asks `PlaylistManager` for the next plugin instance (based on schedule + `paused` state from the circuit breaker in `src/refresh_task/health.py`).
3. It spawns a **subprocess** (`src/refresh_task/worker.py`) to run the plugin in isolation. Subprocess isolation means a crashing plugin can't take down the app. Only the rendered image crosses back to the parent (via a tempfile path) — any other mutation the plugin makes on objects passed into `execute()` happens on the subprocess's own copy and is lost.
4. The plugin's `generate_image()` returns a `PIL.Image`.
5. The parent updates `plugin_health` (success/failure counters, circuit-breaker state, webhook notification on failure) and pushes the image to `DisplayManager`.
6. `DisplayManager` chooses the right driver (Inky, Waveshare, mock) and writes to the panel.

## Config layer

- `device.json` (or `device_dev.json` in dev mode) is the single source of truth for device settings, playlists, and saved plugin instances. There is no database.
- `Config` (`src/config.py`) loads it once at startup and provides locked accessors: `.get_resolution()`, `.get_config(key, default)`, `.load_env_key(name)`.
- `PlaylistManager` is a child of `Config` that manages `Playlist` and `PluginInstance` objects (`src/model.py`).

## Plugin lifecycle

- At startup, `plugin_registry.load_plugins()` walks `src/plugins/`, reads each `plugin-info.json`, imports the class, and instantiates it. In dev mode, plugin code hot-reloads on each `get_plugin_instance()` call.
- Each `PluginInstance` is a saved configuration of a plugin (e.g., "Weather — Home" and "Weather — Work" are two instances of the weather plugin).
- The refresh task picks one `PluginInstance` per tick and runs it via a subprocess worker.

## Where to look next

- New to the codebase? Start with `src/inkypi.py` to see the wiring.
- Building a plugin? See [building_plugins.md](building_plugins.md).
- Understanding the refresh loop? Read `src/refresh_task/task.py`, `scheduler.py`, and `health.py`.
- Display drivers? `src/display/` — `DisplayManager` selects the driver based on `device.json`.
