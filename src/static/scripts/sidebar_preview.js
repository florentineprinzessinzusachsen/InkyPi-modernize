// Wires the sidebar's "now playing" thumbnail up to the shared Lightbox
// module (lightbox.js) so clicking/Enter/Space opens the current display
// image full-size, the same zoom used by the dashboard's big preview and
// the playlist row thumbnails. Global (loaded once in base.html) because
// the sidebar itself renders on every page, not just the dashboard.
(function () {
  "use strict";

  function init() {
    if (!window.Lightbox) return;
    window.Lightbox.bind("[data-sidebar-preview]", {
      getUrl: (el) => el.querySelector("img")?.src || null,
      getAlt: (el) => el.querySelector("img")?.alt || "Current display preview",
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
