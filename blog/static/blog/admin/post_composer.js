(function () {
  "use strict";

  function csrfToken() {
    const input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function initComposer(workbench) {
    const body = document.querySelector(".post-composer__body");
    const grid = workbench.querySelector("[data-media-grid]");
    const upload = workbench.querySelector("[data-media-upload]");
    const search = workbench.querySelector("[data-media-search]");
    const insertSelected = workbench.querySelector("[data-insert-selected]");
    const selectionCount = workbench.querySelector("[data-selection-count]");
    const status = workbench.querySelector("[data-media-status]");
    const writingStats = workbench.querySelector("[data-writing-stats]");

    if (!body || !grid) return;

    function setStatus(message, isError) {
      status.textContent = message || "";
      status.classList.toggle("is-error", Boolean(isError));
    }

    function snippet(card) {
      const label = card.dataset.mediaAlt.trim() || card.dataset.mediaTitle.trim();
      return card.dataset.mediaImage === "true"
        ? `![${label}](${card.dataset.mediaUrl})`
        : `[${label}](${card.dataset.mediaUrl})`;
    }

    function insertSnippets(cards) {
      if (!cards.length) return;
      const start = body.selectionStart;
      const end = body.selectionEnd;
      const before = body.value.slice(0, start);
      const after = body.value.slice(end);
      const prefix = before && !before.endsWith("\n") ? "\n\n" : "";
      const suffix = after && !after.startsWith("\n") ? "\n\n" : "";
      const text = prefix + cards.map(snippet).join("\n\n") + suffix;

      body.setRangeText(text, start, end, "end");
      body.focus();
      body.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function updateSelection() {
      const selected = grid.querySelectorAll("[data-media-select]:checked");
      selectionCount.textContent = String(selected.length);
      insertSelected.disabled = selected.length === 0;
      grid.querySelectorAll("[data-media-card]").forEach(function (card) {
        card.classList.toggle(
          "is-selected",
          Boolean(card.querySelector("[data-media-select]:checked"))
        );
      });
    }

    function updateWritingStats() {
      const text = body.value.trim();
      const words = text ? text.split(/\s+/).length : 0;
      writingStats.textContent = `${words} ${words === 1 ? "word" : "words"} · ${body.value.length} characters`;
    }

    function escapeSelector(value) {
      if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
      return value.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
    }

    function cardMarkup(item) {
      const article = document.createElement("article");
      article.className = "post-media-card";
      article.dataset.mediaCard = "";
      article.dataset.mediaId = item.id;
      article.dataset.mediaTitle = item.title;
      article.dataset.mediaAlt = item.alt_text;
      article.dataset.mediaFilename = item.filename;
      article.dataset.mediaUrl = item.file_url;
      article.dataset.mediaImage = String(item.is_image);
      article.dataset.mediaUpdateUrl = item.update_url;

      const selector = document.createElement("label");
      selector.className = "post-media-card__select";
      selector.title = `Select ${item.title}`;
      selector.innerHTML = '<input type="checkbox" data-media-select><span class="visually-hidden"></span>';
      selector.querySelector("span").textContent = `Select ${item.title}`;

      const preview = document.createElement("div");
      preview.className = "post-media-card__preview";
      if (item.is_image) {
        const image = document.createElement("img");
        image.src = item.file_url;
        image.alt = "";
        image.loading = "lazy";
        preview.appendChild(image);
      } else {
        preview.innerHTML = '<span class="post-media-card__file">FILE</span>';
      }

      const cardBody = document.createElement("div");
      cardBody.className = "post-media-card__body";
      cardBody.innerHTML = `
        <strong class="post-media-card__title" data-card-title></strong>
        <p class="post-media-card__alt is-missing" data-card-alt></p>
        <div class="post-media-card__actions">
          <button type="button" class="button" data-insert-one>Insert</button>
          <details>
            <summary>Edit details</summary>
            <div class="post-media-card__editor">
              <label>Title<input type="text" maxlength="200" data-media-title-input></label>
              <label>Alt text<textarea maxlength="200" rows="3" data-media-alt-input placeholder="Describe what is visible and meaningful in the photo"></textarea></label>
              <button type="button" class="button" data-save-media>Save details</button>
            </div>
          </details>
        </div>`;
      cardBody.querySelector("[data-card-title]").textContent = item.title;
      cardBody.querySelector("[data-card-alt]").textContent = item.alt_text || "Alt text needed";
      cardBody.querySelector("[data-media-title-input]").value = item.title;
      cardBody.querySelector("[data-media-alt-input]").value = item.alt_text;

      article.append(selector, preview, cardBody);
      return article;
    }

    grid.addEventListener("change", function (event) {
      if (event.target.matches("[data-media-select]")) updateSelection();
    });

    grid.addEventListener("click", async function (event) {
      const card = event.target.closest("[data-media-card]");
      if (!card) return;

      if (event.target.closest("[data-insert-one]")) {
        insertSnippets([card]);
        return;
      }

      const saveButton = event.target.closest("[data-save-media]");
      if (!saveButton) return;

      const titleInput = card.querySelector("[data-media-title-input]");
      const altInput = card.querySelector("[data-media-alt-input]");
      const formData = new FormData();
      formData.append("title", titleInput.value);
      formData.append("alt_text", altInput.value);
      saveButton.disabled = true;
      saveButton.textContent = "Saving…";

      try {
        const response = await fetch(card.dataset.mediaUpdateUrl, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken() },
          body: formData,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not save details.");

        card.dataset.mediaTitle = result.media.title;
        card.dataset.mediaAlt = result.media.alt_text;
        card.querySelector("[data-card-title]").textContent = result.media.title;
        const altLabel = card.querySelector("[data-card-alt]");
        altLabel.textContent = result.media.alt_text || "Alt text needed";
        altLabel.classList.toggle("is-missing", !result.media.alt_text);
        card.querySelector("details").open = false;
        setStatus(`Saved details for “${result.media.title}”.`, false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        saveButton.disabled = false;
        saveButton.textContent = "Save details";
      }
    });

    insertSelected.addEventListener("click", function () {
      const cards = Array.from(grid.querySelectorAll("[data-media-select]:checked"))
        .map(function (checkbox) { return checkbox.closest("[data-media-card]"); });
      insertSnippets(cards);
      cards.forEach(function (card) {
        card.querySelector("[data-media-select]").checked = false;
      });
      updateSelection();
    });

    search.addEventListener("input", function () {
      const query = search.value.trim().toLocaleLowerCase();
      grid.querySelectorAll("[data-media-card]").forEach(function (card) {
        const haystack = `${card.dataset.mediaTitle} ${card.dataset.mediaAlt} ${card.dataset.mediaFilename}`.toLocaleLowerCase();
        card.hidden = Boolean(query) && !haystack.includes(query);
      });
    });

    upload.addEventListener("change", async function () {
      const files = Array.from(upload.files || []);
      if (!files.length) return;

      const formData = new FormData();
      files.forEach(function (file) { formData.append("files", file); });
      upload.disabled = true;
      setStatus(`Uploading ${files.length} ${files.length === 1 ? "photo" : "photos"}…`, false);

      try {
        const response = await fetch(workbench.dataset.uploadUrl, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken() },
          body: formData,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Upload failed.");

        const empty = grid.querySelector("[data-media-empty]");
        if (empty) empty.remove();
        result.media.slice().reverse().forEach(function (item) {
          const existing = grid.querySelector(`[data-media-id="${escapeSelector(String(item.id))}"]`);
          if (!existing) grid.prepend(cardMarkup(item));
        });
        const errorText = result.errors && result.errors.length
          ? ` ${result.errors.join(" ")}`
          : "";
        setStatus(`Uploaded ${result.media.length} ${result.media.length === 1 ? "photo" : "photos"}.${errorText}`, Boolean(errorText));
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        upload.value = "";
        upload.disabled = false;
      }
    });

    body.addEventListener("input", updateWritingStats);
    updateSelection();
    updateWritingStats();
  }

  function init() {
    document.querySelectorAll("[data-media-workbench]").forEach(initComposer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
