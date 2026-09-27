"""Classifies MetaApi CopyFactory close-only modes, per
spec/docs/06_platform_adapters.md's "MetaApi CopyFactory and other
channels" section: "Map close-only carefully: by-position, by-symbol
and immediately are not equivalent. A 'by-symbol' mode can allow new
positions in an already held symbol and must not satisfy a strict
no-new-position gate without further evidence."

This module does not call CopyFactory -- it only decides, given a mode
string a caller read back from that API, whether it is strong enough
evidence to satisfy a caller's "no new positions can open" requirement.
"""
from __future__ import annotations

import enum


class CloseOnlyMode(str, enum.Enum):
    BY_POSITION = "by-position"
    BY_SYMBOL = "by-symbol"
    IMMEDIATELY = "immediately"


class UnknownCloseOnlyModeError(Exception):
    pass


def parse_close_only_mode(raw: str) -> CloseOnlyMode:
    try:
        return CloseOnlyMode(raw)
    except ValueError as exc:
        raise UnknownCloseOnlyModeError(
            f"{raw!r} is not a recognized CopyFactory close-only mode -- refusing to guess its semantics"
        ) from exc


#: "by-symbol" is explicitly named in the spec as the one mode that can
#: still allow a NEW position to open in an already-held symbol, so it
#: alone does not satisfy a strict "no new positions" requirement.
_SATISFIES_STRICT_NO_NEW_POSITIONS: frozenset[CloseOnlyMode] = frozenset(
    {CloseOnlyMode.BY_POSITION, CloseOnlyMode.IMMEDIATELY}
)


def satisfies_strict_no_new_position_gate(mode: CloseOnlyMode) -> bool:
    """True only for modes the spec treats as real evidence that no new
    position can open -- "by-symbol" always returns False here, even
    though it does stop SOME activity, because it is not equivalent
    evidence and must not be silently treated as if it were."""
    return mode in _SATISFIES_STRICT_NO_NEW_POSITIONS
