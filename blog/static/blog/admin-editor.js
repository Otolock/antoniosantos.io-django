(() => {
    const editor = document.querySelector("[data-markdown-editor]");
    const editorConfig = document.querySelector("[data-editor-config]");
    const statusSelect = document.getElementById("id_status");
    const statusBadge = document.querySelector("[data-editor-status]");
    const saveState = document.querySelector("[data-editor-save-state]");
    const form = document.querySelector("#content-main form");
    const discardLink = document.querySelector(".writing-discard");
    const recoveryBanner = document.querySelector("[data-draft-recovery]");
    const restoreDraftButton = document.querySelector("[data-restore-draft]");
    const discardDraftButton = document.querySelector("[data-discard-draft]");
    const recoveryTime = document.querySelector("[data-draft-recovery-time]");
    const publishButton = document.querySelector(".writing-publish");
    const publishChecks = document.querySelector("[data-publish-checks]");
    const publishCheckList = document.querySelector("[data-publish-check-list]");
    const draftStorageKey = `studio-draft:${window.location.pathname}`;
    const draftFieldNames = new Set([
        "title", "slug", "body", "description", "reply_to_url", "reply_to_title",
        "tags", "status", "published_at_0", "published_at_1",
    ]);
    let hasUnsavedChanges = false;
    let allowNavigation = false;
    let draftSaveTimer = null;
    let previewTimer = null;
    let publishIsArmed = false;

    if (publishButton) {
        publishButton.dataset.defaultLabel = publishButton.value;
    }

    const insertMarkdown = (snippet) => {
        if (!editor) {
            return;
        }
        const start = editor.selectionStart;
        const end = editor.selectionEnd;
        const before = start > 0 && editor.value[start - 1] !== "\n" ? "\n\n" : "";
        const after = end < editor.value.length && editor.value[end] !== "\n" ? "\n\n" : "\n";
        editor.setRangeText(`${before}${snippet}${after}`, start, end, "end");
        editor.focus();
        editor.dispatchEvent(new Event("input", { bubbles: true }));
    };

    const readDraftFields = () => {
        const fields = {};
        form?.querySelectorAll("input[name], textarea[name], select[name]").forEach((field) => {
            if (!draftFieldNames.has(field.name)) {
                return;
            }
            if (field.type === "checkbox") {
                fields[field.name] ||= [];
                if (field.checked) {
                    fields[field.name].push(field.value);
                }
            } else if (field instanceof HTMLSelectElement && field.multiple) {
                fields[field.name] = Array.from(field.selectedOptions).map((option) => option.value);
            } else {
                fields[field.name] = field.value;
            }
        });
        return fields;
    };

    const saveDraftBackup = (submitted = false) => {
        if (!form) {
            return;
        }
        try {
            window.localStorage.setItem(draftStorageKey, JSON.stringify({
                fields: readDraftFields(),
                savedAt: new Date().toISOString(),
                submitted,
            }));
        } catch (error) {
            // Draft backup is a convenience; editing must still work if storage is unavailable.
        }
    };

    const scheduleDraftBackup = () => {
        window.clearTimeout(draftSaveTimer);
        draftSaveTimer = window.setTimeout(() => saveDraftBackup(false), 600);
    };

    const markUnsaved = () => {
        hasUnsavedChanges = true;
        if (saveState) {
            saveState.textContent = "Unsaved changes";
            saveState.dataset.state = "unsaved";
        }
        scheduleDraftBackup();
        publishIsArmed = false;
        if (publishButton) {
            publishButton.value = publishButton.dataset.defaultLabel;
        }
        if (publishChecks) {
            publishChecks.hidden = true;
        }
    };

    form?.addEventListener("input", markUnsaved);
    form?.addEventListener("change", markUnsaved);
    discardLink?.addEventListener("click", () => { allowNavigation = true; });

    form?.addEventListener("submit", (event) => {
        if (event.submitter?.name === "_preview") {
            return;
        }
        saveDraftBackup(true);
        allowNavigation = true;
        if (saveState) {
            saveState.textContent = "Saving…";
            saveState.dataset.state = "saving";
        }
    });

    window.addEventListener("beforeunload", (event) => {
        if (!hasUnsavedChanges || allowNavigation) {
            return;
        }
        event.preventDefault();
        event.returnValue = "";
    });

    const restoreDraft = (backup) => {
        Object.entries(backup.fields || {}).forEach(([name, value]) => {
            const field = form?.elements.namedItem(name);
            if (!field) {
                return;
            }
            if (field instanceof RadioNodeList) {
                const values = Array.isArray(value) ? value : [value];
                Array.from(field).forEach((choice) => {
                    choice.checked = values.includes(choice.value);
                    choice.dispatchEvent(new Event("change", { bubbles: true }));
                });
                return;
            }
            if (field instanceof HTMLSelectElement && field.multiple) {
                const values = Array.isArray(value) ? value : [value];
                Array.from(field.options).forEach((option) => {
                    option.selected = values.includes(option.value);
                });
            } else {
                field.value = value;
            }
            field.dispatchEvent(new Event("change", { bubbles: true }));
            field.dispatchEvent(new Event("input", { bubbles: true }));
        });
        recoveryBanner.hidden = true;
        markUnsaved();
        editor?.focus();
    };

    if (form && recoveryBanner) {
        try {
            const backup = JSON.parse(window.localStorage.getItem(draftStorageKey));
            if (backup?.fields) {
                const matchesCurrent = JSON.stringify(backup.fields) === JSON.stringify(readDraftFields());
                const hasErrors = Boolean(form.querySelector(".errorlist"));
                if (matchesCurrent && !hasErrors) {
                    window.localStorage.removeItem(draftStorageKey);
                } else if (backup.submitted && window.location.pathname.endsWith("/add/")) {
                    window.localStorage.removeItem(draftStorageKey);
                } else if (!matchesCurrent) {
                    recoveryTime.textContent = backup.savedAt
                        ? `Saved ${new Date(backup.savedAt).toLocaleString()}`
                        : "";
                    recoveryBanner.hidden = false;
                    restoreDraftButton?.addEventListener("click", () => restoreDraft(backup));
                    discardDraftButton?.addEventListener("click", () => {
                        window.localStorage.removeItem(draftStorageKey);
                        recoveryBanner.hidden = true;
                    });
                }
            }
        } catch (error) {
            window.localStorage.removeItem(draftStorageKey);
        }
    }

    const publishingWarnings = () => {
        const warnings = [];
        const body = editor?.value.trim() || "";
        const title = document.getElementById("id_title")?.value.trim();
        const description = document.getElementById("id_description")?.value.trim();
        const publishDate = document.getElementById("id_published_at_0")?.value;
        const publishTime = document.getElementById("id_published_at_1")?.value || "00:00";

        if (!body) {
            warnings.push("The body is empty.");
        }
        if (document.getElementById("id_title") && !title) {
            warnings.push("The post has no title.");
        }
        if (document.getElementById("id_description") && !description) {
            warnings.push("The post has no description for feeds and search results.");
        }
        if (/\[[^\]]*\]\(\s*\)/.test(body)) {
            warnings.push("The Markdown contains an empty link.");
        }
        const unsafeLink = Array.from(body.matchAll(/\]\(([^)]+)\)/g)).find((match) => {
            const url = match[1].trim();
            return /^(javascript|data):/i.test(url) || /\s/.test(url);
        });
        if (unsafeLink) {
            warnings.push("At least one Markdown link looks malformed or unsafe.");
        }
        if (publishDate) {
            const publishAt = new Date(`${publishDate}T${publishTime}`);
            if (publishAt > new Date()) {
                warnings.push(`Publish now will replace the scheduled date (${publishAt.toLocaleString()}) with the current time.`);
            }
        }
        return warnings;
    };

    publishButton?.addEventListener("click", (event) => {
        const warnings = publishingWarnings();
        if (!warnings.length || publishIsArmed) {
            return;
        }
        event.preventDefault();
        publishCheckList.innerHTML = "";
        warnings.forEach((warning) => {
            const item = document.createElement("li");
            item.textContent = warning;
            publishCheckList.appendChild(item);
        });
        publishChecks.hidden = false;
        publishIsArmed = true;
        publishButton.value = "Publish anyway";
        publishChecks.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    const updateStatusBadge = () => {
        if (!statusSelect || !statusBadge) {
            return;
        }
        let value = statusSelect.value;
        let label = statusSelect.selectedOptions[0]?.textContent || "Draft";
        const publishDate = document.getElementById("id_published_at_0")?.value;
        const publishTime = document.getElementById("id_published_at_1")?.value || "00:00";
        const publishAt = publishDate ? new Date(`${publishDate}T${publishTime}`) : null;

        if (value === "published" && publishAt && publishAt > new Date()) {
            value = "scheduled";
            label = "Scheduled";
        }

        statusBadge.textContent = label;
        statusBadge.dataset.status = value;
    };

    statusSelect?.addEventListener("change", updateStatusBadge);
    document.getElementById("id_published_at_0")?.addEventListener("input", updateStatusBadge);
    document.getElementById("id_published_at_1")?.addEventListener("input", updateStatusBadge);
    updateStatusBadge();

    if (editor) {
        const editorContainer = editor.parentNode;
        const toolbar = document.createElement("div");
        toolbar.className = "markdown-toolbar";
        toolbar.setAttribute("aria-label", "Markdown formatting");

        const controls = [
            ["B", "Bold", "**", "**"],
            ["I", "Italic", "_", "_"],
            ["H2", "Heading", "## ", ""],
            ["“”", "Quote", "> ", ""],
            ["🔗", "Link", "[", "](https://)"],
        ];

        const wrapSelection = (before, after) => {
            const start = editor.selectionStart;
            const end = editor.selectionEnd;
            const selected = editor.value.slice(start, end);
            editor.setRangeText(`${before}${selected}${after}`, start, end, "select");
            editor.selectionStart = start + before.length;
            editor.selectionEnd = end + before.length + selected.length;
            editor.focus();
            editor.dispatchEvent(new Event("input", { bubbles: true }));
        };

        controls.forEach(([label, title, before, after]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.title = title;
            button.setAttribute("aria-label", title);
            button.addEventListener("click", () => wrapSelection(before, after));
            toolbar.appendChild(button);
        });

        const modeControls = document.createElement("div");
        modeControls.className = "editor-mode-controls";
        toolbar.appendChild(modeControls);

        const workspace = document.createElement("div");
        workspace.className = "editor-workspace editor-mode-write";
        editor.parentNode.insertBefore(workspace, editor);
        workspace.appendChild(editor);

        const preview = document.createElement("div");
        preview.className = "markdown-preview";
        preview.setAttribute("aria-live", "polite");
        preview.innerHTML = "<p>Preview will appear here.</p>";
        workspace.appendChild(preview);

        const renderPreview = async () => {
            if (!editorConfig?.dataset.renderUrl) {
                return;
            }
            preview.dataset.loading = "true";
            const payload = new FormData();
            payload.append("body", editor.value);
            try {
                const response = await fetch(editorConfig.dataset.renderUrl, {
                    method: "POST",
                    body: payload,
                    credentials: "same-origin",
                    headers: {
                        "X-CSRFToken": form?.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
                        "X-Requested-With": "fetch",
                    },
                });
                if (!response.ok) {
                    throw new Error("Preview failed");
                }
                const data = await response.json();
                preview.innerHTML = data.html || "<p>Nothing to preview yet.</p>";
            } catch (error) {
                preview.innerHTML = "<p>Preview unavailable. Your Markdown is still safe in the editor.</p>";
            } finally {
                delete preview.dataset.loading;
            }
        };

        const setEditorMode = (mode) => {
            workspace.className = `editor-workspace editor-mode-${mode}`;
            modeControls.querySelectorAll("button[data-mode]").forEach((button) => {
                button.setAttribute("aria-pressed", button.dataset.mode === mode ? "true" : "false");
            });
            if (mode !== "write") {
                renderPreview();
            }
        };

        [["Write", "write"], ["Preview", "preview"], ["Split", "split"]].forEach(([label, mode]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.dataset.mode = mode;
            button.setAttribute("aria-pressed", mode === "write" ? "true" : "false");
            button.addEventListener("click", () => setEditorMode(mode));
            modeControls.appendChild(button);
        });

        const focusButton = document.createElement("button");
        focusButton.type = "button";
        focusButton.className = "editor-focus-button";
        focusButton.textContent = "Focus";
        focusButton.setAttribute("aria-pressed", "false");
        focusButton.addEventListener("click", () => {
            const isFocused = document.body.classList.toggle("studio-focus");
            focusButton.textContent = isFocused ? "Exit focus" : "Focus";
            focusButton.setAttribute("aria-pressed", isFocused ? "true" : "false");
            editor.focus();
        });
        modeControls.appendChild(focusButton);

        const uploadStatus = document.createElement("span");
        uploadStatus.className = "editor-upload-status";
        uploadStatus.setAttribute("aria-live", "polite");
        toolbar.appendChild(uploadStatus);

        const count = document.createElement("span");
        count.className = "markdown-count";
        count.setAttribute("aria-live", "polite");
        toolbar.appendChild(count);

        const updateCount = () => {
            const words = editor.value.trim().match(/\S+/g)?.length || 0;
            const minutes = Math.max(1, Math.ceil(words / 220));
            count.textContent = `${words.toLocaleString()} words · ${minutes} min read`;
        };

        editorContainer.insertBefore(toolbar, workspace);
        const schedulePreview = () => {
            if (!workspace.classList.contains("editor-mode-write")) {
                window.clearTimeout(previewTimer);
                previewTimer = window.setTimeout(renderPreview, 300);
            }
        };
        editor.addEventListener("input", () => {
            updateCount();
            schedulePreview();
        });
        updateCount();

        const uploadInlineImage = async (file) => {
            if (!editorConfig?.dataset.uploadUrl) {
                return;
            }
            uploadStatus.textContent = `Uploading ${file.name}…`;
            const payload = new FormData();
            payload.append("file", file);
            payload.append("title", file.name.replace(/\.[^.]+$/, ""));
            try {
                const response = await fetch(editorConfig.dataset.uploadUrl, {
                    method: "POST",
                    body: payload,
                    credentials: "same-origin",
                    headers: {
                        "X-CSRFToken": form?.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
                        "X-Requested-With": "fetch",
                    },
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || "Upload failed");
                }
                insertMarkdown(data.snippet);
                uploadStatus.textContent = `${file.name} inserted`;
            } catch (error) {
                uploadStatus.textContent = error.message || "Upload failed";
            }
        };

        editor.addEventListener("paste", (event) => {
            const images = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
            if (!images.length) {
                return;
            }
            event.preventDefault();
            images.forEach(uploadInlineImage);
        });
        editor.addEventListener("dragover", (event) => {
            if (Array.from(event.dataTransfer?.items || []).some((item) => item.type.startsWith("image/"))) {
                event.preventDefault();
                editor.classList.add("is-dragging-image");
            }
        });
        editor.addEventListener("dragleave", () => editor.classList.remove("is-dragging-image"));
        editor.addEventListener("drop", (event) => {
            const images = Array.from(event.dataTransfer?.files || []).filter((file) => file.type.startsWith("image/"));
            editor.classList.remove("is-dragging-image");
            if (!images.length) {
                return;
            }
            event.preventDefault();
            images.forEach(uploadInlineImage);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && document.body.classList.contains("studio-focus")) {
                document.body.classList.remove("studio-focus");
                focusButton.textContent = "Focus";
                focusButton.setAttribute("aria-pressed", "false");
            }
        });
    }

    const descriptionControl = document.getElementById("description-generate-control");
    const descriptionInput = document.getElementById("id_description");
    if (descriptionControl && descriptionInput && descriptionInput.parentNode) {
        const wrapper = document.createElement("div");
        wrapper.className = "description-field-with-action";
        descriptionInput.parentNode.insertBefore(wrapper, descriptionInput);
        wrapper.appendChild(descriptionInput);
        wrapper.appendChild(descriptionControl);
        descriptionControl.hidden = false;
    }

    document.querySelectorAll("[data-copy-markdown]").forEach((button) => {
        button.addEventListener("click", async () => {
            const original = button.textContent;
            try {
                await navigator.clipboard.writeText(button.dataset.copyMarkdown);
                button.textContent = "Copied";
            } catch (error) {
                button.textContent = "Select snippet";
            }
            window.setTimeout(() => { button.textContent = original; }, 1600);
        });
    });

    document.querySelectorAll("[data-insert-markdown]").forEach((button) => {
        button.addEventListener("click", () => {
            insertMarkdown(button.dataset.insertMarkdown);
            const original = button.textContent;
            button.textContent = "Inserted";
            window.setTimeout(() => { button.textContent = original; }, 1600);
        });
    });

    document.addEventListener("keydown", (event) => {
        if (!form || !(event.metaKey || event.ctrlKey)) {
            return;
        }
        if (event.key.toLowerCase() === "s") {
            event.preventDefault();
            form.requestSubmit(form.querySelector(".writing-save"));
        }
        if (event.key === "Enter") {
            const previewButton = form.querySelector(".writing-preview");
            if (previewButton) {
                event.preventDefault();
                form.requestSubmit(previewButton);
            }
        }
    });
})();
