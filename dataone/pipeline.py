"""HDF5-to-hourly transformation and CSV export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from .calendar import (
    build_continuous_hour_calendar,
    build_day_calendar,
    build_hour_calendar,
    build_week_calendar,
)

Market = Literal["auto", "fx", "metals", "crypto", "generic"]
OHLCV = ["open", "high", "low", "close", "volume"]
DEFAULT_HOLIDAYS = ("New Year's Day", "Christmas Day", "Good Friday", "Boxing Day")


@dataclass(frozen=True)
class H1Options:
    """Options controlling H1 production."""

    market: Market = "auto"
    clean: bool = True
    missing_percentage: int = 80
    excluded_holidays: tuple[str, ...] = DEFAULT_HOLIDAYS

    def __post_init__(self) -> None:
        if self.market not in {"auto", "fx", "metals", "crypto", "generic"}:
            raise ValueError(f"unsupported market: {self.market}")
        if not 0 <= self.missing_percentage <= 100:
            raise ValueError("missing_percentage must be between 0 and 100")


def _timestamp(values: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_datetime(values, unit="ms", utc=True)
    return pd.to_datetime(values, utc=True)


def _read_side(path: Path, side: str) -> pd.DataFrame:
    try:
        frame = pd.read_hdf(path, key=f"m1/{side}")
    except (FileNotFoundError, KeyError, OSError) as exc:
        raise ValueError(f"{path} does not contain m1/{side}") from exc
    required = {"timestamp", *OHLCV}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"m1/{side} is missing columns: {sorted(missing)}")
    result = frame.loc[:, ["timestamp", *OHLCV]].copy()
    result["timestamp"] = _timestamp(result["timestamp"])
    for column in OHLCV:
        result[column] = pd.to_numeric(result[column], errors="raise").astype("float32")
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def read_h5(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read and validate the BID and ASK minute tables in a DataOne HDF5 file."""

    source = Path(path).expanduser()
    return _read_side(source, "bid"), _read_side(source, "ask")


