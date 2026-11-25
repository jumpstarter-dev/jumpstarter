from docutils.core import publish_doctree
from typography import allowed_for, disallowed_characters, findings


def test_plain_ascii_passes():
    assert disallowed_characters("jmp shell --lease demo", allowed="") == []


def test_reports_an_em_dash_with_its_offset():
    assert disallowed_characters("lease \u2014 release", allowed="") == [(6, "\u2014")]


def test_allowed_characters_pass():
    assert disallowed_characters("\u26a1 board \u27a4", allowed="\u26a1\u27a4") == []


def test_findings_point_at_the_text_that_holds_the_character():
    doctree = publish_doctree("Lease and release.\n\nlease \u2014 release \u2014 again\n")
    [(node, chars)] = findings(doctree, allowed="")
    assert node.astext() == "lease \u2014 release \u2014 again"
    assert chars == ["\u2014"]


def test_code_blocks_are_checked_too():
    doctree = publish_doctree("::\n\n    a \u2192 b\n")
    [(_, chars)] = findings(doctree, allowed="")
    assert chars == ["\u2192"]


def test_a_document_can_allow_extra_characters():
    per_document = {"index": "\U0001f9ea"}
    assert allowed_for("index", "\u26a1", per_document) == "\u26a1\U0001f9ea"
    assert allowed_for("glossary", "\u26a1", per_document) == "\u26a1"
