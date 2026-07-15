"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const main = document.querySelector("#main");
    const nav = document.querySelector("#nav-sidebar");
    const toggle = document.querySelector("#toggle-nav-sidebar");
    const closeButtons = document.querySelectorAll("[data-sidebar-close]");

    if (main && nav && toggle) {
        if (window.innerWidth < 768 && main.classList.contains("shifted")) {
            toggle.click();
        }

        const syncNavigation = () => {
            const open = main.classList.contains("shifted");
            document.body.classList.toggle("studio-nav-open", open);
            toggle.setAttribute("aria-expanded", String(open));
        };

        toggle.addEventListener("click", () => window.requestAnimationFrame(syncNavigation));
        closeButtons.forEach((button) => {
            button.addEventListener("click", () => {
                if (main.classList.contains("shifted")) toggle.click();
            });
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && main.classList.contains("shifted") && window.innerWidth < 768) {
                toggle.click();
                toggle.focus();
            }
        });
        syncNavigation();
    }

    document.querySelectorAll("#changelist-filter details").forEach((filter, index) => {
        if (window.innerWidth < 768 && index > 0 && !filter.querySelector("li.selected")) {
            filter.removeAttribute("open");
        }
    });
});
