"""Tests for ConnectivityMonitor (device-level online/offline gate).

Covers:
- Optimistic default state before any probe has run
- check_now() transitions and last_checked_at/last_changed_at bookkeeping
- check_interval_seconds() env var handling
- snapshot() shape
"""

import os

import pytest

from refresh_task.connectivity import ConnectivityMonitor


def _monitor(result):
    """Build a monitor whose check_fn always returns `result` (or, if a
    list, pops results left-to-right for multi-call scenarios)."""
    if isinstance(result, list):
        results = list(result)
        return ConnectivityMonitor(check_fn=lambda: results.pop(0))
    return ConnectivityMonitor(check_fn=lambda: result)


class TestDefaultState:
    def test_defaults_to_online_before_any_probe(self):
        monitor = _monitor(True)
        assert monitor.online is True
        snap = monitor.snapshot()
        assert snap == {
            "online": True,
            "last_checked_at": None,
            "last_changed_at": None,
        }


class TestCheckNow:
    def test_check_now_returns_result(self):
        monitor = _monitor(False)
        assert monitor.check_now() is False
        assert monitor.online is False

    def test_check_now_sets_last_checked_at(self):
        monitor = _monitor(True)
        assert monitor.snapshot()["last_checked_at"] is None
        monitor.check_now()
        assert monitor.snapshot()["last_checked_at"] is not None

    def test_no_change_does_not_set_last_changed_at(self):
        """Default is already online=True; a True result is not a transition."""
        monitor = _monitor(True)
        monitor.check_now()
        assert monitor.snapshot()["last_changed_at"] is None

    def test_transition_sets_last_changed_at(self):
        monitor = _monitor(False)
        monitor.check_now()
        assert monitor.snapshot()["last_changed_at"] is not None

    def test_repeated_same_result_does_not_move_last_changed_at(self):
        monitor = _monitor([False, False])
        monitor.check_now()
        first_changed = monitor.snapshot()["last_changed_at"]
        monitor.check_now()
        assert monitor.snapshot()["last_changed_at"] == first_changed

    def test_flip_back_online_updates_last_changed_at_again(self):
        monitor = _monitor([False, True])
        monitor.check_now()
        first_changed = monitor.snapshot()["last_changed_at"]
        monitor.check_now()
        second_changed = monitor.snapshot()["last_changed_at"]
        assert second_changed is not None
        assert second_changed != first_changed
        assert monitor.online is True

    def test_check_fn_exception_propagates(self):
        """check_now() does not itself swallow check_fn errors - callers
        (the background heartbeat loop, the manual recheck route) are
        responsible for their own error handling."""

        def boom():
            raise RuntimeError("network stack exploded")

        monitor = ConnectivityMonitor(check_fn=boom)
        with pytest.raises(RuntimeError):
            monitor.check_now()


class TestCheckIntervalSeconds:
    def test_default_is_300(self):
        original = os.environ.copy()
        os.environ.pop("INKYPI_CONNECTIVITY_CHECK_S", None)
        try:
            assert ConnectivityMonitor.check_interval_seconds() == 300
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("INKYPI_CONNECTIVITY_CHECK_S", "120")
        assert ConnectivityMonitor.check_interval_seconds() == 120

    def test_invalid_env_var_falls_back_to_300(self, monkeypatch):
        monkeypatch.setenv("INKYPI_CONNECTIVITY_CHECK_S", "not-a-number")
        assert ConnectivityMonitor.check_interval_seconds() == 300

    def test_minimum_is_30(self, monkeypatch):
        monkeypatch.setenv("INKYPI_CONNECTIVITY_CHECK_S", "5")
        assert ConnectivityMonitor.check_interval_seconds() == 30


class TestDefaultCheckFn:
    def test_uses_is_connected_when_no_check_fn_given(self, monkeypatch):
        """Without an injected check_fn, the monitor wires up
        utils.app_utils.is_connected - verified by monkeypatching that
        exact symbol and confirming it's what actually gets called."""
        import utils.app_utils as app_utils

        monkeypatch.setattr(app_utils, "is_connected", lambda: False)
        monitor = ConnectivityMonitor()
        assert monitor.check_now() is False
