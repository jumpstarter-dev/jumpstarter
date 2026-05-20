(function () {
    function getEffectiveTheme() {
        var theme = document.body ? document.body.getAttribute("data-theme") || "auto" : "auto";
        if (theme === "auto") {
            return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        }
        return theme;
    }

    function getMermaidTheme() {
        return getEffectiveTheme() === "dark" ? "dark" : "default";
    }

    function renderMermaid() {
        if (typeof mermaid === "undefined") return;
        mermaid.initialize({ startOnLoad: false, theme: getMermaidTheme() });
        document.querySelectorAll("pre.mermaid").forEach(function (el) {
            var code = el.getAttribute("data-original") || el.textContent;
            el.setAttribute("data-original", code);
            el.removeAttribute("data-processed");
            el.innerHTML = code;
        });
        mermaid.run({ querySelector: "pre.mermaid" });
    }

    document.addEventListener("DOMContentLoaded", function () {
        renderMermaid();

        var observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                if (m.attributeName === "data-theme") renderMermaid();
            });
        });
        if (document.body) {
            observer.observe(document.body, { attributes: true });
        }

        var mq = window.matchMedia("(prefers-color-scheme: dark)");
        if (mq.addEventListener) {
            mq.addEventListener("change", function () {
                var theme = document.body.getAttribute("data-theme");
                if (theme === "auto" || !theme) renderMermaid();
            });
        }
    });
})();
