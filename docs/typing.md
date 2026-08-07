# Type-Checking Strategy

InkyPi uses [mypy](https://mypy.readthedocs.io/) for static analysis, following an **incremental strict** path: a curated subset of modules is held to `--strict` as a hard CI blocker, while the rest of `src/` is checked non-strict against a zero baseline and `tests/` is ratcheted against a checked-in baseline. This avoids enabling `--strict` everywhere at once (which would produce hundreds of errors and churn on actively-changing files) while still raising the bar module by module.

## Current strict subset (CI-blocking)

See `mypy.ini` for the authoritative list (`grep -B1 "strict = True" mypy.ini`). As of this writing:

`utils/http_utils.py`, `utils/plugin_errors.py`, `utils/security_utils.py`, `utils/client_endpoint.py`, `utils/display_names.py`, `utils/messages.py`, `utils/output_validator.py`, `utils/paths.py`, `utils/refresh_info.py`, `utils/refresh_stats.py`, `utils/sri.py`, `utils/time_utils.py`, `utils/http_cache.py`, `utils/request_models.py`, `refresh_task/actions.py`, `refresh_task/context.py`, `refresh_task/worker.py`, `model.py`.

## Adding a module to the strict subset

1. Run mypy strict on the module locally and fix all errors:
   ```bash
   .venv/bin/python -m mypy --strict src/utils/your_module.py
   ```
2. Add a per-module block to `mypy.ini`:
   ```ini
   [mypy-utils.your_module]
   strict = True
   ```
3. Add the file to the blocking check in `scripts/checks/lint.sh` (alongside the existing strict-subset invocation).
4. Open a PR — CI enforces strictness from that point forward.

## CI behavior: clean `src/`, ratcheted `tests/`

`scripts/checks/lint.sh` runs mypy as three separate passes:

1. `mypy src/` — production code, compared against `scripts/checks/mypy_src_baseline.txt`. This baseline should stay `0`; CI fails on any reported issue or a run that can't produce a summary.
2. `mypy tests/` — compared against `scripts/checks/mypy_tests_baseline.txt`. CI fails if the error count rises above the committed baseline.
3. `mypy --strict ...` — the curated subset above, fully blocking regardless of the other two.

The split exists because the test suite carries far more typing noise than `src/` (fixtures, monkeypatching, duck-typed stubs). Combining both into one run let small production regressions get lost in thousands of test-only errors.

### If the `src/` count changes

1. Run `mypy src/` locally, diff against `main`.
2. If your PR introduced the errors, fix them before merging.
3. If unrelated, call it out in the PR and fix the underlying dependency/config issue rather than raising the baseline.
4. If it stays at zero, prefer adding newly-stable modules to the strict subset over changing the baseline.

If `mypy src/` exits without a `Found N errors` / `Success:` summary, treat it as a broken invocation — the ratchet fails until the underlying config/import problem is fixed.

### If the `tests/` count changes

1. Run `mypy tests/` locally, fix errors your PR introduced.
2. Treat baseline *increases* as exceptional and coordinated, not routine.
3. If it goes down, confirm by rerunning, then lower `scripts/checks/mypy_tests_baseline.txt` to the new integer. Pay down errors in clusters: shared fixtures first, then contract/security tests, then browser/integration tests.

## Coding guidelines for typed modules

- Avoid `Any` unless truly unavoidable; prefer `object` or a narrow union.
- Prefer `collections.abc.Callable`/`Sequence`/`Mapping` over their `typing` counterparts for argument types.
- Use `cast()` sparingly — only when mypy can't infer a type you know is correct (e.g. narrowing an untyped third-party return value).
- Add `# type: ignore[<code>]` only as a last resort, with a narrow error code and an inline comment explaining why.
