# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys

import requests

sys.path.insert(0, os.path.abspath("../.."))

project = "jumpstarter"
copyright = "2025, Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer"
author = "Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer"

controller_version = requests.get(
    "https://quay.io/api/v1/repository/jumpstarter-dev/helm/jumpstarter/tag/", params={"limit": 1}
).json()["tags"][0]["name"]

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

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_title = "Jumpstarter"
html_logo = "_static/img/logo-light-theme.svg"
html_favicon = "_static/img/favicon.png"
html_show_sphinx = False

myst_heading_anchors = 3
myst_enable_extensions = [
    "substitution",
]
myst_substitutions = {
    "requires_python": ">=3.11",
    "version": "latest",
    "controller_version": controller_version,
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
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/jumpstarter-dev",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 16 16">
                    <path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"></path>
                </svg>
            """,
            "class": "",
        },
    ],
}

# -- sphinx-multiversion configuration -------------------------------------
# This replaces the custom bash script approach with built-in functionality

# Tags pattern for html_context["versions"]
smv_tag_whitelist = r"^v0\.5\.0|^main"  # Starting from v0.5.0
smv_branch_whitelist = r"^(main|master)$"  # Only include main/master branch
smv_remote_whitelist = None
smv_released_pattern = r"^v[0-9]+\.[0-9]+\.[0-9]+$"  # Tags that are considered releases
smv_outputdir_format = "{ref.name}"  # Directory name format

# Patterns for the versions panel
html_context = {
    "display_lower": True,  # Display lower versions at the bottom of the menu
}
