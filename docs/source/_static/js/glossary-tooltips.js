(function () {
    var definitions = {};
    var isTouch = !window.matchMedia("(pointer: fine)").matches;

    function fetchGlossary() {
        var links = document.querySelectorAll('a.reference.internal[href*="glossary.html#term-"]');
        if (links.length === 0) return;

        var href = links[0].getAttribute("href");
        var glossaryUrl = href.split("#")[0];
        var base = window.location.pathname.replace(/[^/]*$/, "");
        var url = new URL(glossaryUrl, window.location.origin + base).href;

        fetch(url)
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var parser = new DOMParser();
                var doc = parser.parseFromString(html, "text/html");
                doc.querySelectorAll("dl.glossary dt[id]").forEach(function (dt) {
                    var id = dt.getAttribute("id");
                    var dd = dt.nextElementSibling;
                    if (!dd || dd.tagName !== "DD") {
                        dd = dt.parentElement.querySelector("dd");
                    }
                    if (dd) {
                        definitions[id] = dd.textContent.trim();
                    }
                });
                applyTooltips();
            })
            .catch(function () {});
    }

    function applyTooltips() {
        document.querySelectorAll('a.reference.internal[href*="glossary.html#term-"]').forEach(function (a) {
            var href = a.getAttribute("href");
            var termId = href.split("#")[1];
            var def = definitions[termId];
            if (def) {
                var span = document.createElement("span");
                span.className = "glossary-term";
                span.setAttribute("data-tooltip", def);
                while (a.firstChild) {
                    span.appendChild(a.firstChild);
                }
                a.appendChild(span);

                if (isTouch) {
                    a.addEventListener("click", function (e) {
                        var wasActive = span.classList.contains("tooltip-active");
                        document.querySelectorAll(".glossary-term.tooltip-active").forEach(function (el) {
                            el.classList.remove("tooltip-active");
                        });
                        if (!wasActive) {
                            e.preventDefault();
                            span.classList.add("tooltip-active");
                        }
                    });
                }
            }
        });

        if (isTouch) {
            document.addEventListener("click", function (e) {
                if (!e.target.closest(".glossary-term")) {
                    document.querySelectorAll(".glossary-term.tooltip-active").forEach(function (el) {
                        el.classList.remove("tooltip-active");
                    });
                }
            });
        }
    }

    document.addEventListener("DOMContentLoaded", fetchGlossary);
})();
