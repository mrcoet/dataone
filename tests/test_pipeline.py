from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataone import H1Options, build_h1, export_h1, read_h5


def test_read_h5_returns_utc_frames(complete_fx_h5: Path) -> None:
    bid, ask = read_h5(complete_fx_h5)
    assert len(bid) == len(ask) == 6 * 24 * 60
    assert str(bid["timestamp"].dt.tz) == "UTC"


def test_export_h1_is_usable_from_python(complete_fx_h5: Path, tmp_path: Path) -> None:
    output = tmp_path / "result" / "h1.csv"
    returned = export_h1(complete_fx_h5, output, H1Options(market="fx"))
    assert returned == output.resolve()
    exported = pd.read_csv(output)
    assert not exported.empty
    assert exported.columns[0] == "utc_date"
    assert {"open_bid", "close_bid", "avg_spread", "group"} <= set(exported.columns)


def test_build_h1_can_skip_final_cleaning(complete_fx_h5: Path) -> None:
    frame = build_h1(complete_fx_h5, H1Options(market="fx", clean=False))
    assert "group" not in frame
    assert frame.index.name == "utc_date"


def test_null_candles_are_counted_as_missing(complete_fx_h5: Path) -> None:
    missing_hour = pd.Timestamp("2024-01-07 22:00:00", tz="UTC")
    for side in ("bid", "ask"):
        key = f"m1/{side}"
        frame = pd.read_hdf(complete_fx_h5, key)
        timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        mask = timestamps.between(
            missing_hour, missing_hour + pd.Timedelta(minutes=59)
        )
        frame.loc[mask, ["open", "high", "low", "close", "volume"]] = float(
            "nan"
        )
        frame.to_hdf(
            complete_fx_h5, key=key, mode="a", format="table", index=False
        )

    result = build_h1(complete_fx_h5, H1Options(market="fx", clean=False))
    hour = result.loc[missing_hour]
    assert hour["missing_bid"] == 60
    assert hour["missing_ask"] == 60
    assert hour["total_missing"] == 60
