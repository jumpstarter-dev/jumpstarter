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
        let basePath = '';

        // Extract the version part (e.g., 'main' or 'v0.5.0')
        const match = path.match(/^\/([^\/]+)/);
        if (match && match[1]) {
            basePath = '/' + match[1] + '/';
        }

        // Logo path based on effective theme with correct base path
        const logoPath = effectiveTheme === 'dark'
            ? basePath + '_static/img/logo-dark-theme.svg'
            : basePath + '_static/img/logo-light-theme.svg';

        // Create or reuse a stylesheet with the logo as a CSS rule to be applied immediately
        let style = document.getElementById('logo-style');
        if (!style) {
            style = document.createElement('style');
            style.id = 'logo-style';
            document.head.appendChild(style);
        }
        style.textContent = `.sidebar-brand img { content: url("${logoPath}"); }`;

        return logoPath;
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