# Security

## Optional PIN authentication

InkyPi supports an optional PIN that, when configured, protects all routes behind a login form. **Off by default** — with no PIN set, the app behaves identically to unauthenticated mode.

### Enabling

Environment variable (recommended, never touches disk):

```bash
export INKYPI_AUTH_PIN="your-pin-here"
```

Or in `device.json`:

```json
{ "auth": { "pin": "your-pin-here" } }
```

The env-var approach is preferred — a config-file PIN stays in plaintext on disk. The PIN is hashed with `hashlib.scrypt` (per-process random salt) immediately on startup; the plaintext is never stored or logged. Sessions are signed by Flask's `SECRET_KEY` — rotate it to invalidate all existing sessions.

### Behavior when enabled

- All routes except `/login`, `/logout`, `/sw.js`, `/static/*`, `/api/health/plugins`, `/api/health/system`, `/healthz`, and `/readyz` redirect unauthenticated users to `/login`.
- A successful login sets a server-side session cookie for the browser session.
- 5 consecutive failed attempts locks the session out for 60 seconds.
- `/logout` clears the session.
- Comparison uses `hmac.compare_digest` (timing-attack resistant).
- Combine with HTTPS (below) so the PIN isn't sent in the clear.

## HTTPS upgrade redirect

```bash
export INKYPI_FORCE_HTTPS=1
export INKYPI_ALLOWED_HOSTS="inkypi.local,inkypi.example.com"  # default: inkypi.local,localhost,127.0.0.1
```

A `before_request` hook in the security middleware redirects plain HTTP to HTTPS. Requests arriving with `X-Forwarded-Proto: https` (behind a TLS-terminating reverse proxy) pass through unchanged. `--dev` mode always skips the redirect.

The redirect hook validates the inbound `Host` header against `INKYPI_ALLOWED_HOSTS` before building the `Location` header — a host not in the allow-list gets a `400` instead of a redirect, which defends against open-redirect (`Host`-header spoofing into `Location: https://evil.example/`). If you reach the server by a hostname not in the default list (custom mDNS name, public DNS record), add it to `INKYPI_ALLOWED_HOSTS` or all HTTP traffic gets rejected with a 400.

## Read-only API token

An optional bearer token for monitoring/automation that needs to poll status endpoints without an interactive PIN session. Independent of PIN auth — works whether or not a PIN is configured.

```bash
export INKYPI_READONLY_TOKEN="your-long-random-token-here"
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # generate one
```

```bash
curl -H "Authorization: Bearer <your-token>" http://inkypi.local:5000/api/uptime
```

Grants **GET/HEAD/OPTIONS only** to: `/api/health/plugins`, `/api/health/system`, `/api/version/info`, `/api/uptime`, `/api/screenshot`, `/metrics`, `/api/stats`. Any other path, or any mutating method on these paths, needs a PIN session — a valid token never grants access to admin or mutating routes.

The raw token is never stored; only its SHA-256 hex digest lives in memory, compared with `hmac.compare_digest`. Rotate by restarting with a new value.

## Private-network fallback for sensitive introspection routes

`/api/diagnostics`, `/api/logs`, and `/download-logs` surface system internals and shouldn't be reachable from the open internet on an unauthenticated deployment. `src/utils/access_control.py` gates them:

- If PIN auth is enabled, the `before_request` hook has already authenticated the caller — these routes trust that gate.
- If PIN auth is **disabled**, access is restricted to loopback/private-network (RFC1918/ULA) callers, unless `INKYPI_ENV=dev` explicitly opts out for local development.

This applies independently of whether a read-only token is configured — the token allowlist above doesn't cover these routes.

## Software Bill of Materials (SBOM)

Every GitHub release includes a CycloneDX JSON SBOM (`inkypi-vX.Y.Z-bom.json`) listing all bundled Python packages.

```bash
gh release download vX.Y.Z --repo florentineprinzessinzusachsen/InkyPi-modernize --pattern 'inkypi-vX.Y.Z-bom.json'
```

```bash
brew install cyclonedx/cyclonedx/cyclonedx-cli   # or download from CycloneDX/cyclonedx-cli releases
cyclonedx-cli validate --input-file inkypi-vX.Y.Z-bom.json --input-format json
cyclonedx-cli convert --input-file inkypi-vX.Y.Z-bom.json --input-format json \
  --output-file inkypi-vX.Y.Z-bom.spdx --output-format spdxtag

pip install pip-audit
pip-audit --sbom inkypi-vX.Y.Z-bom.json
```

## Reporting a vulnerability

Open a [GitHub Security Advisory](https://github.com/florentineprinzessinzusachsen/InkyPi-modernize/security/advisories/new) rather than a public issue.
