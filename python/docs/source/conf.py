# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys

sys.path.insert(0, os.path.abspath("../.."))

project = "jumpstarter"
copyright = "2024, Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer"
author = "Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinxcontrib.mermaid",
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
# html_static_path = ["_static"]
html_extra_path = ["extra"]
html_title = "Jumpstarter Docs"

myst_heading_anchors = 3
myst_enable_extensions = [
    "substitution",
]
myst_substitutions = {
    "requires_python": ">=3.11",
    "version": "0.5.0",
    "controller_version": "0.5.0",
}
