(() => {
    const editor = document.querySelector("[data-markdown-editor]");
    const statusSelect = document.getElementById("id_status");
    const statusBadge = document.querySelector("[data-editor-status]");

    const updateStatusBadge = () => {
        if (!statusSelect || !statusBadge) {
            return;
        }
        const value = statusSelect.value;
        statusBadge.textContent = statusSelect.selectedOptions[0]?.textContent || "Draft";
        statusBadge.dataset.status = value;
    };

    statusSelect?.addEventListener("change", updateStatusBadge);
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

    const form = document.querySelector("#content-main form");
    document.addEventListener("keydown", (event) => {
        if (!form || !(event.metaKey || event.ctrlKey)) {
            return;
        }
        if (event.key.toLowerCase() === "s") {
            event.preventDefault();
            form.requestSubmit(form.querySelector(".writing-save"));
        }
    });
})();
