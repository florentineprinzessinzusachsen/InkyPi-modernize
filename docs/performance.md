# Performance: Profiling & Benchmarking

Two systems, for two different purposes:

- **Runtime benchmarks** — always-on, records real per-refresh timings to SQLite on the device (or dev machine). For understanding how *this* deployment is actually performing.
- **Developer profiling tools** — pytest-benchmark, cProfile, py-spy. For investigating a specific slowdown while you work on the code.

## Runtime benchmark system

Persists per-refresh metrics and stage events in SQLite with minimal overhead; safe to run in production.

### Configuration (`src/config/device.json`)

| Key | Type | Default |
|---|---|---|
| `enable_benchmarks` | bool | `true` |
| `benchmarks_db_path` | str | `<PROJECT_ROOT>/runtime/benchmarks.db` |
| `benchmark_sample_rate` | float 0..1 | `1.0` — probability of recording a given refresh |

### What's recorded

- `refresh_events` (one row per refresh): `refresh_id`, `ts`, `plugin_id`, `instance`, `playlist`, `used_cached`, `request_ms`, `generate_ms`, `preprocess_ms`, `display_ms`, `cpu_percent`, `memory_percent`, `notes`.
- `stage_events` (one row per stage within a refresh): `refresh_id`, `ts`, `stage`, `duration_ms`, `extra_json`.

Instrumentation lives in `src/refresh_task/recorder.py` (creates the `benchmark_id`, persists `refresh_events`, emits stage events for `generate_image` and `display_pipeline`) and `src/display/display_manager.py` (records `preprocess_ms`/`display_ms`, emits a `display_driver` stage with the driver type). Writes are best-effort — exceptions are swallowed so a benchmarking bug never breaks a refresh.

### Reading the data

- `python scripts/show_benchmarks.py` — quick CLI summary.
- `python scripts/export_benchmarks_report.py` — writes a fuller report.
- `/api/benchmarks/summary`, `/api/benchmarks/refreshes`, `/api/benchmarks/plugins`, `/api/benchmarks/stages` (`src/blueprints/settings/_benchmarks.py`) — used by the Settings page's **Advanced tools → Diagnostics snapshot** panel, which shows p50/p95 timing by stage for a selected window, per-plugin averages, recent refresh rows, and stage drill-down.

## Developer profiling tools

### pytest-benchmark (`tests/benchmarks/`)

Micro-benchmarks for hot paths (cache lookups, image processing, config reads, plugin render pipelines) — deterministic, hermetic, each under 1 second.

```bash
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only -k test_http_cache_hit_lookup
```

| Column | Meaning |
|---|---|
| `min` | Fastest single iteration |
| `mean` | Average over all rounds |
| `median` | Less skewed by outliers than mean |
| `stddev` | Spread; high = noisy |
| `ops` | Iterations/sec (`1/mean`) |
| `rounds` | Timing rounds collected |

Comparing runs:

```bash
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only --benchmark-save=before
# make your change
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only --benchmark-save=after
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only --benchmark-compare=before
.venv/bin/python -m pytest tests/benchmarks/ --benchmark-only --benchmark-compare=before --benchmark-compare-fail=mean:10%
```

Saved runs land in `.benchmarks/` (gitignored).

### CI regression gate

Every PR runs the benchmarks and compares against a cached CI baseline (per-OS GitHub Actions cache; a push to `main` becomes the new baseline). `tests/benchmarks/baseline.json` is the committed fallback for local dev / when no CI cache exists. Threshold defaults to +15% (override via `BENCHMARK_THRESHOLD_PCT`). Comparison script: `scripts/benchmark_compare.py`. Runs as part of the `lint` job in `.github/workflows/ci.yml`; every PR uploads a `benchmark-results` artifact.

To update the baseline after a legitimate perf change:

```bash
SKIP_BROWSER=1 PYTHONPATH=src pytest tests/benchmarks/ --benchmark-only \
  --benchmark-json=tests/benchmarks/baseline.json -q
git add tests/benchmarks/baseline.json
```

New benchmarks must have no network/wall-clock dependency, run well under 1s, and represent a real hot path — regenerate the baseline after adding one.

### `scripts/test_profile.sh`

Wraps pytest with `--durations=25` for a quick scan of slow tests:

```bash
scripts/test_profile.sh                                       # defaults to tests/plugins
scripts/test_profile.sh tests/plugins/test_clock_plugin.py
PYTEST_DURATIONS=50 scripts/test_profile.sh
```

Activates `.venv`, sets `PYTHONPATH=src`, `SKIP_UI=1`, `SKIP_A11Y=1` to skip browser-dependent tests.

### cProfile

```bash
.venv/bin/python -m cProfile -o /tmp/out.prof -m pytest tests/plugins/test_clock_plugin.py -x -q
.venv/bin/pip install snakeviz
.venv/bin/python -m snakeviz /tmp/out.prof   # sunburst call-graph in browser; cumtime is usually most useful
```

Or profile a snippet directly:

```python
import cProfile, pstats, io
pr = cProfile.Profile()
pr.enable()
# ... code under test ...
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats('cumulative').print_stats(20)
print(s.getvalue())
```

### py-spy

Sampling profiler, attaches to a running process without modifying code — low overhead, suitable for production-like investigation.

```bash
pip install py-spy   # system Python, not venv — needs root or ptrace
.venv/bin/python src/inkypi.py --dev --web-only &
sudo py-spy record -o /tmp/profile.svg --pid $! --duration 30
```

`py-spy top` gives a live `top`-style view without waiting for a full recording.

### Which tool when

| Situation | Tool |
|---|---|
| Detect regressions across PRs | `pytest-benchmark` + `--benchmark-compare` |
| Find which test is slow | `scripts/test_profile.sh` |
| Drill into a slow function's call graph | `cProfile` + `snakeviz` |
| Profile a live/production-like process | `py-spy` |
| Per-refresh timings on the actual device | Runtime benchmark system, above |
