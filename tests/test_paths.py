import pytest
from elscione_dl.paths import sanitise


def test_sanitise_strips_illegal_chars():
    assert sanitise('Title: "sub"') == "Title_ _sub_"


def test_sanitise_apostrophe_preserved():
    # Apostrophe is legal on Windows
    name = "I'm in Love With the Villainess"
    assert sanitise(name) == name


def test_sanitise_trailing_dots():
    assert sanitise("My Title...") == "My Title"


def test_sanitise_trailing_spaces():
    assert sanitise("My Title   ") == "My Title"


def test_sanitise_forward_slash():
    assert sanitise("A/B") == "A_B"


def test_sanitise_empty_result():
    # each illegal char becomes '_'; trailing underscore trim only applies to dots/spaces
    assert sanitise(":::") == "___"


def test_sanitise_question_mark():
    assert sanitise("What?") == "What_"
