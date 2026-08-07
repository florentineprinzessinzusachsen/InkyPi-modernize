# Testing Guide

How to run, extend, and understand the test setup. For performance/profiling tooling see [performance.md](performance.md); for the mypy gate see [typing.md](typing.md).

## Setup & running

```bash
bash scripts/venv.sh
. .venv/bin/activate
python -m pip install -r install/requirements.txt -r install/requirements-dev.txt

scripts/test.sh                                     # recommended fast local path
scripts/test.sh tests/unit/test_refresh_task_stress.py
scripts/preflash_validate.sh                         # hardware-free pre-flash gate
PYTHONPATH=$(pwd)/src pytest -q
PYTHONPATH=$(pwd)/src pytest --cov=src --cov-report=term-missing
```

- `scripts/test.sh` with no args shards the main local suite across 4 lanes (`core`, `plugins-a`, `plugins-b`, `plugins-c`), `PYTEST_LANE_WORKERS=2` per lane by default. Runs serial for a single explicit test file, `pytest -n 4 --dist=loadfile -q` for broader explicit targets. Coverage runs stay serial.
- Tests auto-mock Chromium screenshot capture via a fixture — no browser required for the default suite.
- Managed API-key env vars are cleared per test; the temp `PROJECT_DIR` gets an empty `.env`, keeping missing-key flows deterministic.
- Browser smoke coverage is separate and needs Playwright Chromium:
  ```bash
  playwright install chromium
  PYTHONPATH=$(pwd)/src REQUIRE_BROWSER_SMOKE=1 pytest tests/integration/test_browser_smoke.py -q
  ```
- A11y/browser suites can be run explicitly:
  ```bash
  PYTHONPATH=$(pwd)/src SKIP_A11Y=0 pytest tests/integration/test_more_a11y.py -q
  PYTHONPATH=$(pwd)/src SKIP_UI=0 pytest tests/integration/test_weather_autofill.py -q
  ```
- If a removed UI element still appears after a code change, refresh/restart — stale server/browser state can mask template updates.

### Pre-flash validation

`scripts/preflash_validate.sh` checks app boot, config resolution, mock rendering, and targeted pytest coverage without the device connected. It does **not** prove EEPROM detection, SPI/GPIO access, or real panel refresh — those are post-flash hardware checks.

Set `INKYPI_VALIDATE_INSTALL=1` to include the import-only install smoke phase (clean temp env, both macOS and Linux; Linux also validates Inky/systemd imports). Additional opt-in lanes:

```
INKYPI_VALIDATE_PI_RUNTIME=1        INKYPI_VALIDATE_STRESS=1
INKYPI_VALIDATE_HEAVY_PLUGINS=1     INKYPI_VALIDATE_BENCH_THRESHOLDS=1
INKYPI_VALIDATE_COLD_BOOT=1         INKYPI_VALIDATE_CACHE=1
INKYPI_VALIDATE_ISOLATION=1         INKYPI_VALIDATE_BROWSER_RENDER=1
INKYPI_VALIDATE_INSTALL_IDEMPOTENCY=1  INKYPI_VALIDATE_FAULTS=1
INKYPI_VALIDATE_UPGRADE_COMPAT=1    INKYPI_VALIDATE_COVERAGE=1
INKYPI_VALIDATE_SECURITY=1          INKYPI_VALIDATE_FLAKE=1
INKYPI_VALIDATE_FS_PERMS=1          INKYPI_VALIDATE_SOAK=1
INKYPI_VALIDATE_RECOVERY=1          INKYPI_VALIDATE_API_CONTRACT=1
INKYPI_VALIDATE_MUTATION=1
```

These cover fault injection, property/invariant regression, upgrade compatibility for legacy config/benchmark DBs, per-file coverage thresholds, security audit + SBOM output, flaky-test reruns, readonly-filesystem handling, startup recovery, API contracts, nightly soak, and the narrow deterministic mutation harness.

## Key fixtures (`tests/conftest.py`)

- `mock_screenshot` (autouse) — patches `utils.image_utils.take_screenshot`/`take_screenshot_html` to return an in-memory `PIL.Image`, so tests run fast and hardware-free.
- `device_config_dev` — temp `device.json`, patches `Config` paths to temp locations; isolates file IO and the plugin image cache.
- `flask_app`, `client` — minimal Flask app mirroring production blueprints/config, with a `test_client`.

## Test coverage focus

- Unit: `model.py` (scheduling, playlist priorities, refresh logic), `utils/image_utils.py` (orientation, resize, hashing), `plugins/plugin_registry.py` (load/lookup).
- Integration: settings routes, plugin routes, refresh task manual update flow.
- Plugins: Weather (OpenWeatherMap & Open‑Meteo mocked), AI Text (OpenAI/Google chat mocked), AI Image (OpenAI/Google image generation mocked).

## Adding new tests

1. Place under `tests/` (unit, integration, or plugin subfolders).
2. Reuse fixtures from `conftest.py`.
3. Mock external APIs and I/O. Keep tests deterministic and fast.

## Mutation testing

Mutation testing introduces small code changes ("mutants") and reruns the test suite. A mutant that gets caught by a failing test is **killed**; one that survives signals a coverage gap that line-coverage percentages alone wouldn't show. This matters here because InkyPi ships to devices that can't be easily reflashed in the field.

