"""Shared private-network access-control helper for sensitive introspection routes.

Endpoints that surface system internals (diagnostics, logs) should not be
reachable from the open internet on an unauthenticated deployment. When the
app-wide PIN auth gate is enabled, the ``before_request`` hook has already
authenticated the caller (or redirected to /login) by the time a route body
runs, so this helper trusts that gate. When PIN auth is *disabled* (no
``INKYPI_AUTH_PIN`` configured), access is restricted to loopback/private
network callers unless ``INKYPI_ENV=dev`` explicitly opts in for local
development.

Originally introduced for ``/api/diagnostics`` (JTN-707); shared with the
``/api/logs`` and ``/download-logs`` routes so all three sensitive
introspection endpoints apply the same gate.
"""

from __future__ import annotations

import ipaddress
import os

from flask import current_app, request


def is_private_address(addr: str | None) -> bool:
    """Return True when *addr* is a loopback or RFC1918/ULA private address.

    Unknown / unparseable values are treated as non-private (fail closed).
    """
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def local_or_authenticated_access_allowed(
    endpoint_label: str,
) -> tuple[bool, str | None]:
    """Return (allowed, reason-if-denied) for a sensitive introspection endpoint.

    When PIN auth is enabled app-wide, the before_request hook has already
    authenticated the caller (or redirected them to /login). In that case we
    trust the gate and allow the request.

    When PIN auth is disabled, we fall back to restricting access to private
    network addresses. ``INKYPI_ENV=dev`` disables this guardrail so local
    development / tests are unimpeded.

    *endpoint_label* is interpolated into the denial reason (e.g.
    ``"diagnostics endpoint requires authentication or local access"``).
    """
    if current_app.config.get("AUTH_ENABLED"):
        return True, None

    env = (os.getenv("INKYPI_ENV") or "").strip().lower()
    if env == "dev":
        return True, None

    if is_private_address(request.remote_addr):
        return True, None

    return False, f"{endpoint_label} requires authentication or local access"
