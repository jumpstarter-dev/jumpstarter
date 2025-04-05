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
copyright = "2024, Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer"
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
]

templates_path = ["_templates"]
exclude_patterns = []

mermaid_version = "10.9.1"

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_title = "JumpstarterDocumentation"
html_logo = "_static/img/logo-light-theme.svg"
html_favicon = "_static/img/favicon.png"

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

html_js_files = ["js/version_array.js", "js/versions.js", "js/theme-mermaid.js", "js/tabs.js"]
html_static_path = ["_static"]
html_css_files = [
    "css/versions.css",
    "css/tabs.css",
    "css/custom.css",
]
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/ethical-ads.html",
        "sidebar/scroll-end.html",
        "sidebar/versions.html",
    ]
}
