document.addEventListener('DOMContentLoaded', function () {
    var overlay = document.getElementById('versjon-overlay');
    if (!overlay) return;

    var bottomOfPage = document.querySelector('.bottom-of-page');
    var container = bottomOfPage ? bottomOfPage.parentNode : document.body;
    var after = bottomOfPage ? bottomOfPage.nextSibling : null;

    if (after) {
        container.insertBefore(overlay, after);
    } else {
        container.appendChild(overlay);
    }

    overlay.classList.add('versjon-inline');

    var sidebarDrawer = document.querySelector('.sidebar-drawer');
    if (!sidebarDrawer) return;

    function syncSidebarWidth() {
        overlay.style.setProperty(
            '--versjon-sidebar-width',
            sidebarDrawer.getBoundingClientRect().right + 'px'
        );
    }

    syncSidebarWidth();
    window.addEventListener('resize', syncSidebarWidth);
});
