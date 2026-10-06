document.addEventListener('DOMContentLoaded', function () {
    var overlay = document.getElementById('versjon-overlay');
    if (!overlay) return;

    document.body.appendChild(overlay);

    document.addEventListener('click', function (event) {
        if (!overlay.contains(event.target)) {
            var btn = overlay.querySelector('.versjon');
            var content = overlay.querySelector('.versjon-content');
            if (content && content.style.maxHeight) {
                content.style.maxHeight = null;
                btn.classList.remove('active');
            }
        }
    });
});
