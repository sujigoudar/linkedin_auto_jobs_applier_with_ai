"""app/services/history_origin.py -- pure function tests, no database
needed."""
import pytest

from app.services.history_origin import HistoryOrigin, NoComponentsError, classify_composite_origin


def test_all_actual_components_yield_an_actual_composite():
    result = classify_composite_origin([HistoryOrigin.ACTUAL, HistoryOrigin.ACTUAL])
    assert result == HistoryOrigin.ACTUAL


def test_one_reconstructed_component_makes_the_whole_composite_reconstructed():
    """The spec's own rule: no 'all actual' classification merely
    because most inputs came from a real feed."""
    result = classify_composite_origin([HistoryOrigin.ACTUAL, HistoryOrigin.ACTUAL, HistoryOrigin.RECONSTRUCTED])
    assert result == HistoryOrigin.RECONSTRUCTED


def test_the_composite_is_never_stronger_than_its_weakest_component():
    result = classify_composite_origin([HistoryOrigin.FORWARD_PAPER, HistoryOrigin.PLATFORM_MODEL])
    assert result == HistoryOrigin.PLATFORM_MODEL


def test_a_single_component_composite_takes_that_components_label():
    assert classify_composite_origin([HistoryOrigin.FORWARD_PAPER]) == HistoryOrigin.FORWARD_PAPER


def test_an_empty_component_list_is_rejected_not_defaulted_to_actual():
    with pytest.raises(NoComponentsError):
        classify_composite_origin([])
