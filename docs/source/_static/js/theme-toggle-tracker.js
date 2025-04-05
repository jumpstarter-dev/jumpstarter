// Theme toggle tracker - changes logo based on current theme
document.addEventListener('DOMContentLoaded', function () {
    // Wait for the theme toggle button to be available in the DOM
    const checkForToggle = setInterval(function () {
        const themeToggle = document.querySelector('.theme-toggle');
        if (themeToggle) {
            clearInterval(checkForToggle);

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

            // Function to update logo based on theme
            function updateLogo(theme) {
                const logo = document.querySelector('.sidebar-brand img');
                if (logo) {
                    // Get effective theme (resolving auto to light/dark)
                    const effectiveTheme = getEffectiveTheme(theme);

                    const logoPath = effectiveTheme === 'dark'
                        ? '/_static/img/logo-dark-theme.svg'
                        : '/_static/img/logo-light-theme.svg';

                    logo.setAttribute('src', logoPath);
                    console.log('Theme changed to:', theme,
                        theme === 'auto' ? `(using ${effectiveTheme} based on OS)` : '');
                }
            }

            // Set up a mutation observer to watch for theme changes
            const observer = new MutationObserver(function (mutations) {
                mutations.forEach(function (mutation) {
                    if (mutation.attributeName === 'data-theme') {
                        const newTheme = document.body.getAttribute('data-theme');
                        updateLogo(newTheme);
                    }
                });
            });

            // Start observing the body element for data-theme attribute changes
            observer.observe(document.body, { attributes: true });

            // Listen for OS theme preference changes if available
            const prefersDarkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            if (prefersDarkModeMediaQuery.addEventListener) {
                prefersDarkModeMediaQuery.addEventListener('change', function () {
                    // If current theme is auto, we need to update when OS preference changes
                    const currentTheme = document.body.getAttribute('data-theme');
                    if (currentTheme === 'auto') {
                        updateLogo('auto');
                    }
                });
            }

            // Set initial logo based on current theme
            const initialTheme = document.body.getAttribute('data-theme');
            updateLogo(initialTheme);
        }
    }, 500);
});