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
    "sphinx_copybutton",
    "sphinx_inline_tabs"
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

html_js_files = ["js/theme-toggle.js"]
html_static_path = ["_static"]
html_css_files = ["css/custom.css"]
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}
html_theme_options = {
    "sidebar_hide_name": True,
    "top_of_page_button": "edit",
}

# sphinx-copybutton config
copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d*\]: | {2,5}\.\.\.: | {5,8}: "
copybutton_prompt_is_regexp = True
copybutton_only_copy_prompt_lines = True
copybutton_line_continuation_character = "\\"
