# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'jumpstarter'
copyright = '2024, Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer'
author = 'Miguel Angel Ajo Pelayo, Nick Cao, Kirk Brauer'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinxcontrib.mermaid',
    'myst_parser'
]

templates_path = ['_templates']
exclude_patterns = []

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown'
}

mermaid_version = '10.9.1'

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
