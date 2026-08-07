"""Scheduling helpers for the refresh task loop."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from refresh_task.actions import ManualUpdateRequest

logger = logging.getLogger(__name__)


class SupportsRefreshScheduling(Protocol):
    """Config surface needed to wait for the next refresh trigger."""

    def get_config(self, key: str, default: object = ...) -> object: ...

    def get_playlist_manager(self) -> object: ...

    def get_refresh_info(self) -> object: ...


class RefreshScheduler:
    """Owns watchdog cadence and trigger waiting for ``RefreshTask``."""

    def __init__(
        self,
        device_config: SupportsRefreshScheduling,
        condition: threading.Condition,
        manual_update_requests: deque[ManualUpdateRequest],
        get_current_datetime: Callable[[], datetime],
    ) -> None:
        self.device_config = device_config
        self.condition = condition
        self.manual_update_requests = manual_update_requests
        self.get_current_datetime = get_current_datetime
        # Monotonic (not wall-clock) target for the next automatic tick - see
        # wait_for_trigger()'s docstring for why this needs to be a fixed-rate
        # grid rather than a fixed-delay sleep.
        self._next_wake_monotonic: float | None = None

    def reset(self) -> None:
        """Drop the fixed-rate grid so the next wait starts a fresh cycle.

        Call this whenever the background loop (re)starts, so a stale target
        computed before a stop/start cycle can't make the first wait return
        immediately.
        """
        self._next_wake_monotonic = None

    @staticmethod
    def watchdog_interval_seconds(environ: Mapping[str, str] | None = None) -> float:
        """Return half of ``WATCHDOG_USEC`` in seconds, with sane defaults."""
        env = os.environ if environ is None else environ
        try:
            usec = int(env.get("WATCHDOG_USEC", "0"))
        except (ValueError, TypeError):
            usec = 0
        if usec <= 0:
            return 30.0
        return max(1.0, (usec / 1_000_000) / 2)

    @staticmethod
    def notify_watchdog(sd_notify: Callable[[str], None] | None) -> None:
        """Best-effort systemd watchdog notification."""
        if sd_notify is None:
            return
        try:
            sd_notify("WATCHDOG=1")
        except Exception:
            logger.exception("Failed to notify systemd watchdog")

    def watchdog_heartbeat_loop(
        self,
        *,
        is_running: Callable[[], bool],
        notify_watchdog: Callable[[], None],
        interval_seconds: float,
    ) -> None:
        """Feed the watchdog on a fixed cadence until the task stops."""
        while is_running():
            notify_watchdog()
            with self.condition:
                self.condition.wait(timeout=interval_seconds)

    def wait_for_trigger(
        self, *, is_running: Callable[[], bool]
    ) -> tuple[object, object, datetime, ManualUpdateRequest | None] | None:
        """Block until the next interval tick or manual update request.

        Ticks land on a fixed-rate grid (target = last target + interval)
        rather than a fixed delay re-armed from "now" after every cycle. A
        fixed-delay sleep ignores how long the previous refresh cycle's own
        work (plugin fetch/render, display push) took, so that duration gets
        added on top of the configured interval every single cycle - a
        2-minute interval with a 90s refresh compounds into an observed
        ~3.5-minute cadence, drifting further if the next cycle also runs
        long. Anchoring to a monotonic grid instead means any time spent
        doing that work is simply subtracted from the following sleep, so
        the cadence between ticks converges on the configured interval
        rather than interval-plus-work.
        """
        with self.condition:
            interval = self._cycle_interval_seconds()
            now = time.monotonic()
            if self._next_wake_monotonic is None:
                self._next_wake_monotonic = now + interval
            sleep_time = self._next_wake_monotonic - now

            if not is_running():
                return None

            if self.manual_update_requests:
                # Already-queued manual request: service it now without
                # touching the automatic grid - it hasn't actually ticked.
                pass
            elif sleep_time <= 0:
                # A previous cycle's work ate the whole gap (or more) before
                # we even got back here. Treat this as a natural tick.
                self._advance_grid(interval)
            else:
                woke_early = self.condition.wait(timeout=sleep_time)
                if not is_running():
                    return None
                if woke_early and not self.manual_update_requests:
                    # Notified before the deadline by something other than a
                    # manual request - most likely a config change (interval
                    # edited from the settings page). Re-read the (possibly
                    # new) interval and resync the grid to start counting
                    # from now, instead of keeping a target computed from the
                    # stale interval.
                    interval = self._cycle_interval_seconds()
                    self._next_wake_monotonic = time.monotonic() + interval
                elif not woke_early:
                    self._advance_grid(interval)
                # else: woke early because a manual request just arrived -
                # leave the grid target untouched for the next real check.

            if not is_running():
                return None

            playlist_manager = self.device_config.get_playlist_manager()
            latest_refresh = self.device_config.get_refresh_info()
            current_dt = self.get_current_datetime()
            manual_request = None
            if self.manual_update_requests:
                manual_request = self.manual_update_requests.popleft()
            return playlist_manager, latest_refresh, current_dt, manual_request

    def _advance_grid(self, interval: float) -> None:
        """Move the fixed-rate target forward by one interval.

        If a cycle ran long enough to eat more than one full interval, jump
        to "now" instead of firing a burst of back-to-back catch-up ticks.
        """
        base = self._next_wake_monotonic
        if base is None:
            base = time.monotonic()
        self._next_wake_monotonic = max(base + interval, time.monotonic())

    def _cycle_interval_seconds(self) -> float:
        """Read the configured refresh interval with a safe fallback."""
        raw_value = self.device_config.get_config(
            "plugin_cycle_interval_seconds", default=60 * 60
        )
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value or 60 * 60)
            except ValueError:
                return float(60 * 60)
        return float(60 * 60)
