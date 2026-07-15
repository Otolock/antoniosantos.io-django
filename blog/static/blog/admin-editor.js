(() => {
    const editor = document.querySelector("[data-markdown-editor]");
    const statusSelect = document.getElementById("id_status");
    const statusBadge = document.querySelector("[data-editor-status]");
    const saveState = document.querySelector("[data-editor-save-state]");
    const form = document.querySelector("#content-main form");
    const discardLink = document.querySelector(".writing-discard");
    let hasUnsavedChanges = false;
    let allowNavigation = false;

    const markUnsaved = () => {
        hasUnsavedChanges = true;
        if (saveState) {
            saveState.textContent = "Unsaved changes";
            saveState.dataset.state = "unsaved";
        }
    };

    form?.addEventListener("input", markUnsaved);
    form?.addEventListener("change", markUnsaved);
    discardLink?.addEventListener("click", () => { allowNavigation = true; });

    form?.addEventListener("submit", (event) => {
        if (event.submitter?.name === "_preview") {
            return;
        }
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

        const count = document.createElement("span");
        count.className = "markdown-count";
        count.setAttribute("aria-live", "polite");
        toolbar.appendChild(count);

        const updateCount = () => {
            const words = editor.value.trim().match(/\S+/g)?.length || 0;
            const minutes = Math.max(1, Math.ceil(words / 220));
            count.textContent = `${words.toLocaleString()} words · ${minutes} min read`;
        };

        editor.parentNode.insertBefore(toolbar, editor);
        editor.addEventListener("input", updateCount);
        updateCount();
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
            if (!editor) {
                return;
            }
            const start = editor.selectionStart;
            const end = editor.selectionEnd;
            const before = start > 0 && editor.value[start - 1] !== "\n" ? "\n\n" : "";
            const after = end < editor.value.length && editor.value[end] !== "\n" ? "\n\n" : "\n";
            const insertion = `${before}${button.dataset.insertMarkdown}${after}`;
            editor.setRangeText(insertion, start, end, "end");
            editor.focus();
            editor.dispatchEvent(new Event("input", { bubbles: true }));
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
