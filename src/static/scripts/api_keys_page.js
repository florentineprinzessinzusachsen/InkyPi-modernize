(function () {
  const CUSTOM_KEY_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

  // Find a card (fixed-provider or custom-secret - both rendered via the
  // same api_key_card macro and both carry data-key-name) by its key name.
  function _cardForKey(keyName) {
    return document.querySelector(
      `.api-key-card[data-key-name="${CSS.escape(keyName)}"]`
    );
  }

  // Helper — find the label for a given card by reading the title text
  // inside it. Used to produce accurate delete-button aria-labels after a
  // save without hard-coding names.
  function _labelForCard(card) {
    const label = card?.querySelector(".api-key-card-head .key-svc");
    return label ? (label.textContent || "").trim() : "";
  }

  function addDeleteButton(card, keyName) {
    // The Delete button lives inside `.api-key-actions` (the input row), NOT
    // `.api-key-card-head` (which holds the label + status). Walk up to the
    // card and then into the actions container so new buttons land next to
    // the input rather than next to the status line.
    const actions = card?.querySelector(".api-key-actions");
    if (!actions) return;
    if (
      !actions.querySelector('.delete-button[data-api-action="delete-key"]')
    ) {
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "header-button delete-button delete-button-danger";
      deleteButton.dataset.apiAction = "delete-key";
      deleteButton.dataset.keyName = keyName;
      deleteButton.dataset.testSkipClick = "true";
      deleteButton.setAttribute(
        "aria-label",
        `Delete ${_labelForCard(card) || keyName} key permanently`
      );
      deleteButton.title = "Permanently remove key from .env";
      deleteButton.textContent = "Delete";
      actions.appendChild(deleteButton);
    }
  }

  function removeDeleteButton(card) {
    card
      ?.querySelector(
        '.api-key-actions .delete-button[data-api-action="delete-key"]'
      )
      ?.remove();
  }

  // Focus can throw if the element became detached between lookup and call
  // (e.g. concurrent re-render) - swallow rather than surface a blocking
  // error over what's otherwise a successful action.
  function _safeFocus(el) {
    try {
      el?.focus();
    } catch {
      // See comment above.
    }
  }

  function _updateKeyChip(card, configured) {
    const chip = card.querySelector("[data-role='key-chip']");
    if (!chip) return;
    chip.classList.toggle("success", !!configured);
    chip.classList.toggle("warning", !configured);
    chip.textContent = configured ? "Configured" : "Not set";
  }

  // Restore a card's "Add key"/"Change key" toggle to its collapsed-summary
  // wording/style and make it visible again (revealInput hides it while the
  // input row is open; cancelInput and a post-save settle both need it back).
  function _updateKeyToggle(card, configured) {
    const toggle = card.querySelector(".api-key-toggle");
    if (!toggle) return;
    const textNode = toggle.querySelector("[data-role='toggle-label']");
    const label = configured ? "Change key" : "Add key";
    if (textNode) {
      textNode.textContent = label;
    } else {
      toggle.textContent = label;
    }
    // "Add key" and "Change key" are both standard/secondary buttons now -
    // only "Save API keys" is the hero action on this page.
    toggle.classList.add("is-secondary");
    toggle.hidden = false;
    toggle.setAttribute("aria-expanded", "false");
  }

  // Transition a card's visible chip / toggle button between the configured
  // and "not set" states without reloading the page. Called from the
  // save-success and delete-success paths so users see immediate feedback.
  function setCardConfigured(card, configured) {
    if (!card) return;
    card.dataset.configured = configured ? "true" : "false";
    _updateKeyChip(card, configured);
    _updateKeyToggle(card, configured);
    // Collapse the input row back to the compact summary row (a draft
    // custom-secret card has no toggle to restore, so this only takes
    // effect on fixed-provider / already-saved custom-secret cards).
    const actions = card.querySelector(".api-key-actions");
    if (actions) actions.setAttribute("hidden", "");
  }

  // Mirror the server's `_mask_key_value` helper in
  // src/blueprints/settings/_config.py so the transient post-save state
  // matches what the server will render on reload (CodeRabbit review, PR
  // #570). If the algorithms ever diverge the worst case is a cosmetic
  // flash between save and next navigation.
  function _maskApiKeyValue(value) {
    if (!value) return "";
    if (value.length >= 4) {
      return `...${value.slice(-4)} (${value.length} chars)`;
    }
    return `set (${value.length} chars)`;
  }

  function _upsertMaskChip(card, maskedValue) {
    if (!card || !maskedValue) return;
    const target = card.querySelector(".key-row-right");
    if (!target) return;
    let chip = target.querySelector(".api-mask");
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "api-mask mono";
      chip.setAttribute("aria-hidden", "true");
      target.insertBefore(chip, target.firstChild);
    }
    chip.textContent = maskedValue;
  }

  // Turn a not-yet-saved custom-secret card (editable name input) into a
  // normal saved card (static title) after its first successful save.
  // The Cancel button stays - once customDraft flips to "false" it just
  // means "clear the input" instead of "discard this whole row", same as
  // on every other card. Everything else about the card (status chip/mask)
  // is then handled uniformly by updateConfiguredStatus, exactly as it is
  // for fixed-provider cards.
  function _finalizeDraftCard(card, keyName) {
    const nameInput = card.querySelector(".custom-secret-name-input");
    if (nameInput) {
      const label = document.createElement("label");
      label.className = "form-label key-svc";
      label.textContent = keyName;
      const valueInput = card.querySelector(".custom-secret-value-input");
      if (valueInput?.id) label.setAttribute("for", valueInput.id);
      nameInput.replaceWith(label);
    }
    card.dataset.customDraft = "false";
  }

  function updateConfiguredStatus(updatedKeys) {
    updatedKeys.forEach((key) => {
      const card = _cardForKey(key);
      if (!card) return;
      if (card.dataset.customDraft === "true") {
        _finalizeDraftCard(card, key);
      }
      const statusElement = card.querySelector(".api-key-status");
      const inputElement = card.querySelector('input[type="password"]');
      const value = inputElement ? inputElement.value : "";
      if (statusElement && value) {
        statusElement.textContent = `Status: Configured (${_maskApiKeyValue(value)})`;
        // Insert/update the masked-key preview pill so the card's transient
        // state matches the server-rendered version after a reload.
        _upsertMaskChip(card, _maskApiKeyValue(value));
        // Clear the input and update its placeholder so subsequent edits
        // start from empty rather than appending to the prior entry.
        inputElement.value = "";
        inputElement.placeholder = "(leave blank to keep current)";
        addDeleteButton(card, key);
        setCardConfigured(card, true);
      }
    });
  }

  function updateDeletedStatus(keyName) {
    const card = _cardForKey(keyName);
    if (!card) return;
    const statusElement = card.querySelector(".api-key-status");
    const inputElement = card.querySelector('input[type="password"]');
    if (statusElement) {
      statusElement.textContent = "Status: Not configured";
    }
    if (inputElement) {
      inputElement.value = "";
      inputElement.placeholder =
        inputElement.dataset.emptyPlaceholder || "Enter API key";
    }
    removeDeleteButton(card);
    setCardConfigured(card, false);
    // Also remove the "Configured" mask chip since the key is gone.
    card.querySelector(".api-mask")?.remove();
    // A deleted custom secret's card is gone for good (unlike fixed
    // providers, which always keep their card) - remove it entirely so it
    // doesn't linger as a permanently-empty custom card. Custom cards live
    // in the very same grid as the fixed providers (seamless layout), so
    // data-custom-secret - not container membership - is what tells them
    // apart.
    if (card.dataset.customSecret === "true") {
      card.remove();
      refreshKeyCounts();
    }
  }

  // Reveal the hidden .api-key-actions row (password input + Cancel +
  // optional Delete) for a fixed-provider or already-saved custom-secret
  // card, and hide the "Add key"/"Change key" toggle that triggered it -
  // Cancel is what brings the toggle back, so the two are never shown at
  // once. Hoisted because it closes over no createApiKeysPage state
  // (SonarCloud javascript:S7721).
  function revealInput(button) {
    const inputId = button.dataset.inputId;
    if (!inputId) return;
    const input = document.getElementById(inputId);
    if (!input) return;
    const actions = input.closest(".api-key-actions");
    if (!actions) return;
    actions.removeAttribute("hidden");
    button.setAttribute("aria-expanded", "true");
    button.hidden = true;
    _safeFocus(input);
  }

  // Cancel button next to every card's input. On a not-yet-saved
  // custom-secret draft it discards the whole row (there's nothing saved
  // yet, so "cancel" means "never mind, forget this entry"). On any other
  // card - fixed provider or an already-saved custom secret - it clears
  // whatever's been typed and collapses the input row back to the compact
  // summary + toggle, undoing the reveal from "Add key"/"Change key". It
  // never touches the stored key itself (Delete, shown only once a key is
  // configured, is the only way to do that).
  function cancelInput(button) {
    const card = button.closest(".api-key-card");
    if (!card) return;
    if (card.dataset.customDraft === "true") {
      card.remove();
      refreshKeyCounts();
      return;
    }
    const input = card.querySelector('input[type="password"]');
    if (input) {
      input.value = "";
      input.setAttribute("aria-invalid", "false");
    }
    const actions = card.querySelector(".api-key-actions");
    if (actions) actions.setAttribute("hidden", "");
    const toggle = card.querySelector(".api-key-toggle");
    if (toggle) {
      toggle.hidden = false;
      toggle.setAttribute("aria-expanded", "false");
    }
  }

  // Keep the paired value input's `name` attribute (what actually gets
  // submitted) in sync with whatever the user has typed as the key name.
  // Only a syntactically-valid name is ever assigned, so save_api_keys()
  // never receives a bogus field name - an invalid/empty name simply means
  // this row contributes nothing to the submission.
  function onCustomSecretNameInput(event) {
    const nameInput = event.target;
    const card = nameInput.closest(".api-key-card");
    const valueInput = card?.querySelector(".custom-secret-value-input");
    const typed = nameInput.value.trim();
    const valid = typed === "" || CUSTOM_KEY_NAME_RE.test(typed);
    nameInput.setAttribute("aria-invalid", valid ? "false" : "true");
    if (card) card.dataset.keyName = typed && valid ? typed : "";
    if (valueInput) {
      if (typed && valid) {
        valueInput.name = typed;
      } else {
        valueInput.removeAttribute("name");
      }
    }
  }

  function refreshKeyCounts() {
    const totalConfigured = document.querySelectorAll(
      '.api-key-card[data-configured="true"]'
    ).length;
    const customCardCount = document.querySelectorAll(
      '.api-key-card[data-custom-secret="true"]'
    ).length;
    const totalProviders = 6 + customCardCount;
    const providersChip = document.getElementById("providerCountSummary");
    if (providersChip) {
      providersChip.textContent = `${totalProviders} provider${totalProviders === 1 ? "" : "s"}`;
    }
    const configuredChip = document.getElementById("configuredCountSummary");
    if (configuredChip) {
      configuredChip.textContent = `${totalConfigured} configured`;
    }
  }

  function createApiKeysPage(config) {
    // Dirty-tracking state: true when any field has changed since last save/load.
    let _isDirty = false;

    // Monotonic suffix for unique id/name/label on JS-built custom-secret
    // draft cards. Each call to addCustomSecretCard bumps this so assistive
    // tech and autofill can distinguish inputs even across multiple drafts.
    let _draftCounter = 0;

    function markDirty() {
      _isDirty = true;
      const saveBtn = document.getElementById("saveApiKeysBtn");
      if (saveBtn) saveBtn.disabled = false;
    }

    function markClean() {
      _isDirty = false;
      const saveBtn = document.getElementById("saveApiKeysBtn");
      if (saveBtn) saveBtn.disabled = true;
    }

    function addCustomSecretCard() {
      // Custom secrets render inline in the same grid as the fixed
      // providers (no separate "Custom secrets" section) - new drafts are
      // inserted right before the "+ Add Custom Secret" button so it stays
      // pinned at the end of the list.
      const grid = document.getElementById("apiKeysGrid");
      const addBtn = document.getElementById("addCustomSecretBtn");
      if (!grid) {
        console.warn("api_keys_page: #apiKeysGrid not found in DOM");
        return;
      }
      markDirty();
      _draftCounter += 1;
      const suffix = `draft-${_draftCounter}`;

      const card = document.createElement("div");
      card.className = "form-group api-key-card";
      card.dataset.keyCard = "";
      card.dataset.keyName = "";
      card.dataset.customSecret = "true";
      card.dataset.configured = "false";
      card.dataset.customDraft = "true";

      const keyRow = document.createElement("div");
      keyRow.className = "key-row";

      const head = document.createElement("div");
      head.className = "key-row-left api-key-card-head";
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.className = "form-input custom-secret-name-input";
      nameInput.placeholder = "KEY_NAME";
      nameInput.setAttribute("aria-label", "Custom secret name");
      nameInput.autocomplete = "off";
      nameInput.spellcheck = false;
      nameInput.addEventListener("input", onCustomSecretNameInput);
      head.appendChild(nameInput);

      const right = document.createElement("div");
      right.className = "key-row-right";
      const chip = document.createElement("span");
      chip.className = "status-chip warning has-dot";
      chip.dataset.role = "key-chip";
      chip.textContent = "Not set";
      const statusDiv = document.createElement("div");
      statusDiv.className = "api-key-status sr-only";
      statusDiv.id = `custom-${suffix}-status`;
      statusDiv.textContent = "Status: Not configured";
      right.appendChild(chip);
      right.appendChild(statusDiv);

      keyRow.appendChild(head);
      keyRow.appendChild(right);

      // One step: the value input sits right alongside the name input from
      // the start, no "Add key" click needed to reveal it first.
      const actions = document.createElement("div");
      actions.className = "input-container api-key-actions";
      const valueInput = document.createElement("input");
      valueInput.type = "password";
      valueInput.id = `custom-${suffix}-input`;
      valueInput.className = "form-input custom-secret-value-input";
      valueInput.placeholder = "Enter secret value";
      valueInput.dataset.emptyPlaceholder = "Enter secret value";
      valueInput.autocomplete = "off";
      valueInput.spellcheck = false;
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "header-button is-secondary";
      cancelBtn.dataset.apiAction = "cancel-input";
      cancelBtn.textContent = "Cancel";
      actions.appendChild(valueInput);
      actions.appendChild(cancelBtn);

      card.appendChild(keyRow);
      card.appendChild(actions);

      grid.insertBefore(card, addBtn);
      _safeFocus(nameInput);
      refreshKeyCounts();
    }

    // Validate every not-yet-saved custom-secret draft before a save is
    // attempted. Returns false (and surfaces an inline + toast error) on
    // the first problem found; an untouched draft (no name, no value) is
    // silently ignored rather than treated as an error.
    function validateCustomSecretDrafts() {
      const savedNames = new Set(
        Array.from(
          document.querySelectorAll(
            '.api-key-card:not([data-custom-draft="true"])[data-key-name]'
          )
        )
          .map((c) => c.dataset.keyName)
          .filter(Boolean)
      );
      const draftCards = Array.from(
        document.querySelectorAll('.api-key-card[data-custom-draft="true"]')
      );
      const seenDraftNames = new Set();
      for (const card of draftCards) {
        const nameInput = card.querySelector(".custom-secret-name-input");
        const valueInput = card.querySelector(".custom-secret-value-input");
        const name = nameInput?.value.trim() || "";
        const value = valueInput?.value.trim() || "";
        nameInput?.setAttribute("aria-invalid", "false");
        valueInput?.setAttribute("aria-invalid", "false");
        if (!name && !value) continue;

        if (!CUSTOM_KEY_NAME_RE.test(name)) {
          nameInput?.setAttribute("aria-invalid", "true");
          showResponseModal(
            "failure",
            "Custom secret names must start with a letter or underscore and contain only letters, numbers, and underscores."
          );
          _safeFocus(nameInput);
          return false;
        }
        if (!value) {
          valueInput?.setAttribute("aria-invalid", "true");
          showResponseModal(
            "failure",
            `Enter a value for ${name}, or remove that entry.`
          );
          _safeFocus(valueInput);
          return false;
        }
        if (savedNames.has(name) || seenDraftNames.has(name)) {
          nameInput?.setAttribute("aria-invalid", "true");
          showResponseModal(
            "failure",
            `${name} is already in use. Choose a different name.`
          );
          _safeFocus(nameInput);
          return false;
        }
        seenDraftNames.add(name);
      }
      return true;
    }

    // Extracted to keep saveKeys below the cognitive-complexity threshold
    // (SonarCloud javascript:S3776). Shows the appropriate modal for a
    // successful resp.ok response and refreshes the configured-status UI
    // for keys that were actually written.
    function handleSaveSuccess(result, hadNewCustomSecret) {
      const skipped = Array.isArray(result.skipped_placeholder)
        ? result.skipped_placeholder
        : [];
      if (skipped.length > 0) {
        // Some values were rejected as bullet-character placeholders
        // (JTN-598). Tell the user which ones so they can retype if they
        // actually wanted to update those keys.
        showResponseModal(
          "failure",
          `Saved with warnings. Skipped placeholder-only values for: ${skipped.join(
            ", "
          )}. Type a real key and save again to update these.`
        );
      } else {
        showResponseModal("success", `Success! ${result.message}`);
      }
      if (result.updated && result.updated.length > 0) {
        updateConfiguredStatus(result.updated);
      }
      if (hadNewCustomSecret) {
        // A brand-new custom secret was part of this save: the simplest
        // way to get its finalized card into a state indistinguishable
        // from a normal reload is to just do one - in-place updates above
        // already gave instant feedback, this just settles everything.
        setTimeout(() => globalThis.location.reload(), 1000);
      }
    }

    function finalizeSaveButton(saveBtn, savedOk) {
      if (!saveBtn) return;
      saveBtn.textContent = "Save API keys";
      if (savedOk) {
        markClean();
      } else {
        // Re-enable so user can retry
        saveBtn.disabled = false;
      }
    }

    async function saveKeys() {
      if (!_isDirty) {
        showResponseModal("info", "No changes to save.");
        return;
      }
      if (!validateCustomSecretDrafts()) return;

      const hadNewCustomSecret = Array.from(
        document.querySelectorAll('.api-key-card[data-custom-draft="true"]')
      ).some((card) => {
        const name = card.querySelector(".custom-secret-name-input")?.value.trim();
        const value = card.querySelector(".custom-secret-value-input")?.value.trim();
        return !!name && !!value;
      });

      const form = document.getElementById("apiKeysForm");
      const saveBtn = document.getElementById("saveApiKeysBtn");
      if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving…"; }
      const data = new FormData(form);
      let savedOk = false;
      try {
        const resp = await fetch(config.saveManagedUrl, {
          method: "POST",
          body: data,
        });
        const result = await resp.json();
        if (resp.ok) {
          savedOk = true;
          handleSaveSuccess(result, hadNewCustomSecret);
        } else {
          showResponseModal("failure", `Error! ${result.error}`);
        }
      } catch (e) {
        showResponseModal("failure", "Failed to save keys. Please try again.");
      } finally {
        finalizeSaveButton(saveBtn, savedOk);
      }
    }

    async function deleteKey(keyName) {
      if (!confirm(`Delete the ${keyName} API key? This cannot be undone.`)) return;
      const data = new FormData();
      data.append("key", keyName);
      try {
        const resp = await fetch(config.deleteManagedUrl, {
          method: "POST",
          body: data,
        });
        const result = await resp.json();
        if (resp.ok) {
          showResponseModal("success", `Success! ${result.message}`);
          updateDeletedStatus(keyName);
        } else {
          showResponseModal("failure", `Error! ${result.error}`);
        }
      } catch (e) {
        showResponseModal("failure", "Failed to delete key. Please try again.");
      }
    }

    function togglePasswordVisibility(button) {
      const inputId = button.dataset.toggleInput;
      const input = document.getElementById(inputId);
      if (!input) return;
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.textContent = isPassword ? "●" : "○";
      button.setAttribute("aria-label", isPassword ? "Hide key" : "Show key");
    }

    function init() {
      // Sync the badge labels with the current DOM once on load.
      refreshKeyCounts();
      // Add show/hide toggle buttons next to password inputs
      document.querySelectorAll('input[type="password"].form-input').forEach((input) => {
        if (!input.value) return; // Skip unconfigured providers (empty input has no key to reveal)
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "toggle-password-btn";
        toggle.dataset.toggleInput = input.id;
        toggle.dataset.apiAction = "toggle-password";
        toggle.textContent = "○";
        toggle.setAttribute("aria-label", "Show key");
        toggle.title = "Toggle visibility";
        input.parentElement.insertBefore(toggle, input.nextSibling);
      });
      const addBtn = document.getElementById("addCustomSecretBtn");
      const saveBtn = document.getElementById("saveApiKeysBtn");
      // Save starts disabled until the user makes a change
      if (saveBtn) saveBtn.disabled = true;
      // addBtn's click is handled by the delegated data-api-action listener
      // below ("add-custom-secret") - it must NOT also get a direct listener
      // here, or a single click fires addCustomSecretCard() twice and adds
      // two draft cards at once.
      if (!addBtn) {
        console.warn("api_keys_page: #addCustomSecretBtn not found in DOM");
      }
      if (saveBtn) {
        saveBtn.addEventListener("click", saveKeys);
      } else {
        console.warn("api_keys_page: #saveApiKeysBtn not found in DOM");
      }
      // Mark dirty on any input change within the page
      document.addEventListener("input", (event) => {
        if (
          event.target.closest(".api-keys-frame") &&
          (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA")
        ) {
          markDirty();
        }
      });
      document.addEventListener("click", (event) => {
        const actionEl = event.target.closest("[data-api-action]");
        if (!actionEl) return;
        const action = actionEl.dataset.apiAction;
        if (action === "add-custom-secret") {
          addCustomSecretCard();
        } else if (action === "delete-key") {
          deleteKey(actionEl.dataset.keyName);
        } else if (action === "cancel-input") {
          cancelInput(actionEl);
        } else if (action === "reveal-input") {
          revealInput(actionEl);
        } else if (action === "toggle-password") {
          togglePasswordVisibility(actionEl);
        }
      });
    }

    Object.assign(globalThis, {
      addCustomSecretCard,
      deleteKey,
      cancelInput,
      saveKeys,
    });

    return { init };
  }

  globalThis.InkyPiApiKeysPage = { create: createApiKeysPage };

  // Self-initialise from data-* attributes on the page container so no
  // inline <script> is needed (CSP blocks inline JS in production).
  // The deferred script attribute ensures the DOM is ready when this runs.
  function autoInit() {
    const frame = document.querySelector(".api-keys-frame");
    if (!frame) return;
    const config = {
      deleteManagedUrl: frame.dataset.deleteManagedUrl || "",
      saveManagedUrl: frame.dataset.saveManagedUrl || "",
    };
    createApiKeysPage(config).init();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }
})();
