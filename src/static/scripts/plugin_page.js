(function () {
  if (!globalThis.InkyPiPluginPageShared || !globalThis.InkyPiPluginPageProgress) {
    throw new Error(
      "InkyPiPluginPage requires InkyPiPluginPageShared and InkyPiPluginPageProgress to load before plugin_page.js"
    );
  }

  const {
    buildProgressKey,
    ensureInlineValidationMessages,
    fadeSkeleton,
    initScheduleFormState,
    setCurrentDisplayRefresh,
    setHidden,
    setPluginSubtab,
    showInstanceFallback,
    syncModalOpenState,
    updateCombinedColorPreview,
    validateAddToPlaylistAction,
  } = globalThis.InkyPiPluginPageShared;
  const { createProgressController } = globalThis.InkyPiPluginPageProgress;

  function createPluginPage(config) {
    const ui = globalThis.InkyPiUI || {};
    const mobileQuery = globalThis.matchMedia ? globalThis.matchMedia("(max-width: 768px)") : { matches: false, addEventListener() {} };
    const uploadedFiles = (globalThis.uploadedFiles = globalThis.uploadedFiles || {});
    let actionInFlight = false;
    let workflowMode = "configure";
    const {
      saveLastProgressSnapshot,
      showLastProgress,
      syncLastProgressButton,
    } = createProgressController({ config, buildProgressKey, setHidden });

    function setLatestRefresh(value) {
      if (!value) return;
      config.lastRefresh = value;
      const instTimeEl = document.getElementById("instanceLastTime");
      if (instTimeEl) instTimeEl.textContent = new Date(value).toLocaleString();
    }

    function renderMetaBlock(metaDiv, metaContent, info) {
      if (!metaDiv || !metaContent) return;
      metaContent.innerHTML = "";
      if (!info?.plugin_meta) {
        setHidden(metaDiv, true);
        return;
      }
      const m = info.plugin_meta || {};
      const pid = info.plugin_id || "";
      const date = m.date ? new Date(m.date).toISOString().slice(0, 10) : "";
      const labels = {
        wpotd: "Wikipedia Picture of the Day",
        apod: "NASA APOD",
        newspaper: "Newspaper",
      };
      const rows = [];
      if (date || labels[pid]) {
        rows.push({
          strong: labels[pid] || pid,
          text: date,
        });
      }
      if (m.title) rows.push({ italic: m.title });
      if (m.caption) rows.push({ text: m.caption });
      if (m.explanation) rows.push({ text: m.explanation });
      rows.forEach((row) => {
        const block = document.createElement("div");
        block.className = "workflow-meta-row";
        if (row.strong) {
          const strong = document.createElement("strong");
          strong.textContent = row.strong;
          block.appendChild(strong);
          if (row.text) block.appendChild(document.createTextNode(` ${row.text}`));
        } else if (row.italic) {
          const em = document.createElement("em");
          em.textContent = row.italic;
          block.appendChild(em);
        } else if (row.text) {
          block.textContent = row.text;
        }
        metaContent.appendChild(block);
      });
      const link = m.page_url || m.description_url || "";
      if (link) {
        const linkRow = document.createElement("div");
        linkRow.className = "workflow-meta-row";
        const anchor = document.createElement("a");
        anchor.href = link;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.textContent = "Learn more";
        linkRow.appendChild(anchor);
        metaContent.appendChild(linkRow);
      }
      setHidden(metaDiv, metaContent.childNodes.length === 0);
    }

    function runPluginValidation(action) {
      try {
        if (typeof globalThis.validatePluginSettings === "function") {
          return !!globalThis.validatePluginSettings(action);
        }
      } catch (e) {
        console.warn("Plugin validation threw an error:", e);
      }
      return true;
    }

    function ensurePluginFormAvailable() {
      if (
        globalThis.PluginForm &&
        typeof globalThis.PluginForm.sendForm === "function"
      ) {
        return true;
      }
      showResponseModal(
        "failure",
        "Plugin form module failed to load. Refresh and try again."
      );
      return false;
    }

    // Live re-check of one or more env-var keys, right before an action that
    // needs one of them — the page-load `api_key.present` snapshot goes
    // stale the moment the key is added (or removed) from a different tab.
    // Returns true/false when the check ran, or null if it couldn't (so the
    // caller can fail open rather than block on an unreliable answer).
    async function checkApiKeyPresence(keys) {
      if (!keys || !keys.length || !config.urls?.api_keys_status) return true;
      try {
        const resp = await fetch(
          `${config.urls.api_keys_status}?keys=${encodeURIComponent(keys.join(","))}`
        );
        if (!resp.ok) return null;
        const result = await resp.json();
        const status = result?.data || {};
        return keys.some((key) => status[key] === true);
      } catch (e) {
        return null;
      }
    }

    async function handleAction(action, triggerButton) {
      // Checked first, before anything else (including the schedule-tab
      // switch below) so a click never sends the user off to fill in a
      // schedule form that a still-missing key would only block at the end.
      if (triggerButton?.dataset.apiKeyCheck) {
        const keys = triggerButton.dataset.apiKeyCheck.split(",").filter(Boolean);
        const present = await checkApiKeyPresence(keys);
        if (present === false) {
          const service = triggerButton.dataset.apiKeyService || "the required";
          showResponseModal(
            "failure",
            `Add your ${service} API key to continue.`
          );
          openModal("pluginApiKeyModal", triggerButton);
          return;
        }
        // present === true, or null (status check itself failed/unreachable)
        // — either way, don't block on a snapshot we can't trust; let the
        // real submit below be the source of truth.
      }

      if (action === "add_to_playlist") {
        // The header "Add to playlist" button does double duty: the first
        // click (from the Configure tab) just reveals the Schedule tab so
        // the user can fill in the destination/cadence fields; only a
        // second click, made once that panel is already visible, actually
        // validates and submits. This is what lets one button replace the
        // old pair (a header "jump to Schedule" button plus a separate
        // "Add to Playlist" button at the bottom of the schedule form).
        const schedulePanel = document.querySelector(
          '[data-plugin-subpanel="schedule"]'
        );
        if (!schedulePanel || schedulePanel.hidden) {
          showPluginSubtab("schedule", { focus: true, reportMissing: true });
          return;
        }
      }

      if (!validateAddToPlaylistAction(action)) return;

      // Validate settingsForm required fields. Use validateAllInputsDetailed so
      // the failure modal names the specific field (JTN-378) instead of a
      // generic "N fields need fixing" count.
      const settingsForm = document.getElementById("settingsForm");
      if (settingsForm && globalThis.FormValidator) {
        const result = globalThis.FormValidator.validateAllInputsDetailed(settingsForm);
        if (result.count > 0) {
          ensureInlineValidationMessages(result);
          showResponseModal(
            "failure",
            globalThis.FormValidator.buildValidationMessage(result)
          );
          globalThis.FormValidator.focusFirstInvalid(settingsForm);
          return;
        }
      }

      if (action === "add_to_playlist") {
        const scheduleForm = document.getElementById("scheduleForm");
        if (scheduleForm && globalThis.FormValidator) {
          const scheduleResult = globalThis.FormValidator.validateAllInputsDetailed(scheduleForm);
          if (scheduleResult.count > 0) {
            ensureInlineValidationMessages(scheduleResult);
            showResponseModal(
              "failure",
              globalThis.FormValidator.buildValidationMessage(scheduleResult)
            );
            globalThis.FormValidator.focusFirstInvalid(scheduleForm);
            return;
          }
        }
      }

      if (!runPluginValidation(action)) return;
      if (!ensurePluginFormAvailable()) return;

      actionInFlight = true;
      if (triggerButton) triggerButton.disabled = true;
      try {
        await globalThis.PluginForm.sendForm({
          action,
          urls: config.urls,
          uploadedFiles,
          onAfterSuccess: () => {
            if (action === "update_now") {
              // Preview never touches the display or saved instance state -
              // just swap in the freshly-rendered scratch image, not the
              // real current-display/instance-cache refresh the other
              // actions trigger.
              refreshPreviewNowImage();
              return;
            }
            setTimeout(() => {
              refreshPreviewsAfterSuccess();
            }, 250);
            closeModal("scheduleModal");
          },
        });
        saveLastProgressSnapshot(config.progressContext);
      } finally {
        actionInFlight = false;
        if (triggerButton) triggerButton.disabled = false;
      }
    }

    async function refreshPreviewImage() {
      const img = document.getElementById("previewImage");
      const skel = document.getElementById("previewSkeleton");
      if (img) {
        if (skel) { skel.style.display = ""; skel.classList.remove("is-hidden"); }
        img.src = `${config.previewUrl}?t=${Date.now()}`;
      }

      try {
        const res = await fetch(config.refreshInfoUrl);
        const info = await res.json();
        const ts = info?.refresh_time ? new Date(info.refresh_time) : null;
        const currTime = document.getElementById("currentDisplayTime");
        if (currTime) currTime.textContent = ts ? ts.toLocaleString() : "—";
        const pluginRefreshTime =
          info?.plugin_id === config.pluginId ? info.refresh_time : null;
        if (pluginRefreshTime) {
          setLatestRefresh(info.refresh_time);
        }
        const metaDiv = document.getElementById("pluginMeta");
        const metaContent = document.getElementById("pluginMetaContent");
        renderMetaBlock(metaDiv, metaContent, info);
        return pluginRefreshTime || null;
      } catch (e) { console.warn("Failed to refresh preview info:", e); }
      return null;
    }

    async function refreshPreviewsAfterSuccess() {
      let refreshedAt = null;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        refreshedAt = await refreshPreviewImage();
        if (refreshedAt) break;
        if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 350));
      }
      const resolvedRefresh = refreshedAt || new Date().toISOString();
      setLatestRefresh(resolvedRefresh);
      setCurrentDisplayRefresh(resolvedRefresh);
      await refreshInstancePreview({ force: true });
    }

    // Track the element that triggered the most-recently opened modal so focus
    // can be restored when the modal closes (WAI-ARIA best practice).
    let _lastModalTrigger = null;

    function openModal(modalId, triggerEl) {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      if (triggerEl) _lastModalTrigger = triggerEl;
      modal.hidden = false;
      modal.style.display = "flex";
      modal.classList.add("is-open");
      syncModalOpenState(ui);
      // JTN-463: move focus into the modal on open
      const focusable = modal.querySelector(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusable) setTimeout(() => focusable.focus(), 0);
    }

    function closeModal(modalId) {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      modal.hidden = true;
      modal.style.display = "none";
      modal.classList.remove("is-open");
      syncModalOpenState(ui);
      // Restore focus to the trigger element (WAI-ARIA best practice)
      if (_lastModalTrigger) {
        _lastModalTrigger.focus();
        _lastModalTrigger = null;
      }
    }

    function selectedFrame(element) {
      const previous = document.querySelector(
        "#frame-selection .image-option.selected"
      );
      if (previous) previous.classList.remove("selected");
      element.classList.add("selected");
      document.getElementById("selected-frame").value =
        element.getAttribute("data-face-name");
    }

    function showFileName() {
      const fileInput = document.getElementById("imageUpload");
      const fileNameDisplay = document.getElementById("fileName");
      const fileNameText = document.getElementById("fileNameText");
      const uploadButtonLabel = document.getElementById("uploadButtonLabel");
      const removeFileButton = document.getElementById("removeFileButton");
      const file = fileInput?.files?.[0];
      if (!fileNameDisplay || !fileNameText || !uploadButtonLabel || !removeFileButton) {
        return;
      }
      if (file) {
        fileNameText.textContent = file.name;
        setHidden(fileNameDisplay, false);
        setHidden(uploadButtonLabel, true);
      } else {
        setHidden(fileNameDisplay, true);
        setHidden(uploadButtonLabel, false);
      }
    }

    function removeFile() {
      const fileInput = document.getElementById("imageUpload");
      const fileNameDisplay = document.getElementById("fileName");
      const uploadButtonLabel = document.getElementById("uploadButtonLabel");
      if (fileInput) fileInput.value = "";
      setHidden(fileNameDisplay, true);
      setHidden(uploadButtonLabel, false);
      const hidden = document.getElementById("hidden-file-name");
      if (hidden) hidden.remove();
    }

    function populateStyleSettings() {
      if (!config.styleSettings || !config.loadPluginSettings) return;
      const settings = config.pluginSettings || {};
      Object.entries(settings).forEach(([key, value]) => {
        if (key === "selectedFrame") {
          const frameOption = document.querySelector(
            `#frame-selection .image-option[data-face-name="${CSS.escape(String(value))}"]`
          );
          if (frameOption) selectedFrame(frameOption);
          return;
        }
        if (key === "backgroundOption") {
          const radio = document.querySelector(
            `[name="backgroundOption"][value="${CSS.escape(String(value))}"]`
          );
          if (radio) radio.checked = true;
          return;
        }
        const input = document.getElementById(key);
        if (!input || value == null || value === "") return;
        if (input.type === "checkbox") {
          // value is always a string here (settings are serialized as
          // "true"/"false"), and !!"false" is true in JS - a bare truthiness
          // check force-checks every checkbox whose saved value is "false".
          input.checked = value === true || value === "true";
        } else {
          input.value = value;
        }
      });
    }

    async function resolveAvailableImageUrl(url) {
      if (!url) return null;
      const probeUrl = `${url}${url.includes("?") ? "&" : "?"}probe=${Date.now()}`;
      try {
        const response = await fetch(probeUrl, {
          method: "HEAD",
          cache: "no-store",
        });
        if (response.ok) return url;
      } catch (error) { console.warn("Failed to probe image URL:", probeUrl, error); }
      return null;
    }

    async function refreshInstancePreview({ force = false } = {}) {
      const instImgEl = document.getElementById("instancePreviewImage");
      if (!instImgEl) return;
      const skeleton = instImgEl.previousElementSibling;
      const fallback = document.getElementById("instancePreviewFallback");
      setHidden(skeleton, false);
      setHidden(fallback, true);
      setHidden(instImgEl, false);

      // Avoid probing image endpoints before the backend has ever produced
      // output for this plugin or instance. That state is expected on a fresh
      // page and should render the empty fallback without console noise.
      if (!config.lastRefresh && !force) {
        setHidden(instImgEl, true);
        setHidden(skeleton, true);
        setHidden(fallback, false);
        return;
      }

      const primaryUrl = await resolveAvailableImageUrl(config.instanceImageUrl);
      const fallbackUrl =
        primaryUrl === config.latestPluginImageUrl
          ? primaryUrl
          : await resolveAvailableImageUrl(config.latestPluginImageUrl);
      const imageUrl = primaryUrl || fallbackUrl;
      if (!imageUrl) {
        setHidden(instImgEl, true);
        setHidden(skeleton, true);
        setHidden(fallback, false);
        return;
      }

      const onPrimaryError = function () {
        const canFallback =
          primaryUrl && imageUrl === primaryUrl && fallbackUrl && fallbackUrl !== primaryUrl;
        if (canFallback) {
          this.src = `${fallbackUrl}?t=${Date.now()}`;
          this.onerror = onFallbackError;
          return;
        }
        showInstanceFallback(this, skeleton, fallback);
      };
      const onFallbackError = function () {
        showInstanceFallback(this, skeleton, fallback);
      };

      instImgEl.src = `${imageUrl}?t=${Date.now()}`;
      instImgEl.onload = () => setHidden(skeleton, true);
      instImgEl.onerror = onPrimaryError;
    }

    // Swaps the instance-preview image for the scratch render POST /preview_now
    // just produced. Deliberately does not touch config.lastRefresh/instanceLastTime
    // or the current-display card - a preview click isn't a real refresh of
    // anything saved, just a look at how the on-page (possibly unsaved)
    // settings would render.
    function refreshPreviewNowImage() {
      const instImgEl = document.getElementById("instancePreviewImage");
      if (!instImgEl || !config.previewNowImageUrl) return;
      const skeleton = instImgEl.previousElementSibling;
      const fallback = document.getElementById("instancePreviewFallback");
      setHidden(skeleton, false);
      setHidden(fallback, true);
      setHidden(instImgEl, false);
      instImgEl.src = `${config.previewNowImageUrl}?t=${Date.now()}`;
      instImgEl.onload = () => setHidden(skeleton, true);
      instImgEl.onerror = () => showInstanceFallback(instImgEl, skeleton, fallback);
    }

    async function displayInstanceNow() {
      try {
        const resp = await fetch(config.displayInstanceUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(config.displayInstancePayload),
        });
        const result = await resp.json();
        if (!resp.ok) {
          showResponseModal("failure", `Error! ${result.error}`);
        } else {
          showResponseModal("success", `Success! ${result.message}`);
          setTimeout(() => {
            refreshPreviewsAfterSuccess();
          }, 400);
        }
      } catch (e) {
        showResponseModal("failure", "Failed to display instance");
      }
    }

    function initStatusBar() {
      const instTimeEl = document.getElementById("instanceLastTime");
      if (instTimeEl) {
        instTimeEl.textContent = config.lastRefresh
          ? new Date(config.lastRefresh).toLocaleString()
          : "—";
      }
      refreshPreviewImage();
      refreshInstancePreview();
    }

    function initPreviewInteractions() {
      const previewImg = document.getElementById("previewImage");
      const instanceImg = document.getElementById("instancePreviewImage");
      const container = document.getElementById("currentPreviewContainer");
      if (previewImg && container) {
        const previewSkel = document.getElementById("previewSkeleton");
        previewImg.addEventListener("load", () => fadeSkeleton(previewSkel));
        previewImg.addEventListener("error", () => fadeSkeleton(previewSkel));
        const nativeWidth = previewImg.dataset.nativeWidth || config.resolution[0];
        const nativeHeight = previewImg.dataset.nativeHeight || config.resolution[1];
        previewImg.addEventListener("click", () => {
          if (previewImg.src && globalThis.Lightbox) {
            globalThis.Lightbox.open(previewImg.src, previewImg.alt);
          }
        });
        if (!container.closest(".status-card.compact")) {
          previewImg.addEventListener("dblclick", (event) => {
            event.preventDefault();
            container.classList.toggle("native");
            if (container.classList.contains("native")) {
              previewImg.style.width = `${nativeWidth}px`;
              previewImg.style.height = `${nativeHeight}px`;
            } else {
              previewImg.style.width = "";
              previewImg.style.height = "";
            }
          });
        }
      }
      if (instanceImg) {
        const skeleton = instanceImg.previousElementSibling;
        instanceImg.addEventListener("load", () => fadeSkeleton(skeleton));
        instanceImg.addEventListener("click", () => {
          if (
            instanceImg.src &&
            !instanceImg.hidden &&
            globalThis.Lightbox
          ) {
            globalThis.Lightbox.open(instanceImg.src, instanceImg.alt);
          }
        });
      }
      document.addEventListener("click", (event) => {
        const img = event.target.closest("img.lightboxable");
        if (!img || !globalThis.Lightbox || !img.src) return;
        event.preventDefault();
        globalThis.Lightbox.open(img.src, img.alt || "Preview");
      });
      const toggle = document.getElementById("toggleDeviceFrame");
      const overlay = document.getElementById("deviceFrameOverlay");
      if (toggle && overlay) {
        overlay.style.backgroundImage = `url('${config.deviceFrameUrl}')`;
        toggle.addEventListener("change", function () {
          const parent = document.getElementById("currentPreviewContainer");
          if (!parent) return;
          parent.classList.toggle("show-frame", this.checked);
        });
      }
    }

    function collapseApiIndicator(apiIndicator) {
      apiIndicator.classList.remove("auto-collapse");
      apiIndicator.classList.add("collapsed");
    }

    function initApiIndicator() {
      const apiIndicator = document.getElementById("apiKeyIndicator");
      if (!apiIndicator) return;
      // When the indicator lives in the plugin title-stack meta row it is
      // already styled as a compact chip — skip the legacy auto-collapse
      // animation that assumed a full-width header badge (JTN-design refresh).
      if (apiIndicator.closest(".plugin-mode-row")) {
        apiIndicator.classList.remove("auto-collapse", "collapsed");
        return;
      }
      setTimeout(() => {
        apiIndicator.classList.add("auto-collapse");
        setTimeout(() => collapseApiIndicator(apiIndicator), 3000);
      }, 100);
    }

    // After a successful inline key save, patch the affected UI in place —
    // no reload, so nothing typed into settingsForm/scheduleForm is lost.
    // Deliberately conservative: flips the chip(s)/copy that are cheap and
    // unambiguous to update, and clears the buttons' stale-check markers so
    // the next click's live check (checkApiKeyPresence) simply confirms
    // "present" instead of tripping the modal again.
    function markApiKeyConfigured(savedKeyNames) {
      const headerChipWrap = document.querySelector("[data-api-key-header-chip]");
      const headerChip = headerChipWrap?.querySelector(".status-chip");
      if (headerChip) {
        headerChip.classList.remove("warning");
        headerChip.classList.add("success");
      }

      const card = document.querySelector("[data-api-key-card]");
      if (card) {
        const copy = card.querySelector("[data-api-key-copy]");
        const serviceNames = (config.apiKeyServices || [])
          .filter((svc) => savedKeyNames.includes(svc.env_var))
          .map((svc) => svc.name);
        if (copy) {
          copy.textContent = serviceNames.length
            ? `${serviceNames.join(", ")} configured and ready for fresh previews.`
            : "Configured and ready for fresh previews.";
        }
        const chipsWrap = card.querySelector("[data-api-key-chips]");
        const requiredChip = Array.from(
          chipsWrap?.querySelectorAll(".status-chip") || []
        ).find((el) => el.textContent.trim() === "Required");
        if (requiredChip) {
          if (serviceNames.length) {
            requiredChip.textContent = serviceNames.join(", ");
          } else {
            requiredChip.textContent = "Configured";
          }
          requiredChip.classList.remove("warning");
          requiredChip.classList.add("success");
        }
      }

      document.querySelectorAll("[data-api-key-check]").forEach((btn) => {
        btn.removeAttribute("data-api-key-check");
        btn.removeAttribute("data-api-key-service");
      });
    }

    async function saveApiKeyFromModal() {
      const modal = document.getElementById("pluginApiKeyModal");
      const statusEl = document.getElementById("pluginApiKeyModalStatus");
      const saveBtn = document.getElementById("pluginApiKeySaveBtn");
      if (!modal) return;
      const inputs = Array.from(
        modal.querySelectorAll("[data-api-key-modal-input]")
      ).filter((input) => input.value.trim());
      if (!inputs.length) {
        if (statusEl) statusEl.textContent = "Enter a key to save.";
        return;
      }
      const formData = new FormData();
      inputs.forEach((input) => formData.append(input.dataset.keyName, input.value.trim()));

      if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving…"; }
      if (statusEl) statusEl.textContent = "";
      try {
        const resp = await fetch(config.urls.save_api_keys, {
          method: "POST",
          body: formData,
        });
        const result = await resp.json();
        if (!resp.ok || !result?.success) {
          throw new Error(result?.error || "Failed to save key.");
        }
        const savedKeyNames = inputs.map((input) => input.dataset.keyName);
        inputs.forEach((input) => { input.value = ""; });
        markApiKeyConfigured(savedKeyNames);
        closeModal("pluginApiKeyModal");
        showResponseModal("success", "API key saved. You're all set to continue.");
      } catch (e) {
        if (statusEl) statusEl.textContent = e?.message || "Failed to save key. Please try again.";
      } finally {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Save key"; }
      }
    }

    function initApiKeyModal() {
      const modal = document.getElementById("pluginApiKeyModal");
      if (!modal) return;
      document.getElementById("pluginApiKeySaveBtn")?.addEventListener(
        "click",
        saveApiKeyFromModal
      );
    }

    function bindModalClose() {
      globalThis.addEventListener("click", (event) => {
        if (actionInFlight) return;
        const modal = document.getElementById("scheduleModal");
        if (event.target === modal) {
          closeModal("scheduleModal");
        }
      });
      // JTN-461: close #scheduleModal when Escape is pressed
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const modal = document.getElementById("scheduleModal");
        if (!modal || modal.hidden) return;
        event.preventDefault();
        closeModal("scheduleModal");
      });
    }

    // JTN design refresh: the Configure/Preview mode bar was removed in favor
    // of always showing both panels side-by-side on desktop and stacked on
    // mobile. setWorkflowMode is kept as a no-op to preserve the existing
    // public surface; callers no longer change the visible panel.
    function setWorkflowMode(mode) {
      workflowMode = mode;
      document.documentElement.setAttribute("data-mobile-workflow-mode", mode);
      document.querySelectorAll("[data-workflow-panel]").forEach((panel) => {
        panel.classList.add("active");
        panel.setAttribute("aria-hidden", "false");
        panel.removeAttribute("inert");
      });
    }

    function bindWorkflowMode() {
      // Both panels are always visible; no buttons to bind.
      setWorkflowMode("configure");
    }

    // Extracted to avoid nesting this handler 5 levels deep inside
    // IIFE → createPluginPage → bindPluginSubtabs → forEach → addEventListener
    // (SonarCloud javascript:S2004 caps function nesting at 4).
    function onSubtabButtonClick(event) {
      setPluginSubtab(event.currentTarget.dataset.pluginSubtab);
    }

    function onSubtabButtonKeydown(event) {
      if (
        event.key !== "ArrowRight" &&
        event.key !== "ArrowLeft" &&
        event.key !== "Home" &&
        event.key !== "End"
      ) {
        return;
      }
      const buttons = Array.from(document.querySelectorAll("[data-plugin-subtab]"));
      const currentIndex = buttons.indexOf(event.currentTarget);
      if (currentIndex < 0) return;
      event.preventDefault();
      let nextIndex = currentIndex;
      if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % buttons.length;
      if (event.key === "ArrowLeft")
        nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = buttons.length - 1;
      const nextButton = buttons[nextIndex];
      setPluginSubtab(nextButton.dataset.pluginSubtab);
      nextButton.focus();
    }

    function bindPluginSubtabs() {
      const buttons = document.querySelectorAll("[data-plugin-subtab]");
      if (!buttons.length) return;
      buttons.forEach((btn) => btn.addEventListener("click", onSubtabButtonClick));
      buttons.forEach((btn) =>
        btn.addEventListener("keydown", onSubtabButtonKeydown)
      );
      setPluginSubtab("configure");
    }

    function showPluginSubtab(id, { focus = false, reportMissing = false } = {}) {
      const panel = document.querySelector(`[data-plugin-subpanel="${id}"]`);
      if (!panel) {
        if (reportMissing) {
          showResponseModal(
            "failure",
            "Unable to open scheduling controls. Please refresh the page and try again."
          );
        }
        return false;
      }
      setPluginSubtab(id);
      try {
        const scrollTarget = document.getElementById("scheduleForm") || panel;
        scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch {
        // scrollIntoView can throw if the panel was detached between the
        // lookup above and this call; the subtab switch itself already
        // succeeded, so silently absorb the scroll failure.
      }
      if (focus) {
        const focusTarget =
          panel.querySelector("[data-subtab-focus-target]") ||
          panel.querySelector(
            'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])'
          );
        if (focusTarget) setTimeout(() => focusTarget.focus(), 0);
      }
      return true;
    }

    function bindControls() {
      // JTN-648: route Enter-key implicit submit through the app-level
      // validator so empty required fields surface the same labelled toast
      // ("<Field> is required") as the Update Preview click path. The form
      // carries `novalidate` so native HTML5 bubbles never appear.
      document.getElementById("settingsForm")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const settingsForm = event.currentTarget;
        if (settingsForm && globalThis.FormValidator) {
          const result = globalThis.FormValidator.validateAllInputsDetailed(settingsForm);
          if (result.count > 0) {
            ensureInlineValidationMessages(result);
            showResponseModal(
              "failure",
              globalThis.FormValidator.buildValidationMessage(result)
            );
            globalThis.FormValidator.focusFirstInvalid(settingsForm);
          }
        }
      });
      document.getElementById("scheduleForm")?.addEventListener("submit", (event) => {
        event.preventDefault();
      });
      document.querySelectorAll("[data-plugin-action]").forEach((button) => {
        button.addEventListener("click", () => handleAction(button.dataset.pluginAction, button));
      });
      // Extracted handler — nesting forEach → addEventListener → arrow
      // tripped javascript:S2004's 4-level limit at L809.
      function onSubtabTargetClick(event) {
        const button = event.currentTarget;
        if (button.disabled || button.getAttribute("aria-disabled") === "true") return;
        const ok = showPluginSubtab(button.dataset.pluginSubtabTarget, {
          focus: true,
          reportMissing: true,
        });
        if (!ok) event.preventDefault();
      }
      document.querySelectorAll("[data-plugin-subtab-target]").forEach((button) => {
        button.addEventListener("click", onSubtabTargetClick);
      });
      document.addEventListener("click", (event) => {
        const opener = event.target.closest("[data-open-modal]");
        if (opener) openModal(opener.dataset.openModal, opener);
      });
      // JTN-633/[header Add-to-playlist unification]: the DRAFT-state "Add
      // to Playlist" button carries `data-plugin-action="add_to_playlist"`
      // (bound above, not `data-plugin-subtab-target`) so a single click
      // handler — handleAction — owns both revealing the Schedule tab on
      // the first click and validating/submitting on a second click made
      // once that tab is already visible. `data-plugin-draft` is now just a
      // styling/selector hook, not part of the click-wiring contract.
      document.querySelectorAll("[data-close-modal]").forEach((button) => {
        button.addEventListener("click", () => closeModal(button.dataset.closeModal));
      });
      // Collapsible toggle is bound via delegation in ui_helpers.js so every
      // `[data-collapsible-toggle]` button updates aria-expanded consistently.
      document.querySelectorAll("[data-frame-option]").forEach((option) => {
        option.addEventListener("click", () => selectedFrame(option));
        option.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectedFrame(option);
          }
        });
      });
      document.getElementById("showLastProgressBtn")?.addEventListener("click", showLastProgress);
      syncLastProgressButton();
      // Persistent progress card: render whatever the last snapshot is
      // (or the empty-state) on first load so the aside card always has content.
      // showLastProgress reads cached snapshot JSON from localStorage and can
      // throw if the stored value was corrupted (bad JSON, partial write).
      // Swallow — a missing snapshot just means no last-progress card.
      try { showLastProgress(); } catch { /* ignore bad snapshot */ }
      document.getElementById("displayInstanceBtn")?.addEventListener("click", displayInstanceNow);
      document.querySelector("[data-background-upload]")?.addEventListener("change", showFileName);
      document.getElementById("removeFileButton")?.addEventListener("click", removeFile);
      document.querySelectorAll("[data-lightbox-close]").forEach((button) => {
        button.addEventListener("click", () => globalThis.Lightbox?.close());
      });
    }

    function initColorPreviews() {
      document.querySelectorAll(".color-picker").forEach((picker) => {
        const preview = document.querySelector(
          `[data-color-preview="${picker.id}"]`
        );
        if (!preview) return;
        preview.style.setProperty("--preview-color", picker.value);
        picker.addEventListener("input", () => {
          preview.style.setProperty("--preview-color", picker.value);
        });
      });

      // Combined bg+text preview for style section
      const bgPicker = document.getElementById("backgroundColor");
      const textPicker = document.getElementById("textColor");
      if (bgPicker && textPicker) {
        let combined = document.getElementById("colorCombinedPreview");
        if (!combined) {
          combined = document.createElement("span");
          combined.id = "colorCombinedPreview";
          combined.className = "color-combined-preview";
          combined.textContent = "Aa";
          const textGroup = textPicker.closest(".form-group");
          if (textGroup) textGroup.appendChild(combined);
        }
        updateCombinedColorPreview(combined, bgPicker, textPicker);
        bgPicker.addEventListener("input", () => updateCombinedColorPreview(combined, bgPicker, textPicker));
        textPicker.addEventListener("input", () => updateCombinedColorPreview(combined, bgPicker, textPicker));
      }
    }

    function init() {
      populateStyleSettings();
      bindControls();
      initScheduleFormState();
      const scheduleForm = document.getElementById("scheduleForm");
      if (scheduleForm && globalThis.FormValidator?.initFormValidation) {
        globalThis.FormValidator.initFormValidation(scheduleForm);
      }
      bindWorkflowMode();
      bindPluginSubtabs();
      initStatusBar();
      initPreviewInteractions();
      initApiIndicator();
      initApiKeyModal();
      initColorPreviews();
      bindModalClose();
      if (mobileQuery && typeof mobileQuery.addEventListener === "function") {
        mobileQuery.addEventListener("change", () => setWorkflowMode(workflowMode));
      }
    }

    Object.assign(globalThis, {
      closeModal,
      displayInstanceNow,
      handleAction,
      openModal,
      refreshInstancePreview,
      refreshPreviewImage,
      removeFile,
      selectedFrame,
      showFileName,
      showLastProgress,
      toggleCollapsible: ui.toggleCollapsible,
    });

    return { init };
  }

  globalThis.InkyPiPluginPage = { create: createPluginPage };
})();
