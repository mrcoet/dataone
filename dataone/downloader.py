"""Dukascopy download and HDF5 persistence."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from tqdm.auto import tqdm

from .instruments import validate_symbol

SIDES = ("bid", "ask")
REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


class DownloadError(RuntimeError):
    """Raised when Dukascopy data cannot be downloaded or validated."""


@dataclass(frozen=True)
class DownloadResult:
    """Summary returned by :func:`download_h5`."""

    path: Path
    symbol: str
    from_date: date
    to_date: date
    rows_bid: int
    rows_ask: int
    downloaded_ranges: tuple[tuple[date, date], ...]


def _as_date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a YYYY-MM-DD string or datetime.date")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"{name} must use the exact YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid calendar date: {value!r}") from exc


def _stored_coverage(path: Path) -> tuple[date, date] | None:
    if not path.exists():
        return None
    try:
        with pd.HDFStore(path, mode="r") as store:
            node = store.get_node("/m1")
            return (
                date.fromisoformat(str(node._v_attrs.from_date)),
                date.fromisoformat(str(node._v_attrs.to_date)),
            )
    except (AttributeError, KeyError, ValueError) as exc:
        raise DownloadError(f"{path} is missing valid m1 coverage metadata") from exc


def _missing_ranges(
    requested_from: date,
    requested_to: date,
    coverage: tuple[date, date] | None,
) -> list[tuple[date, date]]:
    if coverage is None:
        return [(requested_from, requested_to)]

    stored_from, stored_to = coverage
    ranges: list[tuple[date, date]] = []
    if requested_from < stored_from:
        ranges.append((requested_from, stored_from))
    if requested_to > stored_to:
        ranges.append((stored_to, requested_to))
    return ranges


def _download_csv(
    symbol: str,
    side: str,
    start: date,
    end: date,
    destination: Path,
    *,
    npx: str,
    package_version: str,
    retries: int,
    batch_size: int,
    timeout: float | None,
) -> pd.DataFrame:
    output_name = f"{symbol}-{side}-{start}-{end}"
    command = [
        npx,
        "--yes",
        f"dukascopy-node@{package_version}",
        "--instrument",
        symbol,
        "--date-from",
        start.isoformat(),
        "--date-to",
        end.isoformat(),
        "--timeframe",
        "m1",
        "--price-type",
        side,
        "--utc-offset",
        "0",
        "--volumes",
        "--volume-units",
        "millions",
        "--flats",
        "--format",
        "csv",
        "--directory",
        str(destination),
        "--file-name",
        output_name,
        "--batch-size",
        str(batch_size),
        "--retries",
        str(retries),
        "--retry-on-empty",
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(
            f"dukascopy-node timed out for {symbol} {side} {start}..{end} "
            f"after {timeout:g} seconds"
        ) from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise DownloadError(
            f"dukascopy-node failed for {symbol} {side} {start}..{end}: {detail}"
        )

    csv_path = destination / f"{output_name}.csv"
    if not csv_path.exists():
        candidates = list(destination.glob(f"{output_name}*.csv"))
        if len(candidates) != 1:
            raise DownloadError(
                f"download completed but no CSV was created in {destination}"
            )
        csv_path = candidates[0]

    frame = pd.read_csv(csv_path)
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise DownloadError(f"downloaded CSV is missing columns: {sorted(missing)}")
    frame = frame.loc[:, REQUIRED_COLUMNS].copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="raise").astype(
        "int64"
    )
    for column in REQUIRED_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def _write_frames(
    path: Path,
    frames: Iterable[tuple[str, pd.DataFrame]],
    *,
    symbol: str,
    coverage_from: date,
    coverage_to: date,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for side, frame in frames:
        frame.to_hdf(
            path,
            key=f"m1/{side}",
            mode="a" if path.exists() else "w",
            format="table",
            append=path.exists() and f"/m1/{side}" in _keys(path),
            index=False,
        )

    with pd.HDFStore(path, mode="a") as store:
        attrs = store.get_node("/m1")._v_attrs
        attrs.from_date = coverage_from.isoformat()
        attrs.to_date = coverage_to.isoformat()
        attrs.symbol = symbol
        attrs.timeframe = "m1"
        attrs.schema_version = 1


def _keys(path: Path) -> set[str]:
    with pd.HDFStore(path, mode="r") as store:
        return set(store.keys())


def download_h5(
    symbol: str,
    from_date: str | date,
    to_date: str | date,
    output: str | Path | None = None,
    *,
    npx: str = "npx",
    dukascopy_node_version: str = "1.51.0",
    retries: int = 5,
    batch_size: int = 10,
    timeout: float | None = 900,
    progress: bool = True,
) -> DownloadResult:
    """Download one-minute BID and ASK candles into a DataOne HDF5 file.

    Dates describe a half-open interval: ``from_date <= timestamp < to_date``.
    Calling the function again safely extends the stored coverage.
    Progress shows validated BID/ASK downloads and HDF5 writes as completed
    stages. Set ``progress=False`` to suppress the progress bar.
    """

    normalised_symbol = validate_symbol(symbol)
    start = _as_date(from_date, "from_date")
    end = _as_date(to_date, "to_date")
    if start >= end:
        raise ValueError("from_date must be earlier than to_date")
    if retries < 0 or batch_size < 1 or (timeout is not None and timeout <= 0):
        raise ValueError("retries must be >= 0, batch_size >= 1, and timeout > 0")
    target = (
        Path(output) if output is not None else Path("duka") / f"{normalised_symbol}.h5"
    )
    target = target.expanduser()
    coverage = _stored_coverage(target)
    ranges = _missing_ranges(start, end, coverage)
    if ranges and shutil.which(npx) is None:
        raise DownloadError(
            "npx was not found. Install Node.js 18+ to use DataOne downloads; "
            "conversion and fully covered .h5 files do not require Node.js."
        )

    rows = {"bid": 0, "ask": 0}
    with tqdm(
        total=len(ranges) * 3,
        desc=f"{normalised_symbol.upper()} download",
        unit="stage",
        disable=not progress or not ranges,
        dynamic_ncols=True,
    ) as bar:
        for range_start, range_end in ranges:
            with tempfile.TemporaryDirectory(prefix="dataone-") as tmp:
                temp_path = Path(tmp)
                downloaded: list[tuple[str, pd.DataFrame]] = []
                for side in SIDES:
                    bar.set_postfix_str(f"{side.upper()} {range_start} → {range_end}")
                    frame = _download_csv(
                        normalised_symbol,
                        side,
                        range_start,
                        range_end,
                        temp_path,
                        npx=npx,
                        package_version=dukascopy_node_version,
                        retries=retries,
                        batch_size=batch_size,
                        timeout=timeout,
                    )
                    rows[side] += len(frame)
                    downloaded.append((side, frame))
                    bar.update(1)

                bar.set_postfix_str("Saving HDF5")
                current_from = (
                    min(range_start, coverage[0]) if coverage else range_start
                )
                current_to = max(range_end, coverage[1]) if coverage else range_end
                _write_frames(
                    target,
                    downloaded,
                    symbol=normalised_symbol,
                    coverage_from=current_from,
                    coverage_to=current_to,
                )
                coverage = (current_from, current_to)
                bar.update(1)
        if ranges:
            bar.set_postfix_str("Complete")

    final_from, final_to = coverage if coverage else (start, end)
    return DownloadResult(
        path=target.resolve(),
        symbol=normalised_symbol,
        from_date=final_from,
        to_date=final_to,
        rows_bid=rows["bid"],
        rows_ask=rows["ask"],
        downloaded_ranges=tuple(ranges),
    )
