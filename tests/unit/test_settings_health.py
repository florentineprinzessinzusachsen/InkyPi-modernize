# pyright: reportMissingImports=false
"""Tests for settings health and progress SSE endpoints (_health.py)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from blueprints.settings import _health


class TestHealthPlugins:
    def test_filters_stale_entries(self, client, monkeypatch):
        """Entries with last_seen older than the window are filtered out."""
        monkeypatch.setenv("INKYPI_HEALTH_WINDOW_MIN", "1440")

        stale_time = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        snapshot = {"old_plugin": {"last_seen": stale_time, "status": "ok"}}

        rt = MagicMock()
        rt.get_health_snapshot.return_value = snapshot

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "old_plugin" not in data["items"]

    def test_keeps_recent_entries(self, client, monkeypatch):
        """Entries with recent last_seen are preserved."""
        monkeypatch.setenv("INKYPI_HEALTH_WINDOW_MIN", "1440")
        recent_time = datetime.now(UTC).isoformat()
        snapshot = {"fresh_plugin": {"last_seen": recent_time, "status": "ok"}}

        rt = MagicMock()
        rt.get_health_snapshot.return_value = snapshot

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        data = resp.get_json()
        assert "fresh_plugin" in data["items"]

    def test_keeps_entries_without_last_seen(self, client, monkeypatch):
        """Entries missing last_seen key are preserved."""
        snapshot = {"no_ts_plugin": {"status": "ok"}}

        rt = MagicMock()
        rt.get_health_snapshot.return_value = snapshot

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        data = resp.get_json()
        assert "no_ts_plugin" in data["items"]

    def test_invalid_datetime_preserved(self, client, monkeypatch):
        """Entries with unparseable last_seen are preserved (except path)."""
        snapshot = {"bad_ts": {"last_seen": "not-a-date", "status": "ok"}}

        rt = MagicMock()
        rt.get_health_snapshot.return_value = snapshot

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        data = resp.get_json()
        assert "bad_ts" in data["items"]

    def test_window_env_non_numeric(self, client, monkeypatch):
        """Non-numeric INKYPI_HEALTH_WINDOW_MIN falls back to 1440."""
        monkeypatch.setenv("INKYPI_HEALTH_WINDOW_MIN", "abc")

        recent_time = datetime.now(UTC).isoformat()
        snapshot = {"plugin": {"last_seen": recent_time}}

        rt = MagicMock()
        rt.get_health_snapshot.return_value = snapshot

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        data = resp.get_json()
        assert data["success"] is True
        assert "plugin" in data["items"]

    def test_no_snapshot_method(self, client, monkeypatch):
        """RefreshTask without get_health_snapshot returns empty dict."""
        rt = MagicMock(spec=[])  # no methods at all

        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/plugins")
        data = resp.get_json()
        assert data["success"] is True
        assert data["items"] == {}

    def test_exception_returns_500(self, client, monkeypatch):
        """Outer exception triggers json_internal_error."""
        # Remove REFRESH_TASK entirely to cause KeyError
        with client.application.app_context():
            client.application.config.pop("REFRESH_TASK", None)

        resp = client.get("/api/health/plugins")
        assert resp.status_code == 500


class TestHealthSystem:
    def test_with_psutil(self, client):
        """Returns numeric system metrics when psutil is available."""
        resp = client.get("/api/health/system")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # psutil is installed in test env
        assert isinstance(data["cpu_percent"], int | float)
        assert isinstance(data["memory_percent"], int | float)
        assert isinstance(data["disk_percent"], int | float)
        assert isinstance(data["disk_free_gb"], int | float)
        assert isinstance(data["disk_total_gb"], int | float)
        assert isinstance(data["uptime_seconds"], int)

    def test_disk_free_gb_is_plausible(self, client):
        """disk_free_gb must be non-negative and less than or equal to disk_total_gb."""
        resp = client.get("/api/health/system")
        data = resp.get_json()
        assert data["disk_free_gb"] >= 0
        assert data["disk_total_gb"] > 0
        assert data["disk_free_gb"] <= data["disk_total_gb"]

    def test_psutil_unavailable(self, client, monkeypatch):
        """All metrics are None when psutil import fails."""
        import builtins

        real_import = builtins.__import__

        def _block_psutil(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("blocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_psutil)

        resp = client.get("/api/health/system")
        data = resp.get_json()
        assert data["success"] is True
        assert data["cpu_percent"] is None
        assert data["memory_percent"] is None
        assert data["disk_percent"] is None
        assert data["disk_free_gb"] is None
        assert data["disk_total_gb"] is None
        assert data["uptime_seconds"] is None


class TestHealthConnectivity:
    """Tests for GET /api/health/connectivity and POST .../recheck (the
    sidebar's offline indicator + manual retry - see
    refresh_task/connectivity.py)."""

    def test_default_online_snapshot(self, client):
        """With no override, the flask_app fixture's real RefreshTask has a
        fresh ConnectivityMonitor: online by default, never probed yet."""
        resp = client.get("/api/health/connectivity")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["online"] is True
        assert data["last_checked_at"] is None
        assert data["last_changed_at"] is None

    def test_reflects_offline_state(self, client):
        from refresh_task.connectivity import ConnectivityMonitor

        monitor = ConnectivityMonitor(check_fn=lambda: False)
        monitor.check_now()

        rt = MagicMock()
        rt.connectivity = monitor
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/connectivity")
        data = resp.get_json()
        assert data["online"] is False
        assert data["last_checked_at"] is not None
        assert data["last_changed_at"] is not None

    def test_get_missing_monitor_defaults_to_online(self, client):
        rt = MagicMock()
        rt.connectivity = None
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.get("/api/health/connectivity")
        data = resp.get_json()
        assert data["online"] is True

    def test_recheck_forces_new_probe(self, client):
        from refresh_task.connectivity import ConnectivityMonitor

        calls = []

        def check_fn():
            calls.append(1)
            return True

        monitor = ConnectivityMonitor(check_fn=check_fn)
        rt = MagicMock()
        rt.connectivity = monitor
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.post("/api/health/connectivity/recheck")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["online"] is True
        assert len(calls) == 1

    def test_recheck_wakes_scheduler_on_offline_to_online_transition(self, client):
        from refresh_task.connectivity import ConnectivityMonitor

        monitor = ConnectivityMonitor(check_fn=lambda: False)
        monitor.check_now()  # starts offline
        monitor._check_fn = lambda: True  # the recheck call will find it back

        rt = MagicMock()
        rt.connectivity = monitor
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.post("/api/health/connectivity/recheck")
        assert resp.get_json()["online"] is True
        # The whole point of forcing this: the next automatic refresh cycle
        # shouldn't have to wait out the rest of plugin_cycle_interval_seconds.
        rt.condition.notify_all.assert_called_once()

    def test_recheck_does_not_wake_scheduler_when_already_online(self, client):
        from refresh_task.connectivity import ConnectivityMonitor

        monitor = ConnectivityMonitor(check_fn=lambda: True)
        rt = MagicMock()
        rt.connectivity = monitor
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.post("/api/health/connectivity/recheck")
        assert resp.get_json()["online"] is True
        rt.condition.notify_all.assert_not_called()

    def test_recheck_still_offline_does_not_wake_scheduler(self, client):
        from refresh_task.connectivity import ConnectivityMonitor

        monitor = ConnectivityMonitor(check_fn=lambda: False)
        rt = MagicMock()
        rt.connectivity = monitor
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.post("/api/health/connectivity/recheck")
        assert resp.get_json()["online"] is False
        rt.condition.notify_all.assert_not_called()

    def test_recheck_missing_monitor_returns_501(self, client):
        rt = MagicMock()
        rt.connectivity = None
        with client.application.app_context():
            client.application.config["REFRESH_TASK"] = rt

        resp = client.post("/api/health/connectivity/recheck")
        assert resp.status_code == 501
        assert resp.get_json()["success"] is False


class TestProgressStream:
    def test_disabled(self, client, monkeypatch):
        """SSE endpoint returns 404 when disabled."""
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "false")

        resp = client.get("/api/progress/stream")
        assert resp.status_code == 404

    def test_enabled_mimetype(self, client, monkeypatch):
        """SSE endpoint returns text/event-stream content type."""
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "true")

        resp = client.get("/api/progress/stream")
        try:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.content_type
        finally:
            resp.close()

    def test_last_seq_non_numeric(self, client, monkeypatch):
        """Non-numeric last_seq defaults to 0 without error."""
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "true")

        resp = client.get("/api/progress/stream?last_seq=abc")
        try:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.content_type
        finally:
            resp.close()

    def test_connection_cap_returns_503(self, client, monkeypatch):
        """SSE endpoint refuses excess subscribers instead of tying up workers."""
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "true")
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_MAX_CONNECTIONS", "0")

        resp = client.get("/api/progress/stream")

        assert resp.status_code == 503
        assert resp.get_data(as_text=True) == "Too many progress SSE connections"

    def test_enabled_helper_accepts_truthy_values(self, monkeypatch):
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "yes")

        assert _health._progress_stream_enabled() is True

    def test_enabled_helper_rejects_disabled_values(self, monkeypatch):
        monkeypatch.setenv("INKYPI_PROGRESS_SSE_ENABLED", "off")

        assert _health._progress_stream_enabled() is False

    def test_iter_progress_events_backfills_and_follows_new_events(self):
        bus = MagicMock()
        bus.recent.return_value = [
            {"seq": 1, "state": "old"},
            {"seq": 3, "state": "ready", "message": "done"},
        ]
        bus.wait_for.return_value = [{"seq": 4, "state": "next"}]

        stream = _health._iter_progress_events(bus, last_seq=2)

        backfill = next(stream)
        assert backfill.startswith("event: ready\n")
        assert '"seq":3' in backfill
        assert '"seq":1' not in backfill
        assert next(stream).startswith("event: next\n")
        bus.wait_for.assert_called_once_with(2, timeout_s=15.0)

    def test_iter_progress_events_emits_keepalive_when_idle(self):
        bus = MagicMock()
        bus.recent.return_value = []
        bus.wait_for.return_value = []

        stream = _health._iter_progress_events(bus, last_seq=0)

        assert next(stream) == ": keep-alive\n\n"
