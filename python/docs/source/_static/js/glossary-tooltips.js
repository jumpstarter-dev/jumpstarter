(function () {
    var definitions = {};

    function fetchGlossary() {
        var glossaryLink = document.querySelector('a[href*="glossary"]');
        if (!glossaryLink) return;
        var href = glossaryLink.getAttribute("href");
        var base = window.location.pathname.replace(/[^/]*$/, "");
        var url = new URL(href, window.location.origin + base).href;

        fetch(url)
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var parser = new DOMParser();
                var doc = parser.parseFromString(html, "text/html");
                doc.querySelectorAll("dl.glossary dt").forEach(function (dt) {
                    var dd = dt.nextElementSibling;
                    if (dd && dd.tagName === "DD") {
                        var term = dt.textContent.trim().toLowerCase();
                        var def = dd.textContent.trim();
                        definitions[term] = def;
                    }
                });
                applyTooltips();
            })
            .catch(function () {});
    }

    function applyTooltips() {
        document.querySelectorAll("a.reference.internal").forEach(function (a) {
            var href = a.getAttribute("href") || "";
            if (href.indexOf("glossary") === -1) return;
            var text = a.textContent.trim().toLowerCase();
            var def = definitions[text];
            if (!def) {
                var stripped = text.replace(/s$/, "");
                def = definitions[stripped];
            }
            if (def) {
                var span = document.createElement("span");
                span.className = "glossary-term";
                span.setAttribute("title", def);
                span.innerHTML = a.innerHTML;
                a.parentNode.replaceChild(span, a);
            }
        });
    }

    document.addEventListener("DOMContentLoaded", fetchGlossary);
})();
