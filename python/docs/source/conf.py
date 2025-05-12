# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import asyncio
import os
import sys

from jumpstarter_cli_admin.controller import get_latest_compatible_controller_version

sys.path.insert(0, os.path.abspath("../.."))

project = "jumpstarter"
copyright = "2025, Jumpstarter Contributors"
author = "Jumpstarter Contributors"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinxcontrib.mermaid",
    "sphinxcontrib.programoutput",
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx_click",
    "sphinx_substitution_extensions",
    "sphinx_multiversion",
]

templates_path = ["_templates"]
exclude_patterns = []

mermaid_version = "10.9.1"

suppress_warnings = [
    "ref.class",  # suppress unresolved Python class references (external references
    # are warnings otherwise)
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_title = "Jumpstarter Documentation"
html_logo = "_static/img/logo-light-theme.svg"
html_favicon = "_static/img/favicon.png"
html_show_sphinx = False


def get_controller_version():
    name = os.getenv("SPHINX_MULTIVERSION_NAME")
    if name == "main" or name is None:
        version = None
    elif name.startswith("release-"):
        version = name.removeprefix("release-")
    else:
        version = None

    return asyncio.run(get_latest_compatible_controller_version(client_version=version))


def get_index_url():
    name = os.getenv("SPHINX_MULTIVERSION_NAME")
    if name is None:
        return "https://pkg.jumpstarter.dev/simple"
    else:
        return "https://pkg.jumpstarter.dev/{}/simple".format(name)


myst_heading_anchors = 3
myst_enable_extensions = [
    "substitution",
]
myst_substitutions = {
    "requires_python": ">=3.11",
    "version": "latest",
    "controller_version": get_controller_version(),
    "index_url": get_index_url(),
}

doctest_test_doctest_blocks = ""

html_js_files = ["js/tabs.js", "js/theme-toggle.js"]
html_static_path = ["_static"]
html_css_files = [
    "css/tabs.css",
    "css/custom.css",
]
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
        "sidebar/versions.html",
    ]
}
html_theme_options = {
    "sidebar_hide_name": True,
    "top_of_page_button": "edit",
}

# -- sphinx-multiversion configuration -------------------------------------
# This replaces the custom bash script approach with built-in functionality

# Tags pattern for html_context["versions"]
smv_tag_whitelist = r"$^"  # Ignore all tags
smv_branch_whitelist = r"^(main|release-\d+\.\d+)$"  # Include all release branches and main
smv_remote_whitelist = r"^(origin|upstream)$"  # Include branches from origin and upstream
smv_prefer_remote_refs = True
# smv_released_pattern = r"^v[0-9]+\.[0-9]+\.[0-9]+$"  # Tags that are considered releases
smv_outputdir_format = "{ref.name}"  # Directory name format

# Ensure static files are copied to all versions
smv_static_files = [
    "_static/**",
    "_templates/**",
]

# Ensure RST directives are processed
smv_include_patterns = [
    "*.md",
    "*.rst",
    "*.txt",
]

# Patterns for the versions panel
html_context = {
    "display_lower": True,  # Display lower versions at the bottom of the menu
    "deploy_url": os.getenv("DEPLOY_URL", "https://docs.jumpstarter.dev"),  # Get Netlify URL from environment variable
}
