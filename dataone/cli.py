"""Command-line interface for DataOne."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .downloader import DownloadError, download_h5
from .eda import create_eda_report
from .instruments import list_symbols
from .pipeline import H1Options, export_h1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataone",
        description="Download Dukascopy M1 candles and create quality-checked H1 CSV files.",
    )
    parser.add_argument("--version", action="version", version=f"dataone {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    download = commands.add_parser("download", help="download BID/ASK M1 data to HDF5")
    download.add_argument("symbol", help="Dukascopy instrument id, for example eurusd")
    download.add_argument(
        "--from", dest="from_date", required=True, help="inclusive YYYY-MM-DD"
    )
    download.add_argument(
        "--to", dest="to_date", required=True, help="exclusive YYYY-MM-DD"
    )
    download.add_argument("-o", "--output", type=Path, help="output .h5 path")
    download.add_argument("--retries", type=int, default=20)
    download.add_argument(
        "--retry-pause",
        type=int,
        default=2000,
        help="milliseconds between retries (default: 2000)",
    )
    download.add_argument("--batch-size", type=int, default=10)
    download.add_argument(
        "--chunk-days",
        type=int,
        default=365,
        help="days per HDF5 checkpoint (default: 365)",
    )
    download.add_argument(
        "--no-progress", action="store_true", help="disable the download progress bar"
    )
    download.add_argument(
        "--timeout", type=float, default=900, help="seconds per BID/ASK request"
    )

    h1 = commands.add_parser("h1", help="convert a DataOne .h5 file to final H1 CSV")
    h1.add_argument("source", type=Path, help="input .h5 path")
    h1.add_argument("-o", "--output", type=Path, default=Path("h1.csv"))
    h1.add_argument(
        "--market",
        choices=("auto", "fx", "metals", "crypto", "generic"),
        default="auto",
    )
    h1.add_argument("--missing-percentage", type=int, default=80)
    h1.add_argument(
        "--no-clean", action="store_true", help="retain holiday and incomplete weeks"
    )

    eda = commands.add_parser(
        "eda", help="create holiday, worst-period, quality, and cleaning plots"
    )
    eda.add_argument("source", type=Path, help="input .h5 path")
    eda.add_argument(
        "-o", "--output-dir", type=Path, default=Path("dataone-report")
    )
    eda.add_argument(
        "--market",
        choices=("auto", "fx", "metals", "crypto", "generic"),
        default="auto",
    )
    eda.add_argument("--missing-percentage", type=int, default=80)
    eda.add_argument(
        "--holiday", help="specific holiday name; defaults to the latest available"
    )
    eda.add_argument(
        "--worst-hour-metric", default="total_missing",
        help="hourly quality column used to select the worst hour",
    )
    eda.add_argument("--dpi", type=int, default=140)

    symbols = commands.add_parser("symbols", help="list supported Dukascopy symbols")
    symbols.add_argument(
        "search", nargs="?", help="optional case-insensitive id filter"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            result = download_h5(
                args.symbol,
                args.from_date,
                args.to_date,
                args.output,
                retries=args.retries,
                retry_pause=args.retry_pause,
                batch_size=args.batch_size,
                chunk_days=args.chunk_days,
                timeout=args.timeout,
                progress=not args.no_progress,
            )
            print(
                f"{result.path} ({result.rows_bid:,} BID rows, "
                f"{result.rows_ask:,} ASK rows downloaded)"
            )
        elif args.command == "h1":
            options = H1Options(
                market=args.market,
                clean=not args.no_clean,
                missing_percentage=args.missing_percentage,
            )
            destination = export_h1(args.source, args.output, options)
            print(destination)
        elif args.command == "eda":
            options = H1Options(
                market=args.market,
                missing_percentage=args.missing_percentage,
            )
            report = create_eda_report(
                args.source,
                args.output_dir,
                options,
                holiday=args.holiday,
                worst_hour_metric=args.worst_hour_metric,
                dpi=args.dpi,
            )
            print(report.output_dir)
        else:
            matches = list_symbols(args.search)
            print("\n".join(matches))
            if args.search and not matches:
                return 1
    except (DownloadError, ImportError, OSError, ValueError) as exc:
        parser.exit(2, f"dataone: error: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
