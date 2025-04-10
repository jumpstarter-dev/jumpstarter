// Function to handle tab switching
function openTab(evt, tabName) {
    // Get the parent tab container
    const tabButton = evt.currentTarget;
    const tabContainer = tabButton.closest('.tab');

    // Find all tab content related to this tab group
    // We'll look for siblings of the tab container with class tabcontent
    const tabGroup = tabContainer.parentNode;
    const tabContents = tabGroup.querySelectorAll('.tabcontent');

    // Hide all tab contents in this group
    tabContents.forEach(content => {
        content.style.display = "none";
    });

    // Remove active class from all buttons in this tab container
    const tabButtons = tabContainer.querySelectorAll('.tablinks');
    tabButtons.forEach(button => {
        button.className = button.className.replace(" active", "");
    });

    // Show the selected tab content and add active class to the button
    document.getElementById(tabName).style.display = "block";
    tabButton.className += " active";
}

// Initialize tabs on page load
document.addEventListener('DOMContentLoaded', function () {
    // Process each tab container
    document.querySelectorAll('.tab').forEach(tabContainer => {
        // Hide all tab content except those marked as initially visible
        const tabGroup = tabContainer.parentNode;
        const tabContents = tabGroup.querySelectorAll('.tabcontent');

        // Find the active button in this tab container
        const activeButton = tabContainer.querySelector('.tablinks.active');

        if (activeButton) {
            // Trigger click on the active button to set up initial state
            activeButton.click();
        } else {
            // If no active button is defined, hide all tab contents
            tabContents.forEach(content => {
                if (!content.hasAttribute("style") || content.style.display !== "block") {
                    content.style.display = "none";
                }
            });
        }
    });
}); 