In scope (`pyproject.toml` → `[tool.mutmut]` → `source_paths`): `src/app_setup/`, `src/blueprints/`, `src/utils/`, `src/refresh_task/`.

```bash
# Full run against configured paths
INKYPI_ENV=dev INKYPI_NO_REFRESH=1 PYTHONPATH=src mutmut run

# Narrower shard
INKYPI_ENV=dev INKYPI_NO_REFRESH=1 PYTHONPATH=src mutmut run --paths-to-mutate src/utils/

mutmut results                # summary
mutmut show <id>              # inspect a specific surviving mutant
mutmut apply <id>             # apply to working tree for investigation
mutmut unapply
```

To expand scope: add the path to `source_paths` in `pyproject.toml`, add it to `EXPECTED_FILES` in `tests/test_mutmut_config.py`, open a PR.

The `mutation-nightly` job in `.github/workflows/ci.yml` runs on a schedule (and via `workflow_dispatch`), never on push/PR — it's sharded by package (`app-setup`, `blueprints`, `utils`, `refresh-task`), each uploading a `mutmut-cache-<shard>` artifact. It's advisory: a survived mutant doesn't block CI. Review results, add targeted tests, shrink the surviving count over time.

For PR-time confidence there's also a fast, deterministic harness — a small set of known high-value mutants applied to a temp copy of the repo with targeted tests:

```bash
INKYPI_ENV=dev INKYPI_NO_REFRESH=1 PYTHONPATH=src python scripts/mutation_check.py
```

| Status | Meaning |
|--------|---------|
| Killed | Test suite caught the mutant — good |
| Survived | No test detected the change — consider a targeted test |
| Skipped | mutmut couldn't parse or apply the mutation |
| Suspicious | Test timed out; worth investigating |

## Pi thrash protection regression gate

`tests/integration/test_install_crash_loop.py` guards the "install crash mid-pip → restart loop" failure mode that can require a hard power cycle on a real Pi. It boots a systemd-capable Debian container (`--privileged`, 512 MB cap), installs `inkypi.service` with a stub `ExecStart` that mimics a crash, runs `install.sh`'s service-stop/disable contract, and creates the `/var/lib/inkypi/.install-in-progress` lockfile, then repeatedly tries to start the service while the lockfile is present.

Core invariant: **`ExecStart` must never run while the lockfile exists.** A marker file written by the stub is the primary assertion — if it appears, the defense is broken. A positive-control step removes the lockfile and confirms `ExecStart` does start once install is "complete."

```bash
# Requires local Docker; skips cleanly without it, or set
# REQUIRE_INSTALL_CRASH_LOOP_TEST=1 to force-run and fail hard if Docker is missing
PYTHONPATH=$(pwd)/src pytest tests/integration/test_install_crash_loop.py -v -s
```

Asserts three invariants: (1) after stopping, `systemctl is-enabled inkypi.service` is `disabled` or `masked`; (2) while the lockfile is present, `ExecMainPID=0` and the stub marker is never touched; (3) restart count stays bounded (`NRestarts <= 10`) via systemd's `StartLimitBurst`. Runs automatically in CI as the `install-crash-loop-gate` job and is part of the `ci-gate` required-success loop. If you're changing `install.sh`'s stop-service function or `install/inkypi.service`'s `ExecStartPre` guard, expect this test to need updating.

## CI

GitHub Actions runs the pytest matrix, pre-flash validation matrix, coverage gate, security/SBOM checks, flake detection, and the browser-smoke job. Nightly scheduled jobs run the soak and mutation lanes. Workflow: `.github/workflows/ci.yml`.

### CI memory budgets

`install-smoke-memcap` (`scripts/test_install_memcap.sh`) asserts the running web service stays within the Pi Zero 2 W envelope: 512 MB total RAM, systemd unit caps InkyPi at `MemoryMax=350M`. It runs inside a 512 MB-capped container and reads `VmRSS` from `/proc/1/status`.

| Metric | Target | Hard fail |
| --- | --- | --- |
| Post-install idle RSS (30s after `/healthz`) | <150 MB | >200 MB |
| Peak RSS during plugin render exercise | <250 MB | >300 MB |

The idle sample follows a 30s sleep; the peak sample hits `/`, `/playlist`, `/api/plugins`, `/api/health/plugins`, and a `POST /update_now` with `plugin_id=clock` to exercise the render codepath (`--web-only` short-circuits the actual refresh, but the request still drives the hottest allocation path). If a new plugin or import pushes baseline RSS above target, bump its lazy-import boundary rather than raising the budget. Both failure modes print a `BUDGET CHECK:` line in the CI log.

### OS drift nightly

`.github/workflows/os-drift-nightly.yml` (daily cron) re-runs the install path against the **latest unpinned** `debian:trixie`/`bookworm`/`bullseye` images — the unpinned complement to the pinned PR-gating install matrix. It catches upstream Debian/Pi OS package churn that a pinned matrix can't. Each leg asserts every package in `install/debian-requirements.txt` resolves via `apt-cache show`, every pin in `install/requirements.txt` resolves via `pip install --dry-run`, and `scripts/sim_install.sh` runs `install/install.sh` end-to-end inside a 512 MB arm64 sim of the Pi Zero 2 W. It has no `pull_request:` trigger — a broken nightly must never block merges. On failure it opens a GitHub issue labelled `os-drift`/`bug`. Manual runs: `workflow_dispatch` with an optional codename filter.
