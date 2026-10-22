"""Pinned Dukascopy instrument catalogue and symbol validation."""

from __future__ import annotations

import re
from difflib import get_close_matches
from functools import cache
from importlib.resources import files


class UnsupportedSymbolError(ValueError):
    """Raised when a symbol is absent from the pinned Dukascopy catalogue."""


@cache
def _catalogue() -> tuple[str, ...]:
    content = files("dataone").joinpath("instruments.txt").read_text(encoding="utf-8")
    symbols = tuple(line.strip() for line in content.splitlines() if line.strip())
    if not symbols:
        raise RuntimeError("the packaged Dukascopy instrument catalogue is empty")
    return symbols


@cache
def _catalogue_set() -> frozenset[str]:
    return frozenset(_catalogue())


def normalize_symbol(symbol: str) -> str:
    """Normalize supported display forms to a dukascopy-node instrument id."""

    if not isinstance(symbol, str):
        raise TypeError("symbol must be a string")
    value = symbol.strip().lower()
    if not value or re.fullmatch(r"[a-z0-9./-]+", value) is None:
        raise ValueError("symbol may contain only letters, numbers, '.', '/', and '-'")
    return value.translate(str.maketrans("", "", "./-"))


def is_supported_symbol(symbol: str) -> bool:
    """Return whether *symbol* exists in the pinned Dukascopy catalogue."""

    try:
        return normalize_symbol(symbol) in _catalogue_set()
    except (TypeError, ValueError):
        return False


def validate_symbol(symbol: str) -> str:
    """Return the normalized id or raise :class:`UnsupportedSymbolError`."""

    normalized = normalize_symbol(symbol)
    if normalized in _catalogue_set():
        return normalized
    close = get_close_matches(normalized, _catalogue(), n=5, cutoff=0.6)
    hint = f" Closest supported symbols: {', '.join(close)}." if close else ""
    raise UnsupportedSymbolError(
        f"Dukascopy does not support symbol {symbol!r} in the pinned catalogue."
        f"{hint} Use dataone.list_symbols() or `dataone symbols`."
    )


def list_symbols(search: str | None = None) -> tuple[str, ...]:
    """Return all supported ids, optionally filtered by a case-insensitive term."""

    symbols = _catalogue()
    if search is None:
        return symbols
    if not isinstance(search, str):
        raise TypeError("search must be a string or None")
    term = search.strip().lower()
    return tuple(symbol for symbol in symbols if term in symbol)
