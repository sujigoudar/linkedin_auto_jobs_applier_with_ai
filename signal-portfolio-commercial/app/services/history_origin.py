"""CP-009 "Hypothetical composite labeling", per
spec/docs/03_portfolio_research_and_selection.md: "Record actual,
forward-paper, platform-model and reconstructed histories separately.
No 'all actual' classification merely because an input price came from
a real feed."

The rule this enforces: a composite series (e.g. a portfolio built from
several sleeves' histories) can only be labeled as strong as its
WEAKEST component. One reconstructed or platform-modeled sleeve inside
an otherwise-actual portfolio makes the whole portfolio's label that
weaker one -- never a silent "mostly actual, call it actual."
"""
from __future__ import annotations

import enum
from collections.abc import Sequence


class HistoryOrigin(str, enum.Enum):
    ACTUAL = "ACTUAL"
    FORWARD_PAPER = "FORWARD_PAPER"
    PLATFORM_MODEL = "PLATFORM_MODEL"
    RECONSTRUCTED = "RECONSTRUCTED"


#: Strongest (most verifiable) first -- the composite label is whichever
#: member here has the HIGHEST index among the components' own labels
#: (i.e. the weakest one present), never a stronger label than any
#: individual component actually has.
_STRENGTH_ORDER: tuple[HistoryOrigin, ...] = (
    HistoryOrigin.ACTUAL,
    HistoryOrigin.FORWARD_PAPER,
    HistoryOrigin.PLATFORM_MODEL,
    HistoryOrigin.RECONSTRUCTED,
)


class NoComponentsError(Exception):
    pass


def classify_composite_origin(component_origins: Sequence[HistoryOrigin]) -> HistoryOrigin:
    """The composite's own label -- the weakest label among
    `component_origins`. Refuses an empty sequence outright: a composite
    with no named components has nothing to derive a label from, and
    defaulting to ACTUAL (the strongest label) would be exactly the
    silent-upgrade this function exists to prevent."""
    if not component_origins:
        raise NoComponentsError("cannot classify a composite with no component origins")

    weakest_index = max(_STRENGTH_ORDER.index(origin) for origin in component_origins)
    return _STRENGTH_ORDER[weakest_index]
