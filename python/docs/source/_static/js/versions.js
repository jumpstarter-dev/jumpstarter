var url = window.location.href;

// extract the path without the host from the url
var path = url.replace(window.location.origin, '');

// extract the version from the path, being our version the first part of the path i.e. /v1.0.0/...
var version = path.split('/')[1];

// extract the rest of the path
var rest = path.split('/').slice(2).join('/');


// check if version is in version_array
if (version_array.includes(version)) {
    // ok, we are running on the site compiled with all versions, we can render the version selector
    versions_html = '';
    // iterate version_array and render the version selector
    for (var i = 0; i < version_array.length; i++) {
        v = version_array[i];
        // join v with rest
        var new_url = "/" + v + "/" + rest;
        if (v == version) {
            // add the current version as a strong item
            versions_html += '<li><strong>' + v + '</strong></li>';
        } else {
            // add ul item with the version and a link to the new url
            versions_html += '<li><a href="' + new_url + '">' + v + '</a></li>';
        }

        // set the new html into doc_versions_list
        const docVersionsList = document.getElementById("doc_versions_list");
        if (docVersionsList) {
            docVersionsList.innerHTML = versions_html;
        } else {
            console.error("Element with ID 'doc_versions_list' not found");
        }

    }

} else {
    // we are not, just ignore it
    console.log("no version detected");
}