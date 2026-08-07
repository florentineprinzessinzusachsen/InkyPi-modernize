"""Plugin health and circuit-breaker helpers for ``RefreshTask``."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC
from typing import TYPE_CHECKING, Any, Protocol, cast

from utils.metrics import (
    record_refresh_failure,
    record_refresh_success,
    set_circuit_breaker_open,
)
from utils.time_utils import now_device_tz

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config import Config

HealthEntry = dict[str, object]
Metrics = dict[str, object]


class PluginInstanceLike(Protocol):
    """Playlist instance surface needed for circuit-breaker updates."""

    paused: bool
    consecutive_failure_count: int
    disabled_reason: str | None


class SupportsPlaylistLookup(Protocol):
    """Playlist manager surface needed to resolve plugin instances."""

    def find_plugin(
        self, plugin_id: str, instance_name: str
    ) -> PluginInstanceLike | None: ...


class SupportsPluginHealth(Protocol):
    """Config surface needed by plugin-health bookkeeping."""

    def get_playlist_manager(self) -> SupportsPlaylistLookup: ...

    def get_config(self, key: str, default: object = ...) -> object: ...

    def write_config(self) -> None: ...

    def update_atomic(self, update_fn: Callable[[dict[str, Any]], None]) -> None: ...


class PluginHealthTracker:
    """Tracks per-plugin health and owns circuit-breaker transitions."""

    # Weight given to the newest sample in the exponential moving average of
    # per-plugin timing metrics - low enough that one slow/fast outlier
    # (a cold cache, a transient network hiccup) doesn't swing the average,
    # high enough that a genuine, sustained change in a plugin's typical
    # duration (e.g. a provider getting slower) is reflected within a
    # handful of refresh cycles rather than dozens.
    _AVG_ALPHA = 0.3

    def __init__(
        self,
        device_config: SupportsPluginHealth,
        plugin_health: dict[str, HealthEntry] | None = None,
    ) -> None:
        self.device_config = device_config
        self.plugin_health = plugin_health if plugin_health is not None else {}

    @staticmethod
    def circuit_breaker_threshold(environ: Mapping[str, str] | None = None) -> int:
        """Return the consecutive-failure threshold before pausing a plugin.

        The value is read from ``PLUGIN_FAILURE_THRESHOLD`` and clamped to a
        minimum of 1 (so ``"0"`` becomes ``1``, not ``5``). Invalid values
        (non-integer strings, etc.) fall back to the default of ``5``.
        """
        env = os.environ if environ is None else environ
        try:
            value = int(env.get("PLUGIN_FAILURE_THRESHOLD", "5"))
        except (ValueError, TypeError):
            return 5
        return max(1, value)

    @staticmethod
    def circuit_breaker_cooldown_seconds(environ: Mapping[str, str] | None = None) -> int:
        """Return how long a paused plugin waits before an automatic retry.

        A transient outage (wifi drop, upstream API blip) can easily outlast
        a plugin's failure threshold, and there was previously no way back
        from `paused` short of a human noticing and hitting force_retry -
        on unattended hardware that meant staying blank indefinitely even
        after the underlying problem cleared itself hours earlier. Read from
        ``PLUGIN_CIRCUIT_BREAKER_COOLDOWN_S``, clamped to a minimum of 60s so
        a misconfigured "0" can't turn this back into "retry every cycle"
        and defeat the point of pausing at all. Default 1800s (30 min).
        """
        env = os.environ if environ is None else environ
        try:
            value = int(env.get("PLUGIN_CIRCUIT_BREAKER_COOLDOWN_S", "1800"))
        except (ValueError, TypeError):
            return 1800
        return max(60, value)

    def update(
        self,
        *,
        plugin_id: str,
        instance: str | None,
        ok: bool,
        metrics: Metrics | None,
        error: str | None,
        on_success: Callable[[PluginInstanceLike | None, str, str | None], None],
        on_failure: Callable[[PluginInstanceLike | None, str, str | None], None],
    ) -> None:
        """Update the plugin-health entry and invoke circuit-breaker hooks."""
        now_iso = self._now_iso()
        entry: HealthEntry = dict(self.plugin_health.get(plugin_id, {}))
        entry.setdefault("success_count", 0)
        entry.setdefault("failure_count", 0)
        entry.setdefault("retry_count", 0)
        entry.setdefault("timeout_count", 0)
        entry["instance"] = instance
        entry["last_seen"] = now_iso

        plugin_instance = self._find_plugin_instance(plugin_id, instance)

        if ok:
            entry["status"] = "green"
            entry["last_success_at"] = now_iso
            entry["last_error"] = None
            entry["success_count"] = self._entry_int(entry, "success_count") + 1
            entry["failure_count"] = 0
            entry["retained_display"] = False
            if metrics:
                entry["last_metrics"] = metrics
                self._update_average_metrics(entry, metrics)
                self._warn_if_interval_too_tight(plugin_id, entry)
            self.plugin_health[plugin_id] = entry
            record_refresh_success()
            on_success(plugin_instance, plugin_id, instance)
            return

        msg = error or "unknown error"
        entry["status"] = "red"
        entry["last_failure_at"] = now_iso
        entry["last_error"] = msg
        entry["failure_count"] = self._entry_int(entry, "failure_count") + 1
        if "timed out" in msg.lower():
            entry["timeout_count"] = self._entry_int(entry, "timeout_count") + 1
        entry["retry_count"] = int(os.getenv("INKYPI_PLUGIN_RETRY_MAX", "1") or "1")
        entry["retained_display"] = bool((metrics or {}).get("retained_display"))
        if metrics:
            entry["last_metrics"] = metrics
        self.plugin_health[plugin_id] = entry
        record_refresh_failure(plugin_id)
        on_failure(plugin_instance, plugin_id, instance)

    def on_success(
        self,
        plugin_instance: PluginInstanceLike | None,
        plugin_id: str,
        instance: str | None,
    ) -> None:
        """Reset the circuit breaker after a successful refresh."""
        if plugin_instance is None:
            return
        changed = (
            plugin_instance.paused or plugin_instance.consecutive_failure_count > 0
        )
        if changed:
            logger.info(
                "plugin circuit_breaker: recovered | plugin_id=%s instance=%s",
                plugin_id,
                instance,
            )
        def _apply(_cfg: dict[str, Any]) -> None:
            plugin_instance.consecutive_failure_count = 0
            plugin_instance.paused = False
            plugin_instance.disabled_reason = None
            plugin_instance.paused_at = None

        set_circuit_breaker_open(plugin_id, False)
        if changed:
            # Mutate-then-persist atomically under the config lock, so a
            # concurrent web-request thread's update_atomic() can't
            # interleave a write between these attribute mutations and
            # ours (see Config.update_atomic's docstring).
            try:
                self.device_config.update_atomic(_apply)
            except Exception:
                logger.warning(
                    "plugin circuit_breaker: failed to persist reset for %s/%s",
                    plugin_id,
                    instance,
                    exc_info=True,
                )
        else:
            _apply({})

    def on_failure(
        self,
        plugin_instance: PluginInstanceLike | None,
        plugin_id: str,
        instance: str | None,
        *,
        webhook_sender: Callable[[list[str], dict[str, object]], None] | None = None,
    ) -> None:
        """Increment failure state and trip the circuit breaker when needed."""
        if plugin_instance is None or plugin_instance.paused:
            return
        threshold = self.circuit_breaker_threshold()

        # Mutate-then-persist atomically under the config lock (see
        # Config.update_atomic's docstring) so a concurrent web-request
        # thread's own update_atomic() can't interleave a write between the
        # increment/pause-decision below and ours, which could otherwise
        # persist a torn combination (e.g. paused=True but the failure count
        # not yet bumped).
        def _apply(_cfg: dict[str, Any]) -> None:
            plugin_instance.consecutive_failure_count += 1
            logger.warning(
                "plugin circuit_breaker: failure | plugin_id=%s instance=%s count=%d/%d",
                plugin_id,
                instance,
                plugin_instance.consecutive_failure_count,
                threshold,
            )
            if plugin_instance.consecutive_failure_count >= threshold:
                now_iso = self._now_iso()
                error_msg = str(
                    self.plugin_health.get(plugin_id, {}).get("last_error")
                    or "unknown"
                )
                plugin_instance.paused = True
                plugin_instance.paused_at = now_iso
                plugin_instance.disabled_reason = (
                    f"Paused after {plugin_instance.consecutive_failure_count} consecutive "
                    f"failures at {now_iso}. Last error: {error_msg[:120]}"
                )
                set_circuit_breaker_open(plugin_id, True)
                logger.error(
                    "plugin circuit_breaker: paused | plugin_id=%s instance=%s"
                    " paused after %d consecutive failures",
                    plugin_id,
                    instance,
                    plugin_instance.consecutive_failure_count,
                )

        try:
            self.device_config.update_atomic(_apply)
        except Exception:
            logger.warning(
                "plugin circuit_breaker: failed to persist failure state for %s/%s",
                plugin_id,
                instance,
                exc_info=True,
            )

        self._send_failure_webhook(
            plugin_id=plugin_id,
            instance=instance,
            webhook_sender=webhook_sender,
        )

    def reset_circuit_breaker(self, plugin_id: str, instance: str) -> bool:
        """Clear the paused state and failure counter for a plugin instance."""
        plugin_instance = self._find_plugin_instance(plugin_id, instance)
        if plugin_instance is None:
            return False
        changed = (
            plugin_instance.paused
            or plugin_instance.consecutive_failure_count > 0
            or plugin_instance.disabled_reason is not None
        )
        def _apply(_cfg: dict[str, Any]) -> None:
            plugin_instance.consecutive_failure_count = 0
            plugin_instance.paused = False
            plugin_instance.disabled_reason = None
            plugin_instance.paused_at = None

        set_circuit_breaker_open(plugin_id, False)
        safe_pid = str(plugin_id).replace("\r", "").replace("\n", "")[:64]
        safe_inst = str(instance).replace("\r", "").replace("\n", "")[:64]
        logger.info(
            "plugin circuit_breaker: manual_reset | plugin_id=%s instance=%s",
            safe_pid,
            safe_inst,
        )
        if changed:
            # Mutate-then-persist atomically under the config lock - see
            # Config.update_atomic's docstring; this can genuinely race with
            # a refresh-triggered on_failure()/on_success() call on the
            # background RefreshTask thread, unlike most other callers here.
            try:
                self.device_config.update_atomic(_apply)
            except Exception:
                logger.warning(
                    "plugin circuit_breaker: failed to persist manual reset for %s/%s",
                    safe_pid,
                    safe_inst,
                    exc_info=True,
                )
        else:
            _apply({})
        return True

    def snapshot(self) -> dict[str, HealthEntry]:
        """Return a shallow copy of the health snapshot."""
        return dict(self.plugin_health)

    def _find_plugin_instance(
        self, plugin_id: str, instance: str | None
    ) -> PluginInstanceLike | None:
        if not instance:
            return None
        return self.device_config.get_playlist_manager().find_plugin(
            plugin_id, instance
        )

    def _send_failure_webhook(
        self,
        *,
        plugin_id: str,
        instance: str | None,
        webhook_sender: Callable[[list[str], dict[str, object]], None] | None,
    ) -> None:
        if webhook_sender is None:
            return
        try:
            webhook_urls = self.device_config.get_config("webhook_urls", default=[])
            if not isinstance(webhook_urls, list) or not webhook_urls:
                return
            now_iso = self._now_iso()
            error_msg = str(
                self.plugin_health.get(plugin_id, {}).get("last_error") or "unknown"
            )
            payload: dict[str, object] = {
                "event": "plugin_failure",
                "plugin_id": plugin_id,
                "instance_name": instance,
                "error": error_msg,
                "ts": now_iso,
            }
            webhook_sender(webhook_urls, payload)
        except Exception:
            logger.warning(
                "webhook: unexpected error building webhook payload", exc_info=True
            )

    def _now_iso(self) -> str:
        """Return the current device-local timestamp normalized to UTC ISO format."""
        device_config = cast("Config", self.device_config)
        current_dt = now_device_tz(device_config)
        return str(current_dt.astimezone(UTC).isoformat())

    @classmethod
    def _update_average_metrics(cls, entry: HealthEntry, metrics: Metrics) -> None:
        """Maintain an exponential moving average of a plugin's timing metrics.

        Tracks ``generate_ms`` (fetch/render), ``display_ms`` (hardware push)
        and ``request_ms`` (end-to-end) as ``avg_*`` fields alongside the
        existing ``last_metrics`` snapshot, so a plugin's *typical* cost is
        visible (via the diagnostics API's plugin_health snapshot) rather
        than just its most recent one. A cached-image cycle omits
        ``display_ms`` (no hardware push happened); that sample is simply
        skipped for that one field rather than counted as zero.
        """
        entry["avg_sample_count"] = cls._entry_int(entry, "avg_sample_count") + 1
        for key in ("generate_ms", "display_ms", "request_ms"):
            value = metrics.get(key)
            if not isinstance(value, (int, float)):
                continue
            avg_key = f"avg_{key}"
            previous = entry.get(avg_key)
            if isinstance(previous, (int, float)):
                entry[avg_key] = round(previous + cls._AVG_ALPHA * (value - previous), 1)
            else:
                entry[avg_key] = float(value)

    def _warn_if_interval_too_tight(self, plugin_id: str, entry: HealthEntry) -> None:
        """Log once when a plugin's average duration eats most of the cycle interval.

        A plugin that typically takes close to (or longer than) the
        configured ``plugin_cycle_interval_seconds`` never actually rests
        between refreshes - the scheduler's fixed-rate grid (see
        ``RefreshScheduler.wait_for_trigger``) keeps ticks evenly spaced, but
        it can't make a slow plugin's own fetch/render/display work finish
        any faster, so refreshes for it run back-to-back rather than at the
        configured cadence. Warns once per crossing (tracked via
        ``interval_tight_warned``) rather than every single refresh.
        """
        avg_request_ms = entry.get("avg_request_ms")
        if not isinstance(avg_request_ms, (int, float)):
            return
        try:
            interval_s = float(
                self.device_config.get_config(
                    "plugin_cycle_interval_seconds", default=3600
                )
            )
        except Exception:
            return
        if interval_s <= 0:
            return
        interval_ms = interval_s * 1000
        is_tight = avg_request_ms >= interval_ms * 0.8
        if is_tight and not entry.get("interval_tight_warned"):
            entry["interval_tight_warned"] = True
            logger.warning(
                "plugin timing: '%s' averages %.0fms per refresh, close to or "
                "over the configured %.0fs cycle interval - it will refresh "
                "back-to-back instead of resting between cycles. Consider "
                "raising plugin_cycle_interval_seconds.",
                plugin_id,
                avg_request_ms,
                interval_s,
            )
        elif not is_tight:
            entry["interval_tight_warned"] = False

    @staticmethod
    def _entry_int(entry: HealthEntry, key: str) -> int:
        """Coerce a health-entry counter to ``int`` with a safe default."""
        value = entry.get(key, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0
