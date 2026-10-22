"""Exploratory plots for DataOne HDF5 datasets.

Plotting is an optional feature. Install it with ``dataone[plots]``; importing
the main :mod:`dataone` package does not import matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .pipeline import (
    H1Options,
    _aggregate,
    _calendar,
    _clean,
    _join,
    _market,
    _minutes,
    _symbol,
)


@dataclass(frozen=True)
class EDAReport:
    """Files and summary values produced by :func:`create_eda_report`."""

    output_dir: Path
    holiday_session: Path
    worst_day: Path
    worst_hour: Path
    quality_eda: Path
    before_after: Path
    summary_json: Path
    summary: dict[str, Any]

    @property
    def figures(self) -> tuple[Path, ...]:
        """Return all generated figure paths in report order."""

        return (
            self.holiday_session,
            self.worst_day,
            self.worst_hour,
            self.quality_eda,
            self.before_after,
        )


@dataclass(frozen=True)
class _EDAData:
    source: Path
    symbol: str
    market: str
    options: H1Options
    minutes: pd.DataFrame
    before: pd.DataFrame
    after: pd.DataFrame
    days: pd.DataFrame


def _plot_modules():
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError as exc:  # pragma: no cover - depends on installation extra
        raise ImportError(
            "EDA plotting requires matplotlib; install 'dataone[plots]'"
        ) from exc
    return plt, Rectangle


def _prepare(path: str | Path, options: H1Options | None) -> _EDAData:
    source = Path(path).expanduser()
    selected = options or H1Options()
    # A report always needs both sides of the cleaning cut. The clean option is
    # preserved for its market, threshold, and holiday settings.
    report_options = H1Options(
        market=selected.market,
        clean=True,
        missing_percentage=selected.missing_percentage,
        excluded_holidays=selected.excluded_holidays,
    )
    minutes = _minutes(source)
    if minutes.empty:
        raise ValueError(f"{source} contains no minute candles")
    market = _market(source, report_options.market)
    weeks, days, hours = _calendar(minutes, market)
    if hours.empty:
        raise ValueError("the HDF5 date range does not contain a complete output period")
    before = _join(hours, _aggregate(minutes))
    threshold = round(60 * report_options.missing_percentage / 100)
    before["hour_missing"] = before["total_missing"].ge(threshold)
    after = _clean(before.drop(columns="hour_missing"), weeks, days, report_options)
    return _EDAData(
        source=source,
        symbol=_symbol(source).upper(),
        market=market,
        options=report_options,
        minutes=minutes,
        before=before,
        after=after,
        days=days,
    )


def _candles(ax, frame: pd.DataFrame, *, prefix: str = "", width: float = 0.62) -> None:
    """Draw compact candles without requiring mplfinance."""

    _, Rectangle = _plot_modules()
    opened = frame[f"open{prefix}"].to_numpy(dtype=float)
    high = frame[f"high{prefix}"].to_numpy(dtype=float)
    low = frame[f"low{prefix}"].to_numpy(dtype=float)
    close = frame[f"close{prefix}"].to_numpy(dtype=float)
    x = np.arange(len(frame))
    finite = np.isfinite(opened) & np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    for pos in x[finite]:
        rising = close[pos] >= opened[pos]
        color = "#2a9d8f" if rising else "#e76f51"
        ax.vlines(pos, low[pos], high[pos], color=color, linewidth=0.8)
        bottom = min(opened[pos], close[pos])
        height = abs(close[pos] - opened[pos])
        if height == 0:
            ax.hlines(opened[pos], pos - width / 2, pos + width / 2, color=color)
        else:
            ax.add_patch(
                Rectangle(
                    (pos - width / 2, bottom), width, height,
                    facecolor=color, edgecolor=color, linewidth=0.6,
                )
            )
    ax.set_xlim(-0.8, max(len(frame) - 0.2, 0.8))
    ax.grid(axis="y", alpha=0.2)


def _time_ticks(ax, index: pd.DatetimeIndex, *, maximum: int = 8, fmt: str = "%m-%d\n%H:%M") -> None:
    if len(index) == 0:
        return
    positions = np.unique(np.linspace(0, len(index) - 1, min(maximum, len(index))).astype(int))
    ax.set_xticks(positions, [index[pos].strftime(fmt) for pos in positions])


def _session_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column
        for column in (
            "is_sydney_session",
            "is_tokyo_session",
            "is_london_session",
            "is_new_york_session",
        )
        if column in frame
    ]


def _holiday_window(data: _EDAData, holiday: str | None) -> tuple[pd.DataFrame, str]:
    days = data.days
    if holiday:
        matches = days[days["holiday_name"].eq(holiday)]
        if matches.empty:
            available = sorted(days["holiday_name"].dropna().unique())
            raise ValueError(
                f"holiday {holiday!r} is not in this dataset; available: {available}"
            )
    else:
        matches = days[days["holiday_name"].notna()]

    if not matches.empty:
        # Prefer a recent holiday so the chart reflects the newest downloaded data.
        day = matches.iloc[-1]
        center = pd.Timestamp(day["day_end"])
        label = str(day["holiday_name"])
    else:
        daily_bad = data.before.groupby("dsid")["total_missing"].sum(min_count=1)
        chosen = daily_bad.idxmax() if daily_bad.notna().any() else data.before["dsid"].iloc[len(data.before) // 2]
        center = data.before.loc[data.before["dsid"].eq(chosen)].index.min().normalize()
        label = "representative session window (no configured holiday in range)"
    start, end = center - pd.Timedelta(days=2), center + pd.Timedelta(days=2)
    frame = data.before.loc[(data.before.index >= start) & (data.before.index < end)].copy()
    if frame.empty:
        frame = data.before.iloc[: min(96, len(data.before))].copy()
    return frame, label


def _plot_holiday_sessions(data: _EDAData, holiday: str | None = None):
    plt, _ = _plot_modules()
    frame, label = _holiday_window(data, holiday)
    fig, (price_ax, session_ax) = plt.subplots(
        2, 1, figsize=(14, 7), sharex=True, gridspec_kw={"height_ratios": [4, 1.2]}
    )
    _candles(price_ax, frame, prefix="_bid")
    price_ax.set_title(f"{data.symbol} — holiday/session validation: {label}")
    price_ax.set_ylabel("BID price")

    normalized = frame.index.normalize()
    for pos in np.flatnonzero(normalized.to_series(index=np.arange(len(frame))).ne(
        normalized.to_series(index=np.arange(len(frame))).shift()
    )):
        price_ax.axvline(pos - 0.5, color="#777777", linestyle="--", linewidth=0.7)

    columns = _session_columns(frame)
    matrix = frame[columns].fillna(False).astype(int).to_numpy().T
    session_ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap="Blues", vmin=0, vmax=1)
    session_ax.set_yticks(
        range(len(columns)), [c.removeprefix("is_").removesuffix("_session").replace("_", " ").title() for c in columns]
    )
    session_ax.set_ylabel("Sessions")
    _time_ticks(session_ax, frame.index, fmt="%Y-%m-%d\n%H:%M")
    fig.tight_layout()
    return fig


def _worst_hour(data: _EDAData, metric: str) -> tuple[pd.Timestamp, float, pd.DataFrame]:
    if metric not in data.before:
        raise ValueError(f"unknown hourly quality metric: {metric}")
    values = pd.to_numeric(data.before[metric], errors="coerce")
    if not values.notna().any():
        raise ValueError(f"hourly quality metric {metric!r} contains no values")
    timestamp = pd.Timestamp(values.idxmax())
    frame = data.minutes.loc[
        data.minutes.index.floor("h") == timestamp.floor("h")
    ].copy()
    return timestamp, float(values.loc[timestamp]), frame


def _plot_worst_hour(data: _EDAData, metric: str = "total_missing"):
    plt, _ = _plot_modules()
    timestamp, value, frame = _worst_hour(data, metric)
    fig, axes = plt.subplots(
        2, 2, figsize=(15, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    for column, side in enumerate(("bid", "ask")):
        _candles(axes[0, column], frame, prefix=f"_{side}")
        axes[0, column].set_title(f"{side.upper()} M1 candles")
        axes[0, column].set_ylabel("Price")
        volume = pd.to_numeric(frame[f"volume_{side}"], errors="coerce").to_numpy()
        axes[1, column].bar(np.arange(len(frame)), volume, width=0.8, color="#457b9d")
        axes[1, column].set_ylabel("Volume")
        _time_ticks(axes[1, column], frame.index, maximum=6, fmt="%H:%M")
    fig.suptitle(
        f"{data.symbol} — worst hour by {metric}: {timestamp:%Y-%m-%d %H:%M UTC} ({value:g})"
    )
    fig.tight_layout()
    return fig


def _worst_day(data: _EDAData) -> tuple[Any, pd.DataFrame]:
    scores = data.before.groupby("dsid").agg(
        missing_hours=("hour_missing", "sum"),
        missing_minutes=("total_missing", "sum"),
    )
    dsid = scores.sort_values(["missing_hours", "missing_minutes"], ascending=False).index[0]
    return dsid, data.before.loc[data.before["dsid"].eq(dsid)].copy()


def _plot_worst_day(data: _EDAData):
    plt, _ = _plot_modules()
    dsid, frame = _worst_day(data)
    fig, (price_ax, missing_ax) = plt.subplots(
        2, 1, figsize=(14, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    _candles(price_ax, frame, prefix="_bid")
    price_ax.set_ylabel("BID price")
    day = frame.index.min().strftime("%Y-%m-%d")
    price_ax.set_title(f"{data.symbol} — worst H1 day: {day} (day id {dsid})")
    colors = np.where(frame["hour_missing"].fillna(True), "#e76f51", "#457b9d")
    missing_ax.bar(np.arange(len(frame)), frame["total_missing"].fillna(60), color=colors)
    missing_ax.axhline(
        round(60 * data.options.missing_percentage / 100),
        color="black", linestyle="--", linewidth=1, label="missing-hour threshold",
    )
    missing_ax.set_ylabel("Bad M1")
    missing_ax.legend(loc="upper right")
    _time_ticks(missing_ax, frame.index, maximum=10, fmt="%H:%M")
    fig.tight_layout()
    return fig


def _finite(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").dropna().to_numpy(dtype=float)


def _plot_quality_eda(data: _EDAData):
    plt, _ = _plot_modules()
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    missing = _finite(data.before, "total_missing")
    axes[0, 0].hist(missing, bins=np.arange(-0.5, 61.5, 1), color="#457b9d")
    axes[0, 0].set_yscale("symlog")
    axes[0, 0].set_title("Missing or invalid M1 candles per H1")
    axes[0, 0].set(xlabel="Count in hour", ylabel="H1 observations (log scale)")

    bid = _finite(data.before, "max_stale_block_bid")
    ask = _finite(data.before, "max_stale_block_ask")
    maximum = max(float(np.max(bid, initial=1)), float(np.max(ask, initial=1)))
    cap = min(maximum, 60)
    bins = np.arange(-0.5, cap + 1.5, 1)
    axes[0, 1].hist(np.minimum(bid, cap), bins=bins, alpha=0.65, label="BID")
    axes[0, 1].hist(np.minimum(ask, cap), bins=bins, alpha=0.65, label="ASK")
    axes[0, 1].set_yscale("symlog")
    axes[0, 1].set_title("Maximum stale M1 block per H1")
    axes[0, 1].set(xlabel="Consecutive stale minutes", ylabel="H1 observations")
    if maximum > cap:
        axes[0, 1].text(
            cap, 0.98, f"{cap:g}+", transform=axes[0, 1].get_xaxis_transform(),
            ha="right", va="top",
        )
    axes[0, 1].legend()

    hourly = data.before.assign(utc_hour=data.before.index.hour).groupby("utc_hour")["total_missing"].mean()
    axes[1, 0].bar(hourly.index, hourly.values, color="#2a9d8f")
    axes[1, 0].set_title("Average data issues by UTC hour")
    axes[1, 0].set(xlabel="UTC hour", ylabel="Average bad M1 candles")
    axes[1, 0].set_xticks(range(0, 24, 2))

    sessions = _session_columns(data.before)
    labels, values = [], []
    for column in sessions:
        active = data.before[column].fillna(False).astype(bool)
        labels.append(column.removeprefix("is_").removesuffix("_session").replace("_", " ").title())
        values.append(data.before.loc[active, "total_missing"].mean())
    axes[1, 1].bar(labels, values, color="#f4a261")
    axes[1, 1].set_title("Average data issues during each session")
    axes[1, 1].set_ylabel("Average bad M1 candles")
    axes[1, 1].tick_params(axis="x", rotation=20)

    fig.suptitle(f"{data.symbol} — core data-quality EDA")
    fig.tight_layout()
    return fig


def _plot_before_after(data: _EDAData):
    plt, _ = _plot_modules()
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    kept = data.before.index.isin(data.after.index)
    daily = pd.DataFrame({"kept": kept.astype(int)}, index=data.before.index).resample("1D").mean()
    axes[0, 0].fill_between(daily.index, 0, daily["kept"], color="#2a9d8f", alpha=0.8)
    axes[0, 0].set_ylim(-0.02, 1.02)
    axes[0, 0].set_title("Daily fraction retained after cleaning")
    axes[0, 0].set_ylabel("Retained fraction")

    before_values = _finite(data.before, "total_missing")
    after_values = _finite(data.after, "total_missing")
    samples = [before_values]
    labels = ["Before"]
    if len(after_values):
        samples.append(after_values)
        labels.append("After")
    axes[0, 1].boxplot(samples, tick_labels=labels, showfliers=False)
    axes[0, 1].set_title("Bad M1 candles per H1")
    axes[0, 1].set_ylabel("Count")

    metrics = ["H1 rows", "Missing H1", "Weeks"]
    before_counts = [len(data.before), int(data.before["hour_missing"].sum()), data.before["wid"].nunique()]
    after_counts = [len(data.after), int(data.after.get("hour_missing", pd.Series(dtype=bool)).sum()), data.after["wid"].nunique()]
    x = np.arange(len(metrics))
    axes[1, 0].bar(x - 0.18, before_counts, width=0.36, label="Before")
    axes[1, 0].bar(x + 0.18, after_counts, width=0.36, label="After")
    axes[1, 0].set_yscale("symlog")
    axes[1, 0].set_xticks(x, metrics)
    axes[1, 0].set_title("Cleaning impact (log scale)")
    axes[1, 0].legend()

    removed = len(data.before) - len(data.after)
    summary_lines = [
        f"Symbol: {data.symbol}",
        f"Market: {data.market}",
        f"H1 rows before: {len(data.before):,}",
        f"H1 rows after: {len(data.after):,}",
        f"H1 rows removed: {removed:,} ({removed / len(data.before):.1%})",
        f"Weeks before / after: {data.before['wid'].nunique()} / {data.after['wid'].nunique()}",
        f"Missing threshold: {data.options.missing_percentage}% of M1 candles",
        "Removed units are whole holiday or severely incomplete weeks.",
    ]
    axes[1, 1].axis("off")
    axes[1, 1].text(0.02, 0.95, "\n".join(summary_lines), va="top", family="monospace", fontsize=11)
    axes[1, 1].set_title("Before / after summary")

    fig.suptitle(f"{data.symbol} — before and after cleaning")
    fig.tight_layout()
    return fig


def _save(fig, path: Path, *, dpi: int, show: bool) -> Path:
    plt, _ = _plot_modules()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    return path.resolve()


def _summary(data: _EDAData) -> dict[str, Any]:
    timestamp, value, _ = _worst_hour(data, "total_missing")
    _, worst_day = _worst_day(data)
    before_rows = len(data.before)
    after_rows = len(data.after)
    return {
        "source": str(data.source.resolve()),
        "symbol": data.symbol,
        "market": data.market,
        "h1_rows_before": before_rows,
        "h1_rows_after": after_rows,
        "h1_rows_removed": before_rows - after_rows,
        "retained_percentage": round(100 * after_rows / before_rows, 4),
        "weeks_before": int(data.before["wid"].nunique()),
        "weeks_after": int(data.after["wid"].nunique()),
        "missing_h1_before": int(data.before["hour_missing"].sum()),
        "missing_h1_after": int(data.after["hour_missing"].sum()) if not data.after.empty else 0,
        "worst_hour_utc": timestamp.isoformat(),
        "worst_hour_bad_minutes": value,
        "worst_day_utc": worst_day.index.min().date().isoformat(),
        "missing_percentage": data.options.missing_percentage,
    }


def create_eda_report(
    path: str | Path,
    output_dir: str | Path = "dataone-report",
    options: H1Options | None = None,
    *,
    holiday: str | None = None,
    worst_hour_metric: str = "total_missing",
    dpi: int = 140,
    show: bool = False,
) -> EDAReport:
    """Create a complete PNG and JSON quality report for one HDF5 file.

    The report compares the joined H1 calendar before cleaning with the final
    H1 dataset after holiday and incomplete-week removal.
    """

    if dpi <= 0:
        raise ValueError("dpi must be greater than zero")
    data = _prepare(path, options)
    destination = Path(output_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    holiday_path = _save(
        _plot_holiday_sessions(data, holiday),
        destination / "holiday_session_validation.png", dpi=dpi, show=show,
    )
    worst_day_path = _save(
        _plot_worst_day(data), destination / "worst_day_h1.png", dpi=dpi, show=show,
    )
    worst_hour_path = _save(
        _plot_worst_hour(data, worst_hour_metric),
        destination / "worst_hour_m1_bid_ask.png", dpi=dpi, show=show,
    )
    quality_path = _save(
        _plot_quality_eda(data), destination / "quality_eda.png", dpi=dpi, show=show,
    )
    comparison_path = _save(
        _plot_before_after(data), destination / "before_after_cleaning.png", dpi=dpi, show=show,
    )
    summary = _summary(data)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    return EDAReport(
        output_dir=destination.resolve(),
        holiday_session=holiday_path,
        worst_day=worst_day_path,
        worst_hour=worst_hour_path,
        quality_eda=quality_path,
        before_after=comparison_path,
        summary_json=summary_path.resolve(),
        summary=summary,
    )


def plot_holiday_sessions(
    path: str | Path, options: H1Options | None = None, *, holiday: str | None = None
):
    """Return the holiday/session validation matplotlib figure."""

    return _plot_holiday_sessions(_prepare(path, options), holiday)


def plot_worst_day(path: str | Path, options: H1Options | None = None):
    """Return the worst-day H1 candlestick matplotlib figure."""

    return _plot_worst_day(_prepare(path, options))


def plot_worst_hour(
    path: str | Path,
    options: H1Options | None = None,
    *,
    metric: str = "total_missing",
):
    """Return paired BID/ASK M1 candles for the worst H1 interval."""

    return _plot_worst_hour(_prepare(path, options), metric)


def plot_quality_eda(path: str | Path, options: H1Options | None = None):
    """Return the core data-quality EDA matplotlib figure."""

    return _plot_quality_eda(_prepare(path, options))


def plot_before_after(path: str | Path, options: H1Options | None = None):
    """Return the before-versus-after cleaning matplotlib figure."""

    return _plot_before_after(_prepare(path, options))
