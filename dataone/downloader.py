"""Dukascopy download and HDF5 persistence."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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


def _chunk_ranges(
    ranges: Iterable[tuple[date, date]],
    coverage: tuple[date, date] | None,
    chunk_days: int,
) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    step = timedelta(days=chunk_days)
    for range_start, range_end in ranges:
        range_chunks: list[tuple[date, date]] = []
        chunk_start = range_start
        while chunk_start < range_end:
            chunk_end = min(chunk_start + step, range_end)
            range_chunks.append((chunk_start, chunk_end))
            chunk_start = chunk_end

        # A missing prefix must grow backward from the stored boundary. This
        # keeps coverage continuous if the process stops between checkpoints.
        if coverage is not None and range_end == coverage[0]:
            range_chunks.reverse()
        chunks.extend(range_chunks)
    return chunks


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
    retry_pause: int,
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
        "--retry-pause",
        str(retry_pause),
        "--retry-on-empty",
        "--no-fail-after-retries",
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

    try:
        frame = pd.read_csv(csv_path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(
            {
                column: pd.Series(
                    dtype="int64" if column == "timestamp" else "float64"
                )
                for column in REQUIRED_COLUMNS
            }
        )
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


def _add_null_candles(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Represent every absent M1 timestamp with a null OHLCV candle."""

    expected = pd.date_range(start, end, freq="min", inclusive="left", tz="UTC")
    expected_ms = expected.as_unit("ms").asi8
    result = (
        frame.drop_duplicates("timestamp", keep="last")
        .set_index("timestamp")
        .reindex(expected_ms)
        .rename_axis("timestamp")
        .reset_index()
    )
    result["timestamp"] = result["timestamp"].astype("int64")
    for column in REQUIRED_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype(
            "float64"
        )
    return result.loc[:, REQUIRED_COLUMNS]


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
        key = f"m1/{side}"
        stored_keys = _keys(path) if path.exists() else set()
        stored_key = f"/{key}"

        if frame.empty:
            # pandas does not create a table-format key for an empty frame. A
            # fixed-format placeholder keeps a weekend-only download readable;
            # it is replaced with a table when candles arrive later.
            if stored_key not in stored_keys:
                frame.to_hdf(
                    path,
                    key=key,
                    mode="a" if path.exists() else "w",
                    format="fixed",
                    index=False,
                )
            continue

        append = stored_key in stored_keys
        if append:
            with pd.HDFStore(path, mode="r") as store:
                is_table = store.get_storer(key).is_table
            if not is_table:
                existing = pd.read_hdf(path, key=key)
                if not existing.empty:
                    frame = pd.concat([existing, frame], ignore_index=True)
                append = False

        frame.to_hdf(
            path,
            key=key,
            mode="a" if path.exists() else "w",
            format="table",
            append=append,
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
    retries: int = 20,
    retry_pause: int = 2000,
    batch_size: int = 10,
    chunk_days: int = 365,
    timeout: float | None = 900,
    progress: bool = True,
) -> DownloadResult:
    """Download one-minute BID and ASK candles into a DataOne HDF5 file.

    Dates describe a half-open interval: ``from_date <= timestamp < to_date``.
    Calling the function again safely extends the stored coverage. Downloads
    are committed after each ``chunk_days`` interval, so an interrupted call
    can resume from the last completed checkpoint.
    Progress shows validated BID/ASK downloads and HDF5 writes as completed
    stages. Set ``progress=False`` to suppress the progress bar.
    """

    normalised_symbol = validate_symbol(symbol)
    start = _as_date(from_date, "from_date")
    end = _as_date(to_date, "to_date")
    if start >= end:
        raise ValueError("from_date must be earlier than to_date")
    if (
        retries < 0
        or retry_pause < 0
        or batch_size < 1
        or chunk_days < 1
        or (timeout is not None and timeout <= 0)
    ):
        raise ValueError(
            "retries and retry_pause must be >= 0, batch_size and chunk_days "
            "must be >= 1, and timeout must be > 0"
        )
    target = (
        Path(output) if output is not None else Path("duka") / f"{normalised_symbol}.h5"
    )
    target = target.expanduser()
    coverage = _stored_coverage(target)
    ranges = _missing_ranges(start, end, coverage)
    chunks = _chunk_ranges(ranges, coverage, chunk_days)
    if ranges and shutil.which(npx) is None:
        raise DownloadError(
            "npx was not found. Install Node.js 18+ to use DataOne downloads; "
            "conversion and fully covered .h5 files do not require Node.js."
        )

    rows = {"bid": 0, "ask": 0}
    with tqdm(
        total=len(chunks) * 3,
        desc=f"{normalised_symbol.upper()} download",
        unit="stage",
        disable=not progress or not chunks,
        dynamic_ncols=True,
    ) as bar:
        for range_start, range_end in chunks:
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
                        retry_pause=retry_pause,
                        batch_size=batch_size,
                        timeout=timeout,
                    )
                    frame = _add_null_candles(frame, range_start, range_end)
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
        if chunks:
            bar.set_postfix_str("Complete")

    final_from, final_to = coverage if coverage else (start, end)
    return DownloadResult(
        path=target.resolve(),
        symbol=normalised_symbol,
        from_date=final_from,
        to_date=final_to,
        rows_bid=rows["bid"],
        rows_ask=rows["ask"],
        downloaded_ranges=tuple(chunks),
    )
