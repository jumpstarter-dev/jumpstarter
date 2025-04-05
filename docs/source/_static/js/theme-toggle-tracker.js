// Theme toggle tracker - changes logo based on current theme
document.addEventListener('DOMContentLoaded', function () {
    // Wait for the theme toggle button to be available in the DOM
    const checkForToggle = setInterval(function () {
        const themeToggle = document.querySelector('.theme-toggle');
        if (themeToggle) {
            clearInterval(checkForToggle);

            // Function to update logo based on theme
            function updateLogo(theme) {
                const logo = document.querySelector('.sidebar-brand img');
                if (logo) {
                    const logoPath = theme === 'dark'
                        ? '_static/img/logo-dark-theme.svg'
                        : '_static/img/logo-light-theme.svg';
                    logo.setAttribute('src', logoPath);
                    console.log('Theme changed to:', theme);
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

            // Set initial logo based on current theme
            const initialTheme = document.body.getAttribute('data-theme');
            updateLogo(initialTheme);
        }
    }, 500);
});