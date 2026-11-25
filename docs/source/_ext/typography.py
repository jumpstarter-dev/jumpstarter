"""Fail the docs build on non-ASCII characters such as em dashes, arrows and
curly quotes, wherever they come from: Markdown, docstrings, CLI help or
generated reference pages."""

import unicodedata

from docutils import nodes
from sphinx.util import logging

logger = logging.getLogger(__name__)


def disallowed_characters(text, allowed):
    return [(offset, char) for offset, char in enumerate(text) if ord(char) > 127 and char not in allowed]


def allowed_for(docname, allowed, per_document):
    return allowed + per_document.get(docname, "")


def findings(doctree, allowed):
    for node in doctree.findall(nodes.Text):
        chars = [char for _, char in disallowed_characters(node.astext(), allowed)]
        if chars:
            yield node, list(dict.fromkeys(chars))


def check_doctree(app, doctree, docname):
    allowed = allowed_for(docname, app.config.typography_allowed, app.config.typography_allowed_per_document)
    for node, chars in findings(doctree, allowed):
        names = ", ".join(f"U+{ord(char):04X} {unicodedata.name(char, 'UNNAMED')}" for char in chars)
        logger.warning("use ASCII instead of %s", names, location=node.parent, type="typography")


def setup(app):
    app.add_config_value("typography_allowed", "", "env")
    app.add_config_value("typography_allowed_per_document", {}, "env")
    app.connect("doctree-resolved", check_doctree)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
