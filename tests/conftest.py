from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def complete_fx_h5(tmp_path: Path) -> Path:
    timestamps = pd.date_range(
        "2024-01-07 00:00:00",
        "2024-01-13 00:00:00",
        freq="min",
        inclusive="left",
        tz="UTC",
    )
    sequence = np.arange(len(timestamps), dtype="float64")
    base = 1.08 + sequence / 10_000_000

    def side(offset: float) -> pd.DataFrame:
        opened = base + offset
        return pd.DataFrame(
            {
                "timestamp": (timestamps.as_unit("ms").asi8).astype("int64"),
                "open": opened,
                "high": opened + 0.00005,
                "low": opened - 0.00005,
                "close": opened + 0.00001,
                "volume": 1.0 + (sequence % 17) / 10,
            }
        )

    path = tmp_path / "eurusd.h5"
    side(0).to_hdf(path, key="m1/bid", mode="w", format="table", index=False)
    side(0.0001).to_hdf(path, key="m1/ask", mode="a", format="table", index=False)
    with pd.HDFStore(path, mode="a") as store:
        attrs = store.get_node("/m1")._v_attrs
        attrs.from_date = "2024-01-07"
        attrs.to_date = "2024-01-13"
        attrs.symbol = "eurusd"
    return path