def _quality_side(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["null_candle"] = result[OHLCV].isna().any(axis=1)
    stale = (
        result["open"].shift().eq(result["open"])
        & result["high"].shift().eq(result["high"])
        & result["low"].shift().eq(result["low"])
        & result["close"].shift().eq(result["close"])
        & result["volume"].gt(0)
    )
    result["stale_block"] = stale.groupby(stale.ne(stale.shift()).cumsum()).transform(
        "sum"
    )
    result["stale"] = stale
    return result


def _minutes(path: Path) -> pd.DataFrame:
    bid, ask = read_h5(path)
    bid = _quality_side(bid)
    ask = _quality_side(ask)
    minute = bid.merge(ask, on="timestamp", how="left", suffixes=("_bid", "_ask"))
    minute = minute.set_index("timestamp").sort_index()
    minute["spread"] = minute["close_ask"] - minute["close_bid"]
    minute["bad_volume_ask"] = minute["volume_ask"].le(0)
    minute["bad_volume_bid"] = minute["volume_bid"].le(0)
    for side in ("bid", "ask"):
        high = minute[f"high_{side}"]
        low = minute[f"low_{side}"]
        opened = minute[f"open_{side}"]
        close = minute[f"close_{side}"]
        minute[f"bad_logic_{side}"] = (
            high.lt(opened)
            | high.lt(close)
            | high.lt(low)
            | low.gt(opened)
            | low.gt(close)
            | low.gt(high)
        )
        minute[f"missing_{side}"] = (
            minute[f"bad_volume_{side}"]
            | minute[f"stale_{side}"]
            | minute[f"bad_logic_{side}"]
            | minute[f"null_candle_{side}"]
        )
    minute["total_missing"] = minute["missing_ask"] | minute["missing_bid"]
    minute["total_bad_volume"] = minute["bad_volume_ask"] | minute["bad_volume_bid"]
    return minute


def _aggregate(minutes: pd.DataFrame) -> pd.DataFrame:
    return minutes.resample("1h").agg(
        open_bid=("open_bid", "first"),
        high_bid=("high_bid", "max"),
        low_bid=("low_bid", "min"),
        close_bid=("close_bid", "last"),
        avg_spread=("spread", "mean"),
        max_spread=("spread", "max"),
        bid_volume=("volume_bid", "sum"),
        ask_volume=("volume_ask", "sum"),
        num_stales_bid=("stale_bid", "sum"),
        max_stale_block_bid=("stale_block_bid", "max"),
        bad_volume_bid=("bad_volume_bid", "sum"),
        bad_logic_bid=("bad_logic_bid", "sum"),
        missing_bid=("missing_bid", "sum"),
        num_stales_ask=("stale_ask", "sum"),
        max_stale_block_ask=("stale_block_ask", "max"),
        bad_volume_ask=("bad_volume_ask", "sum"),
        bad_logic_ask=("bad_logic_ask", "sum"),
        missing_ask=("missing_ask", "sum"),
        total_bad_volume=("total_bad_volume", "sum"),
        total_missing=("total_missing", "sum"),
    )


def _symbol(path: Path) -> str:
    try:
        with pd.HDFStore(path, mode="r") as store:
            return str(store.get_node("/m1")._v_attrs.symbol).lower()
    except (AttributeError, KeyError):
        return path.stem.lower()


def _market(path: Path, requested: Market) -> str:
    if requested != "auto":
        return requested
    symbol = _symbol(path)
    if symbol.startswith(("xau", "xag", "xpt", "xpd")):
        return "metals"
    if symbol.startswith(
        (
            "btc",
            "eth",
            "bch",
            "ltc",
            "xlm",
            "ada",
            "ave",
            "bat",
            "cmp",
            "dsh",
            "lnk",
            "trx",
            "uni",
            "yfi",
        )
    ):
        return "crypto"
    if len(symbol) == 6 and symbol.isalpha():
        return "fx"
    return "generic"


def _calendar(
    minutes: pd.DataFrame, market: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start, end = minutes.index.min(), minutes.index.max()
    if market in {"fx", "metals"}:
        weeks = build_week_calendar(start, end, days=5)
        days = build_day_calendar(weeks, days=5)
        hours = build_hour_calendar(days, market=market)
    else:
        hours = build_continuous_hour_calendar(start, end)
        week_bounds = hours.groupby("wid")["utc_date"].agg(["min", "max"])
        weeks = pd.DataFrame(
            {
                "wid": week_bounds.index,
                "start_date": week_bounds["min"],
                "end_date": week_bounds["max"],
            }
        ).reset_index(drop=True)
        day_bounds = (
            hours.groupby(["wid", "dsid"])["utc_date"].agg(["min", "max"]).reset_index()
        )
        days = day_bounds.rename(columns={"min": "day_start", "max": "day_end"})
        days["is_holiday"] = False
        days["holiday_name"] = None
    return weeks, days, hours


def _join(hours: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    metadata = hours[
        [
            "wid",
            "dsid",
            "hsid",
            "utc_date",
            "utc_day_name",
            "is_sydney_session",
            "is_tokyo_session",
            "is_london_session",
            "is_new_york_session",
        ]
    ].set_index("utc_date")
    return metadata.join(hourly, how="left")


def _segments(length: int, deleted: list[int]) -> list[tuple[int, int]]:
    deleted = sorted({value for value in deleted if 0 <= value < length})
    cuts: list[tuple[int, int]] = []
    start = 0
    for value in deleted:
        if value > start:
            cuts.append((start, value - 1))
        start = value + 1
    if start == length - 1:
        cuts.append((start, start))
    elif start < length - 1:
        cuts.append((start, length - 1))
    return cuts


def _clean(
    joined: pd.DataFrame,
    weeks: pd.DataFrame,
    days: pd.DataFrame,
    options: H1Options,
) -> pd.DataFrame:
    result = joined.copy()
    minute_threshold = round(60 * options.missing_percentage / 100)
    result["hour_missing"] = result["total_missing"].ge(minute_threshold)

    missing_by_day = result.groupby("dsid")["hour_missing"].sum().rename("hour_missing")
    days_with_missing = days.merge(missing_by_day, on="dsid", how="left")
    bad_day_values = (
        days_with_missing["hour_missing"]
        .value_counts()
        .sort_index()
        .loc[
            lambda counts: counts.index >= round(24 * options.missing_percentage / 100)
        ]
        .index
    )
    incomplete_weeks = days_with_missing.loc[
        days_with_missing["hour_missing"].isin(bad_day_values), "wid"
    ].tolist()
    holiday_weeks = days_with_missing.loc[
        days_with_missing["holiday_name"].isin(options.excluded_holidays), "wid"
    ].tolist()

    group_by_week: dict[int, float] = {}
    for group, (start, end) in enumerate(
        _segments(len(weeks), holiday_weeks + incomplete_weeks)
    ):
        for wid in range(start, end + 1):
            group_by_week[wid] = float(group)
    result["group"] = result["wid"].map(group_by_week)
    return result[result["group"].notna()]


def build_h1(path: str | Path, options: H1Options | None = None) -> pd.DataFrame:
    """Build the final session-aware hourly DataFrame from a DataOne HDF5 file."""

    source = Path(path).expanduser()
    selected = options or H1Options()
    minutes = _minutes(source)
    if minutes.empty:
        raise ValueError(f"{source} contains no minute candles")
    market = _market(source, selected.market)
    weeks, days, hours = _calendar(minutes, market)
    if hours.empty:
        raise ValueError(
            "the HDF5 date range does not contain a complete output period"
        )
    result = _join(hours, _aggregate(minutes))
    return _clean(result, weeks, days, selected) if selected.clean else result


def export_h1(
    path: str | Path,
    output: str | Path = "h1.csv",
    options: H1Options | None = None,
) -> Path:
    """Build an H1 dataset, write it to CSV, and return the absolute output path."""

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame = build_h1(path, options)
    frame.to_csv(destination, index=True, index_label="utc_date")
    return destination.resolve()
