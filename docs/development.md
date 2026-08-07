# InkyPi Development Quick Start

The `--dev` flag enables complete development without Raspberry Pi hardware, a physical e-ink display, root/GPIO access, or systemd. Works on macOS, Linux, and Windows.

## Setup

<table>
<tr>
<td>

Traditional setup

```bash
git clone https://github.com/florentineprinzessinzusachsen/InkyPi-modernize.git
cd InkyPi-modernize

./scripts/dev/dev.sh   # quick start

# or manually:
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r install/requirements-dev.txt
bash install/update_vendors.sh
python src/inkypi.py --dev
```

</td>
<td>

devbox (works on macOS / any Linux distro / WSL2)

```bash
command -v devbox >/dev/null || curl -fsSL https://get.jetify.com/devbox | bash
command -v direnv >/dev/null || nix profile install "nixpkgs#direnv"

git clone https://github.com/florentineprinzessinzusachsen/InkyPi-modernize.git
cd InkyPi-modernize # direnv reads .envrc -> runs devbox shell -> installs deps & activates venv

devbox run dev # or: devbox shell, then python src/inkypi.py --dev
```

</td>
</tr>
</table>

Open http://localhost:8080.

### Pre-commit hooks

```bash
pre-commit install
```

Runs on every `git commit`: whitespace/YAML/merge-conflict checks, **ruff** (lint + format), **mypy** (type checks — see [typing.md](typing.md)), **gitleaks** (secret scanning), and conventional-commit message validation. Frontend changes under `src/static/**` or `src/templates/**` also trigger `scripts/checks/test.sh browser-smoke` locally before the commit is allowed through. See [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) for the full list. `git commit --no-verify` skips hooks locally, but CI enforces the same checks.

## Essential commands

```bash
python src/inkypi.py --dev            # full program, mock display
python src/inkypi.py --dev --web-only # web UI only, no background refresh thread
python src/inkypi.py --dev --fast-dev # fast cycle, skip startup image
./scripts/dev/web_only.sh                 # scripted web-only startup
./scripts/dev/dev_watch.sh                # auto-rebuild CSS/JS on save (needs `watchdog`)
python3 scripts/build/build_css.py          # one-shot CSS build (no watcher)
```

`scripts/dev/dev_watch.sh` watches `src/static/styles/` → `scripts/build/build_css.py`, `src/static/scripts/` → `scripts/build/build_assets.py`, and `src/templates/` (log-only; Flask auto-reloads templates).

The dev server (waitress) does **not** hot-reload on `--dev` — restart after any code change. Exception: plugin modules under `src/plugins/` hot-reload on each access when `INKYPI_ENV=dev` / `--dev` is set. For Python code reload via Flask's debug reloader instead:

```bash
FLASK_APP=src.inkypi:create_app INKYPI_ENV=dev flask --debug run -p 8080
```

## Dev tips

- Rendered output: `runtime/mock_display_output/latest.png`.
- Simulated e-ink frame: `/tmp/inkypi-mock-frame.png` (override with `INKYPI_MOCK_FRAME_PATH`), or view live at `http://localhost:8080/dev/mock-frame`.
- Plugin development: copy an existing plugin as a template (see [building_plugins.md](building_plugins.md)).
- Config: edit `src/config/device_dev.json` for display settings (gitignored, safe to churn).
- Plugin validator: `python scripts/checks/plugin_validator.py [plugin_id]`.
- JSON schemas (IDE/CI): `src/config/schemas/device_config.schema.json`, `src/config/schemas/plugin-info.schema.json`.
- BasePlugin's Jinja environment is initialized even if a plugin has no `render/` directory — base templates under `plugins/base_plugin/render/` are always available. A plugin without `build_settings_schema()` fails CI (see [building_plugins.md](building_plugins.md)).

## Docker

```bash
docker compose up --build
```

Web UI at http://localhost:8080. Source changes in `src/` reflect immediately via volume mount. Display is mocked automatically. `Ctrl+C` or `docker compose down` to stop.

## System requirements

Skip this section if using devbox.

- Linux system packages: see [`install/debian-requirements.txt`](../install/debian-requirements.txt), install via `apt` or your distro's equivalent.
- A Chromium-family browser for HTML-rendered plugins (headless screenshot rendering):

  | Platform | Recommended package | Notes |
  | --- | --- | --- |
  | Raspbian / Debian | `chromium-headless-shell` | `chromium`/`google-chrome` also work if on `PATH` |
  | Other Linux | `chromium` | devbox installs this on Linux |
  | macOS | Google Chrome | chromium on macOS/aarch64 isn't stable; devbox expects Chrome at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
  | Windows | Chromium or Google Chrome | devbox installs chromium on WSL2; native Windows needs one on `PATH` |

  InkyPi searches the project `PATH` (devbox) then the system `PATH` for a Chrome-like browser.

## CodeQL suppression policy

CodeQL runs on every push and PR. Most alerts are real and should be fixed in code. A small number are taint-tracker false positives where CodeQL can't model a validation/sanitization helper — suppress only those, at the flagged line, with a justification:

- Python: `# lgtm[<rule-id>] — <why this is a false positive>`
- JavaScript: `// lgtm[<rule-id>] — <why this is a false positive>`

Required:
1. Use the exact rule ID from the alert (e.g. `py/clear-text-logging-sensitive-data`).
2. Explain *why* the alert doesn't apply at this call site — reference the data flow or sanitization that makes it wrong.
3. Place the comment on the flagged line itself, not above/below.

Forbidden: generic `# lgtm — false positive` / `# noqa` comments, or suppressing a rule across a whole file/module.

If you're not sure an alert is a false positive, don't suppress it — a real alert silenced by mistake is worse than an unsuppressed false positive.

## CI

The `ci-gate` job in `.github/workflows/ci.yml` depends on all required jobs and is the single check to mark as required in GitHub branch protection (Settings → Branches → require status check `CI gate (all checks pass)`) — adding a new required CI job only means editing `ci.yml`, not the branch-protection UI.
