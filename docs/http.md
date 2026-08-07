# HTTP: Session Pooling & Response Caching

InkyPi targets the Raspberry Pi Zero 2 W (512 MB RAM, single-core-class throughput), where every unnecessary TLS handshake costs 100–400 ms. All plugin HTTP traffic should go through the shared infrastructure in `src/utils/http_client.py` / `src/utils/http_utils.py` rather than bare `requests.get()`.

## Which entry point to use

| Scenario | Use |
|---|---|
| Data valid for minutes (weather, RSS, APOD) | `http_get()` — caching on by default |
| Same URL requested more than once per refresh cycle | `http_get()` — second call is free |
| POST/PUT/DELETE (writes) | `get_http_session().post(...)` — cache only covers GET |
| One-shot read where staleness would be wrong (live scores, stock prices) | `get_http_session().get(url, timeout=…)`, or `http_get(url, use_cache=False)` |
| Streaming a large binary (image download) | `get_http_session().get(url, stream=True, timeout=…)` — cache stores full content, so skip it |

**Decision rule:** repeat within a refresh cycle, or data stable for a few minutes → `http_get()`. Writing data or a one-shot endpoint → the raw session.

## `get_http_session()`

`get_http_session()` (`src/utils/http_client.py`) returns the plugin-facing session, built from the same pooled adapter used by `http_get()`. A shared session buys three things on Pi hardware:

1. **TLS session resumption** — reusing a negotiated session avoids the ~200–400 ms cold-handshake cost.
2. **TCP keep-alive** — avoids repeating the ~20–50 ms three-way handshake per request to the same host.
3. **Automatic retry** — the adapter retries HTTP 429/500/502/503/504 with 0.5s exponential backoff, up to 3 attempts, for idempotent methods (GET/HEAD/OPTIONS).

Calling `requests.get(url)` directly pays full TLS+TCP cost every time with no retry behavior.

### Pool sizing

```python
requests.adapters.HTTPAdapter(
    pool_connections=10,   # distinct host connection pools
    pool_maxsize=10,       # sockets kept open per host pool
    max_retries=retry_strategy,
    pool_block=False,
)
```

10 pools covers the realistic number of distinct hosts InkyPi talks to in one refresh cycle (weather API, GitHub, Wikimedia, etc.) without holding open dozens of sockets a 512 MB device can't spare. `pool_block=False` means a host with all 10 sockets busy opens a temporary extra connection rather than blocking the refresh thread — trading a little memory for deadlock-freedom, which is correct for a single-device daemon. Worst-case memory: ~100 sockets × ~12 KB ≈ 1.2 MB; real resident overhead is well under 200 KB in practice.

### Timeouts

Default is **20 seconds**, from `INKYPI_HTTP_TIMEOUT_DEFAULT_S`. Split timeouts are optional:

| Env var | Default | Meaning |
|---|---|---|
| `INKYPI_HTTP_TIMEOUT_DEFAULT_S` | `20.0` | Combined connect+read timeout |
| `INKYPI_HTTP_CONNECT_TIMEOUT_S` | unset | Connect-only, overrides default when set |
| `INKYPI_HTTP_READ_TIMEOUT_S` | unset | Read-only, overrides default when set |

```python
from utils.http_client import get_http_session

session = get_http_session()
response = session.get(url, timeout=5)          # tight timeout for a fast API you control
response = session.get(url, timeout=(5, 30))     # 5s connect, 30s read for a large payload
```

**Never omit a timeout** — a hung socket blocks the refresh thread indefinitely on Pi Zero.

## `http_get()` — caching + pooling + latency logging

```python
from utils.http_utils import http_get

response = http_get(url, timeout=15)
response = http_get(url, cache_ttl=3600)          # cache for 1 hour instead of the 5-minute default
response = http_get(url, use_cache=False)         # force a fresh request
response = http_get(url, params={"city": "NYC"})  # query params are part of the cache key
```

`http_get()` shares the pooled session factory with `get_http_session()` but keeps its own thread-local lifecycle (different retry defaults for the request wrapper). It respects `Cache-Control` response headers: `max-age=N` sets that TTL, `no-cache`/`max-age=0` disables caching for that response.

### Cache configuration

```bash
export INKYPI_HTTP_CACHE_ENABLED=true   # default: true
export INKYPI_HTTP_CACHE_TTL_S=600      # default: 300 (5 min)
export INKYPI_HTTP_CACHE_MAX_SIZE=200   # default: 100 entries
```

- Automatic for all GET requests; thread-safe (RLock); LRU eviction when full; expired entries cleaned up on access.
- Different query parameters → different cache entries.
- Multiple plugins hitting the same URL share one cache entry (e.g. a weather call at `00:00` and another plugin's identical call at `00:02` reuse the same response).

### Inspecting/clearing the cache

```python
from utils.http_cache import get_cache_stats, clear_cache, get_cache

stats = get_cache_stats()
# {'hits': 45, 'misses': 12, 'expirations': 3, 'evictions': 0, 'errors': 0,
#  'hit_rate': 78.95, 'size': 25, 'max_size': 100, 'enabled': True}

clear_cache()

cache = get_cache()
cache.get(url)
cache.put(url, response, ttl=600)
cache.remove_expired()
```

## Plugin author checklist

- Use `get_http_session()` instead of bare `requests.get()` or `requests.Session()`.
- Set an explicit `timeout` on every call.
- Catch `requests.exceptions.ReadTimeout` and `requests.exceptions.ConnectionError` (the two most common failures on intermittent home Wi-Fi) and re-raise as a user-friendly `RuntimeError`.
- Prefer `http_get()` for anything read more than once per refresh cycle.
- Don't create a bare `requests.Session()` unless you need a genuinely different adapter (custom SSL context, different retry policy) — document why if you do.

```python
import requests.exceptions
from utils.http_client import get_http_session

def fetch_data(url: str, api_key: str) -> dict:
    session = get_http_session()
    try:
        resp = session.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ReadTimeout as exc:
        raise RuntimeError("API request timed out") from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError("Could not reach API") from exc
```

`src/plugins/comic/comic_parser.py` is a deliberate exception: it calls `http_get(..., use_cache=False)` rather than `get_http_session()` directly, since feed content changes frequently — it still gets pooling from the shared session underneath, just no caching.
