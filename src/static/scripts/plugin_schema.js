(function () {
  function normalizeValue(value) {
    if (value == null) return "";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  function parseJson(value, fallback) {
    try {
      return JSON.parse(value);
    } catch (error) {
      // Intentionally ignored — invalid JSON returns the provided fallback value
      return fallback;
    }
  }

  function getFieldValue(root, fieldName) {
    const radios = root.querySelectorAll(`input[type="radio"][name="${fieldName}"]`);
    if (radios.length) {
      const checked = Array.from(radios).find((input) => input.checked);
      return checked ? normalizeValue(checked.value) : "";
    }

    const checkboxes = root.querySelectorAll(`input[type="checkbox"][name="${fieldName}"]`);
    if (checkboxes.length) {
      const primary = Array.from(checkboxes).find((input) => input.dataset.checkboxPrimary === "true") || checkboxes[0];
      return primary.checked ? normalizeValue(primary.value || "true") : "false";
    }

    const field = root.querySelector(`[name="${fieldName}"]`);
    return field ? normalizeValue(field.value) : "";
  }

  function setHiddenState(node, hidden) {
    node.hidden = hidden;
    node.classList.toggle("hidden", hidden);
    node.querySelectorAll("input, select, textarea, button").forEach((control) => {
      if (control.type === "hidden") return;
      if (hidden) {
        control.dataset.wasDisabled = control.disabled ? "true" : "false";
        control.disabled = true;
      } else if (control.dataset.wasDisabled !== "true") {
        control.disabled = false;
      }
    });
  }

  function matchesVisibility(node, root) {
    const fieldName = node.dataset.visibleIfField;
    if (!fieldName) return true;
    const operator = node.dataset.visibleIfOperator || "equals";
    const expected = node.dataset.visibleIfEquals || "";
    const values = parseJson(node.dataset.visibleIfValues || "[]", []);
    const actual = getFieldValue(root, fieldName);

    if (operator === "equals") return actual === expected;
    if (operator === "not_equals") return actual !== expected;
    if (operator === "not_empty") return actual.trim() !== "";
    if (operator === "in") return values.map(normalizeValue).includes(actual);
    return true;
  }

  function applyVisibility(root) {
    root.querySelectorAll("[data-visible-if-field]").forEach((node) => {
      setHiddenState(node, !matchesVisibility(node, root));
    });
  }

  function replaceOptions(select, options, currentValue) {
    const previous = currentValue || select.dataset.currentValue || select.value;
    while (select.firstChild) select.removeChild(select.firstChild);
    options.forEach((opt) => {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      if (normalizeValue(previous) === normalizeValue(opt.value)) {
        option.selected = true;
      }
      select.appendChild(option);
    });
    if (select.selectedIndex === -1 && select.options.length) {
      select.options[0].selected = true;
    }
  }

  function applyDependentOptions(root) {
    root.querySelectorAll("select[data-options-source-field]").forEach((select) => {
      const optionMap = parseJson(select.dataset.optionsMap || "{}", {});
      const fallbackOptions = parseJson(select.dataset.fallbackOptions || "[]", []);
      const sourceValue = getFieldValue(root, select.dataset.optionsSourceField);
      const options = optionMap[sourceValue] || fallbackOptions;
      replaceOptions(select, options, select.dataset.currentValue || select.value);
    });
  }

  function syncCheckboxState(root) {
    root.querySelectorAll("input[type='checkbox'].toggle-checkbox").forEach((checkbox) => {
      checkbox.dataset.checkboxPrimary = "true";
      checkbox.value = checkbox.checked ? checkbox.value || "true" : checkbox.dataset.uncheckedValue || "true";
    });
  }

  function bindStandardEvents(root) {
    root.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (
        typeof target.matches === "function" &&
        target.matches("input[type='checkbox'].toggle-checkbox")
      ) {
        target.value = target.checked ? target.dataset.checkedValue || "true" : target.dataset.uncheckedValue || "true";
      }
      if (
        (typeof target.matches === "function" &&
          target.matches("input, select, textarea")) ||
        (typeof target.closest === "function" &&
          target.closest("input, select, textarea"))
      ) {
        applyDependentOptions(root);
        applyVisibility(root);
      }
    });
  }

  function createHiddenInput(name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  function initClockFacePicker(widget, config) {
    const hidden = widget.querySelector("#selected-clock-face");
    const options = Array.from(widget.querySelectorAll(".image-option"));
    if (!hidden || !options.length) return;

    // primaryColor/secondaryColor fields live in a sibling schema section on
    // the plugin form, not inside the picker widget — scope the lookup to the
    // whole schema root (or document as a last resort) so the face picker
    // still activates when the colour fields are hoisted out of the widget.
    const scope = widget.closest("[data-settings-schema]") ||
      widget.closest("form") || document;
    const primary = scope.querySelector("[name='primaryColor']");
    const secondary = scope.querySelector("[name='secondaryColor']");

    const setColor = (input, value) => {
      if (!input || !value) return;
      input.value = value;
      // Keep any color-preview swatches bound to the input in sync — they
      // listen for `input`/`change`, not direct assignment.
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const selectOption = (option) => {
      options.forEach((item) => {
        const selected = item === option;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      hidden.value = option.dataset.faceName || "";
      setColor(primary, option.dataset.primaryColor);
      setColor(secondary, option.dataset.secondaryColor);
    };

    options.forEach((option) => {
      option.addEventListener("click", () => selectOption(option));
    });

    const currentValue = config.selectedClockFace || hidden.value;
    const initial = options.find((option) => option.dataset.faceName === currentValue) || options[0];
    selectOption(initial);

    if (config.primaryColor) setColor(primary, config.primaryColor);
    if (config.secondaryColor) setColor(secondary, config.secondaryColor);
  }

  function initAIImagePromptTools(widget) {
    const button = widget.querySelector("[data-ai-image-random-prompt]");
    const promptInput = document.querySelector("[name='textPrompt']");
    if (!button || !promptInput) return;

    button.addEventListener("click", async () => {
      const provider = document.querySelector("[name='provider']")?.value || "openai";
      const services = window.__INKYPI_PLUGIN_BOOT__?.apiKeyServices || [];
      if (Array.isArray(services) && services.length) {
        const providerName = provider === "google" ? "Google" : "OpenAI";
        const providerKeyLabel = provider === "google" ? "Google AI" : "OpenAI";
        const service = services.find(
          (item) => String(item?.name || "").toLowerCase() === providerName.toLowerCase()
        );
        if (service && !service.present) {
          if (window.showResponseModal) {
            window.showResponseModal(
              "failure",
              `${providerKeyLabel} API Key not configured.`
            );
          }
          return;
        }
      }
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = "Thinking...";
      try {
        const response = await fetch(
          window.__INKYPI_PLUGIN_BOOT__?.urls?.random_prompt ||
            "/plugin/ai_image/random_prompt",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              provider,
              prompt: promptInput.value || "",
            }),
          }
        );
        const result = await response.json();
        if (!response.ok || !result?.success || !result.prompt) {
          throw new Error(result?.error || "Failed to generate a prompt");
        }
        promptInput.value = result.prompt;
        promptInput.dispatchEvent(new Event("input", { bubbles: true }));
        promptInput.dispatchEvent(new Event("change", { bubbles: true }));
        promptInput.focus();
      } catch (error) {
        console.debug("AI image random prompt failed:", error);
        if (window.showResponseModal) {
          window.showResponseModal(
            "failure",
            error.message || "Failed to generate a prompt"
          );
        }
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    });
  }

  function initNewspaperSearch(widget, config) {
    const newspapers = parseJson(widget.dataset.newspapers || "[]", []);
    const newspaperInput = widget.querySelector("#newspaper");
    const locationInput = widget.querySelector("#locationSearch");
    const slugInput = widget.querySelector("#newspaperSlug");
    const newspaperList = widget.querySelector("#newspaperList");
    if (!newspaperInput || !locationInput || !slugInput || !newspaperList) return;

    const renderList = (items) => {
      while (newspaperList.firstChild) newspaperList.removeChild(newspaperList.firstChild);
      items.forEach((paper) => {
        const option = document.createElement("option");
        option.value = paper.name;
        newspaperList.appendChild(option);
      });
    };

    const updateSlugAndLocation = () => {
      const selected = newspapers.find((paper) => paper.name === newspaperInput.value);
      if (selected) {
        slugInput.value = selected.slug;
        locationInput.value = `${selected.city}, ${selected.country}`;
      } else {
        slugInput.value = "";
      }
    };

    const filterByLocation = () => {
      const value = locationInput.value.toLowerCase();
      const filtered = newspapers.filter((paper) => {
        const formatted = `${paper.city}, ${paper.country}`.toLowerCase();
        return !value || formatted.includes(value);
      });
      renderList(filtered);
      const selected = newspapers.find((paper) => paper.name === newspaperInput.value);
      if (!selected) slugInput.value = "";
    };

    newspaperInput.addEventListener("change", updateSlugAndLocation);
    locationInput.addEventListener("change", filterByLocation);
    renderList(newspapers);

    if (config.newspaperName) {
      newspaperInput.value = config.newspaperName;
      updateSlugAndLocation();
    }
  }

  var _calendarEntryCounter = 0;

  function createCalendarEntry(url, color) {
    _calendarEntryCounter++;
    const entryIndex = _calendarEntryCounter;
    const entry = document.createElement("div");
    entry.className = "dynamic-list-item compact-repeater compact-repeater-calendar";

    const toolbar = document.createElement("div");
    toolbar.className = "dynamic-list-toolbar compact-repeater-toolbar";
    const urlLabel = document.createElement("label");
    urlLabel.className = "sr-only";
    urlLabel.setAttribute("for", "calendarURL_dyn_" + entryIndex);
    urlLabel.textContent = "Calendar URL " + entryIndex;
    const urlInput = document.createElement("input");
    urlInput.type = "url";
    urlInput.id = "calendarURL_dyn_" + entryIndex;
    urlInput.name = "calendarURLs[]";
    urlInput.className = "form-input";
    urlInput.placeholder = "https://calendar.google.com/…/basic.ics";
    urlInput.required = true;
    urlInput.setAttribute("aria-label", "Calendar URL " + entryIndex);
    urlInput.pattern = "https?://.+";
    urlInput.value = url || "";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-btn icon-button";
    removeBtn.setAttribute("aria-label", "Remove calendar");
    const icon = document.createElement("i");
    icon.className = "ph ph-trash ph-thin action-icon";
    icon.setAttribute("aria-hidden", "true");
    removeBtn.appendChild(icon);
    removeBtn.addEventListener("click", () => {
      const list = entry.parentElement;
      if (list) handleRemoveClick(removeBtn, list);
    });
    removeBtn.dataset.boundRemove = "true";
    toolbar.appendChild(urlLabel);
    toolbar.appendChild(urlInput);
    toolbar.appendChild(removeBtn);

    const colorLabel = document.createElement("label");
    colorLabel.className = "dynamic-list-color-group";
    const colorSpan = document.createElement("span");
    colorSpan.textContent = "Color";
    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.name = "calendarColors[]";
    colorInput.className = "color-picker";
    colorInput.value = color || "#007BFF";
    colorLabel.appendChild(colorSpan);
    colorLabel.appendChild(colorInput);

    entry.appendChild(toolbar);
    entry.appendChild(colorLabel);
    return entry;
  }

  function updateRepeaterEmptyState(list) {
    const items = list.querySelectorAll(".dynamic-list-item").length;
    let msg = list.querySelector(".empty-state-message");
    if (items === 0 && !msg) {
      msg = document.createElement("p");
      msg.className = "empty-state-message";
      msg.textContent = 'No calendars configured. Click "Add Calendar" to get started.';
      list.appendChild(msg);
    } else if (items > 0 && msg) {
      msg.remove();
    }
  }

  // JTN-311: Disable remove buttons when only one calendar row remains so the
  // button is never a silent no-op. The tooltip explains why removal is blocked.
  function syncRemoveButtonStates(list) {
    const count = list.querySelectorAll(".dynamic-list-item").length;
    const onlyOne = count <= 1;
    list.querySelectorAll(".remove-btn").forEach((btn) => {
      btn.disabled = onlyOne;
      if (onlyOne) {
        btn.title = "Add another calendar before removing this one";
      } else {
        btn.title = "";
      }
    });
  }

  function handleRemoveClick(button, list) {
    if (list.querySelectorAll(".dynamic-list-item").length <= 1) {
      return;
    }
    const item = button.closest(".dynamic-list-item");
    const parentList = item?.parentElement;
    item?.remove();
    if (parentList) {
      updateRepeaterEmptyState(parentList);
      syncRemoveButtonStates(parentList);
    }
  }

  function bindRemoveButtons(list) {
    list.querySelectorAll(".remove-btn").forEach((button) => {
      if (button.dataset.boundRemove === "true") return;
      button.dataset.boundRemove = "true";
      button.addEventListener("click", () => handleRemoveClick(button, list));
    });
  }

  function initCalendarRepeater(widget, config) {
    const list = widget.querySelector("[data-repeater-list]");
    const addButton = widget.querySelector("[data-repeater-add]");
    if (!list || !addButton) return;
    bindRemoveButtons(list);
    if (!list.children.length) {
      const urls = config["calendarURLs[]"] || [""];
      const colors = config["calendarColors[]"] || ["#007BFF"];
      urls.forEach((url, index) => list.appendChild(createCalendarEntry(url, colors[index] || "#007BFF")));
      if (!urls.length) list.appendChild(createCalendarEntry("", "#007BFF"));
    }
    updateRepeaterEmptyState(list);
    syncRemoveButtonStates(list);
    addButton.addEventListener("click", () => {
      // JTN-357: Refuse to append a new empty row while the previous row
      // holds an empty or invalid value.  This prevents users from spamming
      // empty/invalid rows into the form.
      const items = list.querySelectorAll(".dynamic-list-item");
      if (items.length > 0) {
        const lastItem = items[items.length - 1];
        const lastInput = lastItem.querySelector('input[name="calendarURLs[]"]');
        if (lastInput) {
          const value = (lastInput.value || "").trim();
          if (!value || !lastInput.checkValidity()) {
            lastInput.focus();
            lastInput.reportValidity?.();
            const message = value
              ? "Fix the previous calendar URL before adding another."
              : "Enter a calendar URL before adding another.";
            if (typeof showError === "function") {
              showError(message);
            }
            return;
          }
        }
      }
      list.appendChild(createCalendarEntry("", "#007BFF"));
      updateRepeaterEmptyState(list);
      syncRemoveButtonStates(list);
      bindRemoveButtons(list);
    });
  }

  function updateCalendarAuthKeyHint(entry) {
    const labelInput = entry.querySelector("[data-auth-label]");
    const hint = entry.querySelector("[data-auth-key-hint]");
    if (!labelInput || !hint) return;
    const label = (labelInput.value || "").trim();
    hint.textContent = label ? `Password key: CALENDAR_AUTH_PASSWORD_${label.toUpperCase()}` : "";
  }

  let _calendarAuthEntryCounter = 0;

  function createCalendarAuthEntry(url, color, username, label) {
    _calendarAuthEntryCounter++;
    const entryIndex = _calendarAuthEntryCounter;
    const entry = document.createElement("div");
    entry.className = "dynamic-list-item compact-repeater compact-repeater-calendar-auth";

    const toolbar = document.createElement("div");
    toolbar.className = "dynamic-list-toolbar compact-repeater-toolbar";
    const urlLabel = document.createElement("label");
    urlLabel.className = "sr-only";
    urlLabel.setAttribute("for", "calendarAuthURL_dyn_" + entryIndex);
    urlLabel.textContent = "Calendar URL " + entryIndex;
    const urlInput = document.createElement("input");
    urlInput.type = "url";
    urlInput.id = "calendarAuthURL_dyn_" + entryIndex;
    urlInput.name = "calendarAuthURLs[]";
    urlInput.className = "form-input";
    urlInput.placeholder = "https://calendar.google.com/…/basic.ics";
    urlInput.required = true;
    urlInput.setAttribute("aria-label", "Calendar URL " + entryIndex);
    urlInput.pattern = "https?://.+";
    urlInput.value = url || "";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-btn icon-button";
    removeBtn.setAttribute("aria-label", "Remove calendar");
    const removeIcon = document.createElement("i");
    removeIcon.className = "ph ph-trash ph-thin action-icon";
    removeIcon.setAttribute("aria-hidden", "true");
    removeBtn.appendChild(removeIcon);
    removeBtn.addEventListener("click", () => {
      const list = entry.parentElement;
      if (list) handleRemoveClick(removeBtn, list);
    });
    removeBtn.dataset.boundRemove = "true";
    toolbar.appendChild(urlLabel);
    toolbar.appendChild(urlInput);
    toolbar.appendChild(removeBtn);

    const authToolbar = document.createElement("div");
    authToolbar.className = "dynamic-list-toolbar compact-repeater-toolbar";
    const usernameInput = document.createElement("input");
    usernameInput.type = "text";
    usernameInput.name = "calendarAuthUsernames[]";
    usernameInput.className = "form-input";
    usernameInput.placeholder = "Username (leave blank if no login needed)";
    usernameInput.autocomplete = "username";
    usernameInput.value = username || "";
    usernameInput.dataset.authUsername = "true";
    const labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.name = "calendarAuthLabels[]";
    labelInput.className = "form-input";
    labelInput.placeholder = "Credential label (e.g. HOME)";
    labelInput.pattern = "[A-Za-z0-9_]*";
    labelInput.value = label || "";
    labelInput.dataset.authLabel = "true";
    authToolbar.appendChild(usernameInput);
    authToolbar.appendChild(labelInput);

    const colorLabel = document.createElement("label");
    colorLabel.className = "dynamic-list-color-group";
    const colorSpan = document.createElement("span");
    colorSpan.textContent = "Color";
    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.name = "calendarAuthColors[]";
    colorInput.className = "color-picker";
    colorInput.value = color || "#007BFF";
    colorLabel.appendChild(colorSpan);
    colorLabel.appendChild(colorInput);

    const keyHint = document.createElement("small");
    keyHint.className = "field-note";
    keyHint.dataset.authKeyHint = "true";

    entry.appendChild(toolbar);
    entry.appendChild(authToolbar);
    entry.appendChild(colorLabel);
    entry.appendChild(keyHint);

    labelInput.addEventListener("input", () => updateCalendarAuthKeyHint(entry));
    updateCalendarAuthKeyHint(entry);

    return entry;
  }

  function initCalendarAuthRepeater(widget, config) {
    const list = widget.querySelector("[data-repeater-list]");
    const addButton = widget.querySelector("[data-repeater-add]");
    if (!list || !addButton) return;
    bindRemoveButtons(list);
    if (!list.children.length) {
      const urls = config["calendarAuthURLs[]"] || [""];
      const colors = config["calendarAuthColors[]"] || ["#007BFF"];
      const usernames = config["calendarAuthUsernames[]"] || [""];
      const labels = config["calendarAuthLabels[]"] || [""];
      urls.forEach((url, index) =>
        list.appendChild(
          createCalendarAuthEntry(url, colors[index] || "#007BFF", usernames[index] || "", labels[index] || "")
        )
      );
      if (!urls.length) list.appendChild(createCalendarAuthEntry("", "#007BFF", "", ""));
    } else {
      list.querySelectorAll(".dynamic-list-item").forEach((entry) => updateCalendarAuthKeyHint(entry));
      list.querySelectorAll("[data-auth-label]").forEach((labelInput) => {
        labelInput.addEventListener("input", () => updateCalendarAuthKeyHint(labelInput.closest(".dynamic-list-item")));
      });
    }
    updateRepeaterEmptyState(list);
    syncRemoveButtonStates(list);
    addButton.addEventListener("click", () => {
      const items = list.querySelectorAll(".dynamic-list-item");
      if (items.length > 0) {
        const lastItem = items[items.length - 1];
        const lastInput = lastItem.querySelector('input[name="calendarAuthURLs[]"]');
        if (lastInput) {
          const value = (lastInput.value || "").trim();
          if (!value || !lastInput.checkValidity()) {
            lastInput.focus();
            lastInput.reportValidity?.();
            const message = value
              ? "Fix the previous calendar URL before adding another."
              : "Enter a calendar URL before adding another.";
            if (typeof showError === "function") {
              showError(message);
            }
            return;
          }
        }
      }
      list.appendChild(createCalendarAuthEntry("", "#007BFF", "", ""));
      updateRepeaterEmptyState(list);
      syncRemoveButtonStates(list);
      bindRemoveButtons(list);
    });
  }

  function createTodoEntry(title, body) {
    const entry = document.createElement("div");
    entry.className = "dynamic-list-item compact-repeater compact-repeater-text";

    const toolbar = document.createElement("div");
    toolbar.className = "dynamic-list-toolbar compact-repeater-toolbar";
    const titleInput = document.createElement("input");
    titleInput.type = "text";
    titleInput.name = "list-title[]";
    titleInput.className = "form-input";
    titleInput.placeholder = "List title";
    titleInput.value = title || "";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-btn icon-button";
    removeBtn.setAttribute("aria-label", "Remove list");
    const icon = document.createElement("i");
    icon.className = "ph ph-trash ph-thin action-icon";
    icon.setAttribute("aria-hidden", "true");
    removeBtn.appendChild(icon);
    removeBtn.addEventListener("click", () => {
      const list = entry.parentElement;
      if (list) handleRemoveClick(removeBtn, list);
    });
    removeBtn.dataset.boundRemove = "true";
    toolbar.appendChild(titleInput);
    toolbar.appendChild(removeBtn);

    const textarea = document.createElement("textarea");
    textarea.name = "list[]";
    textarea.className = "form-input dynamic-list-textarea";
    textarea.rows = 5;
    textarea.placeholder = "Enter tasks, one per line";
    textarea.value = body || "";

    entry.appendChild(toolbar);
    entry.appendChild(textarea);
    return entry;
  }

  function initTodoRepeater(widget, config) {
    const list = widget.querySelector("[data-repeater-list]");
    const addButton = widget.querySelector("[data-repeater-add]");
    const maxItems = Number(list?.dataset.maxItems || widget.dataset.maxItems || "3");
    if (!list || !addButton) return;
    bindRemoveButtons(list);
    if (!list.children.length) {
      const titles = config["list-title[]"] || [];
      const items = config["list[]"] || [];
      const seedCount = Math.min(Math.max(titles.length, items.length, 1), maxItems);
      for (let index = 0; index < seedCount; index += 1) {
        list.appendChild(createTodoEntry(titles[index] || "", items[index] || ""));
      }
    }
    syncRemoveButtonStates(list);
    addButton.addEventListener("click", () => {
      if (list.children.length < maxItems) {
        list.appendChild(createTodoEntry("", ""));
        bindRemoveButtons(list);
        syncRemoveButtonStates(list);
      }
    });
  }

  function initGitHubColors(widget, config) {
    const defaults = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"];
    const colors = config["contributionColor[]"] || defaults;
    widget.querySelectorAll("input[name='contributionColor[]']").forEach((input, index) => {
      input.value = colors[index] || defaults[index];
    });
  }

  function initImageUpload(widget, config) {
    const fileInput = widget.querySelector("#imageUpload");
    const fileNames = widget.querySelector("#fileNames");
    const hiddenContainer = widget.querySelector("#hiddenFileInputs");
    if (!fileInput || !fileNames || !hiddenContainer) return;

    const uploadedFiles = globalThis.__INKYPI_UPLOADED_FILES__ || (globalThis.__INKYPI_UPLOADED_FILES__ = {});
    if (!uploadedFiles["imageFiles[]"]) uploadedFiles["imageFiles[]"] = [];

    const renderFile = (id, fileName, removeHandler) => {
      const row = document.createElement("div");
      row.className = "file-name";
      row.id = id;
      const span = document.createElement("span");
      span.textContent = fileName;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "remove-btn";
      btn.setAttribute("aria-label", "Remove file");
      btn.textContent = "X";
      btn.addEventListener("click", removeHandler);
      row.appendChild(span);
      row.appendChild(btn);
      fileNames.appendChild(row);
    };

    (config["imageFiles[]"] || []).forEach((filePath) => {
      const fileName = filePath.split("/").pop();
      renderFile(`existing-${fileName}`, fileName, () => {
        const row = widget.querySelector(`#existing-${CSS.escape(fileName)}`);
        if (row) row.remove();
        const hidden = widget.querySelector(`#hidden-${CSS.escape(fileName)}`);
        if (hidden) hidden.remove();
      });
      const hidden = createHiddenInput("imageFiles[]", filePath);
      hidden.id = `hidden-${fileName}`;
      hiddenContainer.appendChild(hidden);
    });

    fileInput.addEventListener("change", () => {
      Array.from(fileInput.files || []).forEach((file) => {
        if (uploadedFiles["imageFiles[]"].some((item) => item.name === file.name)) return;
        uploadedFiles["imageFiles[]"].push(file);
        renderFile(`added-${file.name}`, file.name, () => {
          uploadedFiles["imageFiles[]"] = uploadedFiles["imageFiles[]"].filter((item) => item.name !== file.name);
          const row = widget.querySelector(`#added-${CSS.escape(file.name)}`);
          if (row) row.remove();
        });
      });
      fileInput.value = "";
    });
  }

  // Calls the free, keyless community HAFAS-wrapper transit APIs directly
  // from the browser (they send Access-Control-Allow-Origin: * - no backend
  // proxy needed).
  const ABFAHRTZEITEN_PROVIDER_BASES = {
    vbb: "https://v6.vbb.transport.rest",
    bvg: "https://v6.bvg.transport.rest",
    db: "https://v6.db.transport.rest",
  };
  const ABFAHRTZEITEN_PROVIDER_LABELS = { vbb: "VBB", bvg: "BVG", db: "Deutsche Bahn" };
  // Rough bounding box for the VBB service area (Berlin + Brandenburg) -
  // inside it we only ever use VBB/BVG (their own HAFAS instance, best data
  // quality), never silently falling back to the lower-quality/less
  // reliable nationwide DB backend; outside it DB is the only option.
  const ABFAHRTZEITEN_BB_LAT_RANGE = [51.3, 53.6];
  const ABFAHRTZEITEN_BB_LON_RANGE = [11.2, 14.8];

  function abfahrtzeitenInBerlinBrandenburg(lat, lon) {
    return (
      lat >= ABFAHRTZEITEN_BB_LAT_RANGE[0] &&
      lat <= ABFAHRTZEITEN_BB_LAT_RANGE[1] &&
      lon >= ABFAHRTZEITEN_BB_LON_RANGE[0] &&
      lon <= ABFAHRTZEITEN_BB_LON_RANGE[1]
    );
  }

  function abfahrtzeitenProvidersFor(lat, lon) {
    if (lat != null && lon != null && !abfahrtzeitenInBerlinBrandenburg(lat, lon)) {
      return ["db"];
    }
    return ["vbb", "bvg", "db"];
  }

  function abfahrtzeitenLocationLatLon(item) {
    const loc = item.location || {};
    const lat = loc.latitude !== undefined ? loc.latitude : item.latitude;
    const lon = loc.longitude !== undefined ? loc.longitude : item.longitude;
    return [lat, lon];
  }

  async function abfahrtzeitenSearchAddress(query) {
    for (const provider of abfahrtzeitenProvidersFor()) {
      try {
        const url = `${ABFAHRTZEITEN_PROVIDER_BASES[provider]}/locations?query=${encodeURIComponent(query)}&results=8&poi=false&addresses=true&stops=true`;
        const resp = await fetch(url);
        if (!resp.ok) continue;
        const data = await resp.json();
        const results = [];
        for (const item of data || []) {
          const [lat, lon] = abfahrtzeitenLocationLatLon(item);
          if (lat == null || lon == null) continue;
          results.push({ id: item.id, name: item.name || item.address, lat, lon, provider, type: item.type });
        }
        if (results.length) return results;
      } catch (e) {
        console.warn(`transit search via ${provider} failed`, e);
      }
    }
    return [];
  }

  async function abfahrtzeitenNearbyStops(lat, lon) {
    for (const provider of abfahrtzeitenProvidersFor(lat, lon)) {
      try {
        const url = `${ABFAHRTZEITEN_PROVIDER_BASES[provider]}/locations/nearby?latitude=${lat}&longitude=${lon}&results=8&distance=1500`;
        const resp = await fetch(url);
        if (!resp.ok) continue;
        const data = await resp.json();
        const results = (data || [])
          .filter((item) => item.type === "stop")
          .map((item) => ({
            id: item.id,
            name: item.name,
            distance: item.distance,
            provider,
            providerLabel: ABFAHRTZEITEN_PROVIDER_LABELS[provider] || provider,
          }));
        if (results.length) {
          results.sort((a, b) => (a.distance ?? Infinity) - (b.distance ?? Infinity));
          return results;
        }
      } catch (e) {
        console.warn(`nearby stops via ${provider} failed`, e);
      }
    }
    return [];
  }

  async function abfahrtzeitenLinesForStop(stopId, provider) {
    const url = `${ABFAHRTZEITEN_PROVIDER_BASES[provider]}/stops/${encodeURIComponent(stopId)}/departures?duration=180&results=100`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("Unable to fetch departures for this stop.");
    const data = await resp.json();
    const seen = new Set();
    const results = [];
    for (const dep of data.departures || []) {
      const line = dep.line && dep.line.name;
      const direction = dep.direction;
      if (!line || !direction) continue;
      const key = `${line}|${direction}`;
      if (seen.has(key)) continue;
      seen.add(key);
      results.push({ lineName: line, direction });
    }
    results.sort((a, b) => (a.lineName + a.direction).localeCompare(b.lineName + b.direction));
    return results;
  }

  function initAbfahrtzeitenStops(widget, config) {
    const list = widget.querySelector("#abfahrtzeitenEntryList");
    const addButton = widget.querySelector("#abfahrtzeitenAddStopBtn");
    const modal = widget.querySelector("#abfahrtzeitenEntryModal");
    const step1 = widget.querySelector("#abfahrtzeitenWizardStep1");
    const step2 = widget.querySelector("#abfahrtzeitenWizardStep2");
    const step3 = widget.querySelector("#abfahrtzeitenWizardStep3");
    const addressInput = widget.querySelector("#abfahrtzeitenAddressSearchInput");
    const searchBtn = widget.querySelector("#abfahrtzeitenSearchAddressBtn");
    const addressResults = widget.querySelector("#abfahrtzeitenAddressResults");
    const nearbyResults = widget.querySelector("#abfahrtzeitenNearbyResults");
    const lineResults = widget.querySelector("#abfahrtzeitenLineResults");
    const status = widget.querySelector("#abfahrtzeitenWizardStatus");
    if (!list || !addButton || !modal || !step1 || !step2 || !step3 || !addressInput || !searchBtn || !addressResults || !nearbyResults || !lineResults || !status) {
      return;
    }

    let selectedStop = null; // {provider, stopId, stopName}

    function setStatus(text) {
      status.textContent = text;
    }

    function showStep(step) {
      step1.hidden = step !== 1;
      step2.hidden = step !== 2;
      step3.hidden = step !== 3;
    }

    function openWizard() {
      selectedStop = null;
      addressInput.value = "";
      addressResults.replaceChildren();
      nearbyResults.replaceChildren();
      lineResults.replaceChildren();
      setStatus("");
      showStep(1);
      modal.hidden = false;
      modal.style.display = "block";
      modal.classList.add("is-open");
    }

    function closeWizard() {
      modal.hidden = true;
      modal.style.display = "none";
      modal.classList.remove("is-open");
    }

    function addEntryRow(entry) {
      const wrapper = document.createElement("div");
      wrapper.className = "dynamic-list-item abfahrtzeiten-stop-entry";

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "entries[]";
      hidden.value = JSON.stringify(entry);

      const summary = document.createElement("div");
      summary.className = "summary";
      const primary = document.createElement("span");
      primary.className = "primary";
      primary.textContent = entry.stopName;
      const secondary = document.createElement("span");
      secondary.className = "secondary";
      secondary.textContent = `${entry.lineName} → ${entry.direction}`;
      summary.appendChild(primary);
      summary.appendChild(secondary);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "remove-btn icon-button";
      removeBtn.setAttribute("aria-label", "Remove stop");
      const removeIcon = document.createElement("i");
      removeIcon.className = "ph ph-trash ph-thin action-icon";
      removeIcon.setAttribute("aria-hidden", "true");
      removeBtn.appendChild(removeIcon);
      removeBtn.addEventListener("click", () => wrapper.remove());

      wrapper.appendChild(hidden);
      wrapper.appendChild(summary);
      wrapper.appendChild(removeBtn);
      list.appendChild(wrapper);
    }

    async function runAddressSearch() {
      const query = addressInput.value.trim();
      if (!query) return;
      setStatus("Searching...");
      try {
        const results = await abfahrtzeitenSearchAddress(query);
        addressResults.replaceChildren();
        if (results.length === 0) {
          setStatus("No results found.");
          return;
        }
        setStatus("");
        results.forEach((r) => {
          const item = document.createElement("div");
          item.className = "abfahrtzeiten-wizard-result-item";
          const name = document.createElement("span");
          name.textContent = r.name;
          item.appendChild(name);
          item.addEventListener("click", () => selectAddress(r));
          addressResults.appendChild(item);
        });
      } catch (e) {
        setStatus(`Error: ${e.message}`);
      }
    }

    async function selectAddress(addr) {
      setStatus("Loading nearby stops...");
      try {
        const results = await abfahrtzeitenNearbyStops(addr.lat, addr.lon);
        nearbyResults.replaceChildren();
        if (results.length === 0) {
          setStatus("No nearby stops found.");
          return;
        }
        setStatus("");
        results.forEach((r) => {
          const item = document.createElement("div");
          item.className = "abfahrtzeiten-wizard-result-item";
          const name = document.createElement("span");
          name.textContent = r.name;
          const meta = document.createElement("span");
          meta.className = "meta";
          meta.textContent = `${r.providerLabel} · ${Math.round(r.distance || 0)}m`;
          item.appendChild(name);
          item.appendChild(meta);
          item.addEventListener("click", () => selectStop(r));
          nearbyResults.appendChild(item);
        });
        showStep(2);
      } catch (e) {
        setStatus(`Error: ${e.message}`);
      }
    }

    async function selectStop(stop) {
      selectedStop = { provider: stop.provider, stopId: stop.id, stopName: stop.name };
      setStatus("Loading lines...");
      try {
        const results = await abfahrtzeitenLinesForStop(stop.id, stop.provider);
        lineResults.replaceChildren();
        if (results.length === 0) {
          setStatus("No upcoming lines found at this stop right now.");
          return;
        }
        setStatus("");
        results.forEach((r) => {
          const item = document.createElement("div");
          item.className = "abfahrtzeiten-wizard-result-item";
          const label = document.createElement("span");
          const strong = document.createElement("strong");
          strong.textContent = r.lineName;
          label.appendChild(strong);
          label.appendChild(document.createTextNode(` → ${r.direction}`));
          item.appendChild(label);
          item.addEventListener("click", () => selectLine(r));
          lineResults.appendChild(item);
        });
        showStep(3);
      } catch (e) {
        setStatus(`Error: ${e.message}`);
      }
    }

    function selectLine(line) {
      addEntryRow({
        provider: selectedStop.provider,
        stopId: selectedStop.stopId,
        stopName: selectedStop.stopName,
        lineName: line.lineName,
        direction: line.direction,
      });
      closeWizard();
    }

    addButton.addEventListener("click", openWizard);
    modal.querySelector(".close-button")?.addEventListener("click", closeWizard);
    searchBtn.addEventListener("click", runAddressSearch);
    addressInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        runAddressSearch();
      }
    });
    step2.querySelector('[data-wizard-back="1"]')?.addEventListener("click", () => {
      setStatus("");
      showStep(1);
    });
    step3.querySelector('[data-wizard-back="2"]')?.addEventListener("click", () => {
      setStatus("");
      showStep(2);
    });

    (config["entries[]"] || []).forEach((raw) => {
      try {
        addEntryRow(JSON.parse(raw));
      } catch (e) {
        console.error("Failed to parse saved Abfahrtzeiten entry", raw, e);
      }
    });
  }

  function initWeatherMap(widget, config) {
    const latInput = widget.querySelector("#latitude");
    const lonInput = widget.querySelector("#longitude");
    const openButton = widget.querySelector("#openMap");
    const saveButton = widget.querySelector("#closeMap");
    const modal = widget.querySelector("#mapModal");
    const mapRoot = widget.querySelector("#map");
    if (!latInput || !lonInput || !openButton || !saveButton || !modal || !mapRoot) return;

    latInput.value = config.latitude || latInput.value || "40.7128";
    lonInput.value = config.longitude || lonInput.value || "-74.0060";

    const mapState = { map: null, marker: null };

    const initLeafletMap = () => {
      if (!globalThis.L || mapState.map) return;
      const lat = Number.parseFloat(latInput.value) || 40.7128;
      const lon = Number.parseFloat(lonInput.value) || -74.006;
      mapState.map = globalThis.L.map(mapRoot).setView([lat, lon], 4.5);
      globalThis.L
        .tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
        .addTo(mapState.map);
      mapState.marker = globalThis.L
        .marker([lat, lon], { draggable: true })
        .addTo(mapState.map);
      mapState.map.on("click", (event) => mapState.marker.setLatLng(event.latlng));
    };

    const openModal = () => {
      modal.hidden = false;
      modal.style.display = "block";
      modal.classList.add("is-open");
      setTimeout(initLeafletMap, 100);
    };

    const closeModal = () => {
      if (mapState.marker) {
        const position = mapState.marker.getLatLng().wrap();
        latInput.value = position.lat;
        lonInput.value = position.lng;
      }
      modal.hidden = true;
      modal.style.display = "none";
      modal.classList.remove("is-open");
    };

    openButton.addEventListener("click", openModal);
    saveButton.addEventListener("click", closeModal);
    modal.querySelector(".close-button")?.addEventListener("click", closeModal);
  }

  const widgetInitializers = {
    "clock-face-picker": initClockFacePicker,
    "ai-image-prompt-tools": initAIImagePromptTools,
    "newspaper-search": initNewspaperSearch,
    "calendar-repeater": initCalendarRepeater,
    "calendar-auth-repeater": initCalendarAuthRepeater,
    "todo-repeater": initTodoRepeater,
    "github-colors": initGitHubColors,
    "image-upload-list": initImageUpload,
    "weather-map": initWeatherMap,
    "abfahrtzeiten-stops": initAbfahrtzeitenStops,
  };

  function initializeWidgets(root, config) {
    root.querySelectorAll("[data-hybrid-widget]").forEach((widget) => {
      const widgetType = widget.dataset.hybridWidget;
      const widgetConfig = parseJson(widget.dataset.widgetConfig || "{}", {});
      const initializer = widgetInitializers[widgetType];
      // Real saved plugin settings (config) must win over a schema's static
      // widget default (widgetConfig) - e.g. regenalarm's central-Germany
      // default lat/long should only apply to a brand-new instance that has
      // no saved location yet, not silently overwrite an existing one's real
      // saved coordinates on every settings-page load (JTN-regenalarm-widget).
      if (initializer) initializer(widget, { ...widgetConfig, ...config });
    });
  }

  function init() {
    const root = document.querySelector("[data-settings-schema]");
    if (!root) return;
    const boot = globalThis.__INKYPI_PLUGIN_BOOT__ || {};
    syncCheckboxState(root);
    initializeWidgets(root, boot.pluginSettings || {});
    applyDependentOptions(root);
    applyVisibility(root);
    bindStandardEvents(root);
  }

  globalThis.InkyPiPluginSchema = { init };
  document.addEventListener("DOMContentLoaded", init);
})();
