"""Device-level connectivity monitoring for ``RefreshTask``.

Without this, an internet outage looks identical - from each plugin's own
point of view - to that one specific external API being broken: every
configured plugin independently burns through its own circuit-breaker
threshold and gets paused, even though nothing about any of them was
actually wrong. A real overnight wifi outage produced exactly that: two
unrelated plugins hitting two different hosts, both paused within minutes
of each other (see refresh_task.health's circuit breaker cooldown).

``ConnectivityMonitor`` checks reachability once, up front, independent of
any plugin. When the device is offline, ``RefreshTask`` skips the
automatic playlist-cycling refresh entirely for that tick - nothing is
attempted, so no plugin's failure count moves and nothing gets paused for
a reason that was never its own fault. A background thread re-probes on
its own fixed cadence (default 5 minutes, decoupled from
``plugin_cycle_interval_seconds`` - see RefreshTask._connectivity_heartbeat_loop),
and a manual recheck (the sidebar's "Retry" action - see
blueprints/settings/_health.py) can force an immediate probe.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class ConnectivityMonitor:
    """Tracks whether the device currently has a working internet connection."""

    def __init__(self, check_fn: Callable[[], bool] | None = None) -> None:
        if check_fn is None:
            from utils.app_utils import is_connected

            check_fn = is_connected
        self._check_fn = check_fn
        self._lock = threading.Lock()
        # Optimistic default: assume online until the first real probe runs,
        # so a normal boot with working internet never shows a false
        # "offline" flash before that first check completes (it runs almost
        # immediately after the background thread starts - see
        # RefreshTask._connectivity_heartbeat_loop - so this window is brief).
        self._online = True
        self._last_checked_at: str | None = None
        self._last_changed_at: str | None = None

    @staticmethod
    def check_interval_seconds(environ: Mapping[str, str] | None = None) -> int:
        """How often the background monitor re-probes connectivity.

        Read from ``INKYPI_CONNECTIVITY_CHECK_S``, clamped to a minimum of
        30s so a misconfigured "0" can't turn this into a tight probe loop.
        Default 300s (5 minutes) per the explicit design brief this
        implements.
        """
        env = os.environ if environ is None else environ
        try:
            value = int(env.get("INKYPI_CONNECTIVITY_CHECK_S", "300"))
        except (ValueError, TypeError):
            return 300
        return max(30, value)

    def check_now(self) -> bool:
        """Run a real connectivity probe synchronously and update state.

        Safe to call from any thread - the background heartbeat loop and a
        manual recheck request can both land here concurrently; the lock
        only guards the small state update, not the (potentially slow,
        network-bound) probe itself.
        """
        online = bool(self._check_fn())
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            changed = online != self._online
            self._online = online
            self._last_checked_at = now_iso
            if changed:
                self._last_changed_at = now_iso
        if changed:
            logger.log(
                logging.INFO if online else logging.WARNING,
                "device connectivity: %s",
                "restored" if online else "lost",
            )
        return online

    @property
    def online(self) -> bool:
        with self._lock:
            return self._online

    def snapshot(self) -> dict[str, object]:
        """Return the current cached state without forcing a new probe."""
        with self._lock:
            return {
                "online": self._online,
                "last_checked_at": self._last_checked_at,
                "last_changed_at": self._last_changed_at,
            }
