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
copyright = "2025, Jumpstarter Contributors"
author = "Jumpstarter Community"

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
html_title = "Jumpstarter Documentation"
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
}

# -- sphinx-multiversion configuration -------------------------------------
# This replaces the custom bash script approach with built-in functionality

# Tags pattern for html_context["versions"]
smv_tag_whitelist = r"^main$|^v(0\.[5-9](\.\d+)|0\.[1-9][0-9]+(\.\d+)|[1-9]\d*\.\d+\.\d+)$"  # Starting from v0.5.0
smv_branch_whitelist = r"^(main|master)$"  # Only include main/master branch
smv_remote_whitelist = None
smv_released_pattern = r"^v[0-9]+\.[0-9]+\.[0-9]+$"  # Tags that are considered releases
smv_outputdir_format = "{ref.name}"  # Directory name format

# Patterns for the versions panel
html_context = {
    "display_lower": True,  # Display lower versions at the bottom of the menu
    "deploy_url": os.getenv("DEPLOY_URL", "http://localhost:8000"),  # Get Netlify URL from environment variable
}
