from __future__ import annotations

import pytest

from dataone import (
    UnsupportedSymbolError,
    is_supported_symbol,
    list_symbols,
    normalize_symbol,
    validate_symbol,
)


def test_catalogue_contains_all_pinned_symbols() -> None:
    symbols = list_symbols()
    assert len(symbols) == 1499
    assert symbols == tuple(sorted(symbols))
    assert {"eurusd", "xauusd", "btcusd", "aaplususd"} <= set(symbols)


@pytest.mark.parametrize(
    ("entered", "normalized"),
    [
        ("EURUSD", "eurusd"),
        ("EUR/USD", "eurusd"),
        ("EUR-USD", "eurusd"),
        ("AAPL.US/USD", "aaplususd"),
    ],
)
def test_supported_display_forms(entered: str, normalized: str) -> None:
    assert normalize_symbol(entered) == normalized
    assert validate_symbol(entered) == normalized
    assert is_supported_symbol(entered)


def test_unsupported_symbol_has_suggestions() -> None:
    with pytest.raises(UnsupportedSymbolError, match="eurusd"):
        validate_symbol("eurusx")
    assert not is_supported_symbol("eurusx")


def test_list_symbols_can_be_searched() -> None:
    matches = list_symbols("btcusd")
    assert matches == ("btcusd",)
