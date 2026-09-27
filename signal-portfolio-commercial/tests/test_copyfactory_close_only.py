"""app/services/copyfactory_close_only.py -- pure function tests, no
database needed."""
import pytest

from app.services.copyfactory_close_only import (
    CloseOnlyMode,
    UnknownCloseOnlyModeError,
    parse_close_only_mode,
    satisfies_strict_no_new_position_gate,
)


@pytest.mark.parametrize("raw,expected", [
    ("by-position", CloseOnlyMode.BY_POSITION),
    ("by-symbol", CloseOnlyMode.BY_SYMBOL),
    ("immediately", CloseOnlyMode.IMMEDIATELY),
])
def test_parses_every_documented_mode(raw, expected):
    assert parse_close_only_mode(raw) == expected


def test_an_unrecognized_mode_is_rejected_not_guessed():
    with pytest.raises(UnknownCloseOnlyModeError):
        parse_close_only_mode("some-new-mode-not-in-the-docs")


def test_by_position_satisfies_the_strict_gate():
    assert satisfies_strict_no_new_position_gate(CloseOnlyMode.BY_POSITION) is True


def test_immediately_satisfies_the_strict_gate():
    assert satisfies_strict_no_new_position_gate(CloseOnlyMode.IMMEDIATELY) is True


def test_by_symbol_does_not_satisfy_the_strict_gate():
    """The spec's own warning: "A 'by-symbol' mode can allow new
    positions in an already held symbol and must not satisfy a strict
    no-new-position gate without further evidence.\""""
    assert satisfies_strict_no_new_position_gate(CloseOnlyMode.BY_SYMBOL) is False
