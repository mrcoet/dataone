"""Public API for DataOne."""

from .downloader import DownloadError, DownloadResult, download_h5
from .eda import (
    EDAReport,
    create_eda_report,
    plot_before_after,
    plot_holiday_sessions,
    plot_quality_eda,
    plot_worst_day,
    plot_worst_hour,
)
from .instruments import (
    UnsupportedSymbolError,
    is_supported_symbol,
    list_symbols,
    normalize_symbol,
    validate_symbol,
)
from .pipeline import H1Options, build_h1, export_h1, read_h5

__all__ = [
    "DownloadError",
    "DownloadResult",
    "EDAReport",
    "H1Options",
    "UnsupportedSymbolError",
    "build_h1",
    "create_eda_report",
    "download_h5",
    "export_h1",
    "is_supported_symbol",
    "list_symbols",
    "normalize_symbol",
    "plot_before_after",
    "plot_holiday_sessions",
    "plot_quality_eda",
    "plot_worst_day",
    "plot_worst_hour",
    "read_h5",
    "validate_symbol",
]

__version__ = "0.1.0"
