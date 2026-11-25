"""Fail the docs build on non-ASCII characters such as em dashes, arrows and
curly quotes, and on double hyphens used as dashes in prose, wherever they
come from: Markdown, docstrings, CLI help or generated reference pages."""

import re
import unicodedata

from docutils import nodes
from sphinx import addnodes
from sphinx.util import logging

logger = logging.getLogger(__name__)

DOUBLE_HYPHEN_DASH = re.compile(r"(?<=\w)--(?=\w)|(?<=\s)--(?=\s)")
CODE_NODES = (nodes.literal, nodes.FixedTextElement, nodes.option_string, addnodes.desc_signature)
SPHINX_FIELD_SEPARATOR = " -- "


def disallowed_characters(text, allowed):
    return [(offset, char) for offset, char in enumerate(text) if ord(char) > 127 and char not in allowed]


def prose_dashes(text):
    return [match.start() for match in DOUBLE_HYPHEN_DASH.finditer(text)]


def in_code(node):
    parent = node.parent
    while parent is not None:
        if isinstance(parent, CODE_NODES):
            return True
        parent = parent.parent
    return False


def use_ascii_field_separators(doctree):
    for node in list(doctree.findall(nodes.Text)):
        if node.astext() == SPHINX_FIELD_SEPARATOR and not in_code(node):
            node.parent.replace(node, nodes.Text(" - "))


def dash_findings(doctree):
    for node in doctree.findall(nodes.Text):
        if prose_dashes(node.astext()) and not in_code(node):
            yield node


def allowed_for(docname, allowed, per_document):
    return allowed + per_document.get(docname, "")


def findings(doctree, allowed):
    for node in doctree.findall(nodes.Text):
        chars = [char for _, char in disallowed_characters(node.astext(), allowed)]
        if chars:
            yield node, list(dict.fromkeys(chars))


def check_doctree(app, doctree, docname):
    use_ascii_field_separators(doctree)
    allowed = allowed_for(docname, app.config.typography_allowed, app.config.typography_allowed_per_document)
    for node, chars in findings(doctree, allowed):
        names = ", ".join(f"U+{ord(char):04X} {unicodedata.name(char, 'UNNAMED')}" for char in chars)
        logger.warning("use ASCII instead of %s", names, location=node.parent, type="typography")
    for node in dash_findings(doctree):
        logger.warning("use ' - ' instead of '--' as a dash in prose", location=node.parent, type="typography")


def setup(app):
    app.add_config_value("typography_allowed", "", "env")
    app.add_config_value("typography_allowed_per_document", {}, "env")
    app.connect("doctree-resolved", check_doctree)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
