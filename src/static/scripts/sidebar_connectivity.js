// Sidebar offline indicator + manual retry.
//
// The dot/label are server-rendered on first paint from
// RefreshTask.connectivity's cached state (inkypi.py's _inject_sidebar_system),
// so there's no flash of "online" before this script runs. From here it polls
// GET /api/health/connectivity (cheap - reflects whatever the background
// ConnectivityMonitor last found, forces no new probe) so the indicator
// tracks state changes live across the whole session, and wires the retry
// button to POST /api/health/connectivity/recheck, which forces a real probe
// on demand (the CSRF token is attached automatically by csrf.js's fetch
// wrapper, loaded earlier in base.html).
(function () {
  "use strict";

  const POLL_MS = 30_000;
  const STATUS_ENDPOINT = "/api/health/connectivity";
  const RECHECK_ENDPOINT = "/api/health/connectivity/recheck";

  let pollTimerId = null;

  function applyState(dot, label, retryBtn, data) {
    if (!data || typeof data.online !== "boolean") return;
    const online = data.online;
    dot.dataset.online = online ? "true" : "false";
    dot.classList.toggle("is-offline", !online);
    label.textContent = online ? "online" : "offline";
    retryBtn.hidden = online;
  }

  async function fetchStatus(signal) {
    const response = await fetch(STATUS_ENDPOINT, { cache: "no-store", signal });
    if (!response.ok) {
      throw new Error("connectivity status check failed: " + response.status);
    }
    return await response.json();
  }

  async function poll(dot, label, retryBtn) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const data = await fetchStatus(controller.signal);
      applyState(dot, label, retryBtn, data);
    } catch (e) {
      // Silent - the indicator just keeps showing its last known state
      // until the next successful poll.
    } finally {
      clearTimeout(timeout);
    }
  }

  async function retryNow(dot, label, retryBtn) {
    retryBtn.disabled = true;
    retryBtn.classList.add("is-spinning");
    try {
      const response = await fetch(RECHECK_ENDPOINT, { method: "POST" });
      if (!response.ok) {
        throw new Error("connectivity recheck failed: " + response.status);
      }
      const data = await response.json();
      applyState(dot, label, retryBtn, data);
    } catch (e) {
      // Leave the indicator as-is; the next scheduled poll (or another
      // manual click) will pick up the real state.
    } finally {
      retryBtn.disabled = false;
      retryBtn.classList.remove("is-spinning");
    }
  }

  function init() {
    const dot = document.getElementById("sidebarOnlineDot");
    const label = document.getElementById("sidebarOnlineLabel");
    const retryBtn = document.getElementById("sidebarConnectivityRetryBtn");
    if (!dot || !label || !retryBtn) return;

    retryBtn.addEventListener("click", () => retryNow(dot, label, retryBtn));

    // First poll happens immediately (not after the full POLL_MS) so a
    // stale server-rendered state from just before a connectivity change
    // corrects itself right away rather than waiting out the interval.
    poll(dot, label, retryBtn);
    pollTimerId = setInterval(() => poll(dot, label, retryBtn), POLL_MS);

    // Stop polling once the tab is hidden/closed rather than leaving an
    // orphaned timer - matches the SSE/other pollers' teardown convention
    // elsewhere in this app.
    window.addEventListener("pagehide", () => {
      if (pollTimerId) clearInterval(pollTimerId);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose for tests.
  window.InkyPiSidebarConnectivity = { init, applyState };
})();
