# DataOne troubleshooting and caveats

## Input and validation errors

- `UnsupportedSymbolError`: use `list_symbols(search)` or `dataone symbols <term>`. Display forms such as `EUR/USD` normalize, but unsupported normalized ids still fail.
- Date errors: require exact `YYYY-MM-DD` or `datetime.date`; reject timestamps, compact dates, locale dates, impossible dates, and `datetime.datetime`.
- `from_date must be earlier than to_date`: intervals cannot be empty or reversed.
- Option validation: `missing_percentage` must be 0 through 100; `retries >= 0`; `batch_size >= 1`; `chunk_days >= 1`; `timeout > 0` unless it is `None`; report `dpi > 0`.

## Download failures

- `npx was not found`: install Node.js 18+ when missing data must be fetched. A fully covered file and all conversion operations can work without it.
- Backend timeout/failure: the exception is `DownloadError` and includes symbol, side, range, and backend detail where available. `timeout` applies separately to each BID/ASK subprocess call.
- Browser/WAF challenge: install the pinned browser runtime with `npx dukascopy-node@1.51.0 --install-browser`, then retry.
- Missing or malformed backend CSV: DataOne requires all six M1 columns and numeric values.
- A failed chunk is not an atomic two-side transaction at the process level, but HDF5 writing happens only after both BID and ASK downloads for that chunk validate. Earlier chunks remain saved and the same command resumes from their coverage boundary.

Do not report the progress bar as network transfer progress. It counts validated stages, writes to stderr, and should not reach “Complete” after a failed stage.

## Existing HDF5 problems

- An existing target without valid `/m1` coverage attributes cannot be incrementally updated; `download_h5()` raises `DownloadError` rather than inferring coverage from rows.
- `read_h5()` requires both side keys. A missing key or unreadable file is surfaced as a `ValueError` saying the side is absent.
- Missing required columns raise `ValueError` naming the side and columns.
- Nonnumeric OHLCV values fail numeric conversion.
- An empty minute dataset causes `build_h1()` and EDA preparation to raise.
- Short FX/metals date ranges may not contain a complete output session period and raise `the HDF5 date range does not contain a complete output period`.

Use this compact inspection when diagnosing a file:

```python
from pathlib import Path
import pandas as pd
from dataone import read_h5

path = Path("data/eurusd.h5")
with pd.HDFStore(path, mode="r") as store:
    print(store.keys())
    node = store.get_node("/m1")
    print({name: getattr(node._v_attrs, name, None) for name in
           ("symbol", "from_date", "to_date", "timeframe", "schema_version")})

bid, ask = read_h5(path)
print(len(bid), len(ask), bid["timestamp"].min(), bid["timestamp"].max())
```

Compare coverage attributes with actual timestamp extrema; metadata describes the requested covered interval and should not be presented as proof that every expected candle exists.

## Surprising H1 results

- Unexpected calendar: set `market` explicitly. `auto` uses symbol-name heuristics, not authoritative asset metadata.
- Many gaps for an exchange-traded instrument: `generic` creates a continuous 24/7 hourly range and does not know exchange sessions or holidays.
- Fewer rows than expected: `clean=True` removes whole holiday or severely incomplete weeks. Compare with `H1Options(..., clean=False)` and generate `plot_before_after()`.
- No `group` column: expected when `clean=False`.
- Large stale-block metric: stale blocks are computed before hourly aggregation and may span hour boundaries.
- Zero quality counts alongside NaN OHLC: an expected calendar hour may contain no joined minute rows; inspect OHLC NaNs as well as counts.
- Spread is negative: DataOne calculates `close_ask - close_bid` and reports the data as received; it does not clamp values.
- BID/ASK row-count mismatch: ASK is left-joined to BID timestamps. Inspect each side with `read_h5()` before trusting spread or ASK aggregates.

## Plot/report errors

- `ImportError` mentioning matplotlib: install `dataone[plots]`.
- Unknown holiday: use one of the `available` names in the exception or omit `holiday` to use the latest/representative window.
- Unknown worst-hour metric: choose a numeric H1 column such as `total_missing`, `missing_bid`, `missing_ask`, `max_stale_block_bid`, or `max_stale_block_ask`.
- A metric with no numeric values cannot select a worst hour.

Close individual matplotlib figures after use in batch processes. `create_eda_report()` saves and closes its own figures.

## CLI behavior

The CLI returns 0 on success, 1 only for a symbol search with no matches, and exits 2 for handled download, import, OS, and value errors. It prints successful download/export/report paths or summaries to stdout; download progress goes to stderr.

## License expectation

The package is source-available under PolyForm Noncommercial 1.0.0. Personal, educational, research, charitable, and other noncommercial uses are permitted under its terms. Commercial permission is not granted by that license; advise the user to review the repository `LICENSE` or obtain a separate license rather than making a legal determination for them.

