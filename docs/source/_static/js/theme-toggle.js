// Theme toggle tracker - changes logo based on current theme

// Immediately execute theme detection and logo setting
(function () {
    // Function to detect if OS prefers dark mode
    function prefersDarkMode() {
        return window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    // Function to determine effective theme 
    function getEffectiveTheme(themeValue) {
        // If theme is set to auto, use OS preference
        if (themeValue === 'auto') {
            return prefersDarkMode() ? 'dark' : 'light';
        }
        // Otherwise use the explicitly set theme
        return themeValue;
    }

    // Set logo src attribute - called both on initial load and when theme changes
    function setLogoSrc() {
        // Use current theme or fallback to auto (which uses OS preference)
        const currentTheme = document.body ? document.body.getAttribute('data-theme') || 'auto' : 'auto';
        const effectiveTheme = getEffectiveTheme(currentTheme);

        // Calculate base path by analyzing current URL path
        // This ensures we get the correct path regardless of navigation depth
        const path = window.location.pathname;
        let logoPath;

        // Function to create the correct logo path based on a base path
        function createLogoPath(basePath) {
            return effectiveTheme === 'dark'
                ? basePath + '_static/img/logo-dark-theme.svg'
                : basePath + '_static/img/logo-light-theme.svg';
        }

        // Check if _static directory exists at the root level (sphinx-autobuild case)
        // We dynamically create an image element to test if the root path works
        const testImg = new Image();
        testImg.onerror = function () {
            // Root _static doesn't exist, try to extract version from path
            const segments = path.split('/').filter(Boolean);
            if (segments.length > 0) {
                // First segment might be a version (in multiversion) or a page (in autobuild)
                // We'll try with it as a version first
                logoPath = createLogoPath('/' + segments[0] + '/');
            } else {
                // Fallback to just root-relative
                logoPath = createLogoPath('/');
            }
            applyLogoPath(logoPath);
        };
        testImg.onload = function () {
            // Root _static exists, use root-relative path (sphinx-autobuild case)
            logoPath = createLogoPath('/');
            applyLogoPath(logoPath);
        };
        // Set src to test if root _static exists
        testImg.src = '/_static/img/logo-' + effectiveTheme + '-theme.svg';

        // Function to apply the logo path to CSS
        function applyLogoPath(logoPath) {
            // Create or reuse a stylesheet with the logo as a CSS rule
            let style = document.getElementById('logo-style');
            if (!style) {
                style = document.createElement('style');
                style.id = 'logo-style';
                document.head.appendChild(style);
            }
            style.textContent = `.sidebar-brand img { content: url("${logoPath}"); }`;
        }

        // Return an empty string initially - actual logo path will be set asynchronously
        return '';
    }

    // Set initial logo - will run even before DOMContentLoaded
    setLogoSrc();

    // Wait for DOM to be loaded to set up observers and event listeners
    document.addEventListener('DOMContentLoaded', function () {
        // Set up a mutation observer to watch for theme changes
        const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                if (mutation.attributeName === 'data-theme') {
                    setLogoSrc();
                }
            });
        });

        // Start observing once the body is available
        if (document.body) {
            observer.observe(document.body, { attributes: true });
        }

        // Listen for OS theme preference changes
        const prefersDarkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        if (prefersDarkModeMediaQuery.addEventListener) {
            prefersDarkModeMediaQuery.addEventListener('change', function () {
                // If current theme is auto, we need to update when OS preference changes
                const currentTheme = document.body.getAttribute('data-theme');
                if (currentTheme === 'auto') {
                    setLogoSrc();
                }
            });
        }
    });
})();