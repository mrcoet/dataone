# DataOne usage

## Install and prerequisites

```bash
python -m pip install dataone
python -m pip install "dataone[plots]"  # only for EDA/plots
```

Node.js 18+ with `npx` is needed only when `download_h5()` or `dataone download` must fetch missing coverage. An already-covered file can be reopened without Node, and H1 conversion is Python-only.

## Choose a symbol

Public API:

```python
from dataone import is_supported_symbol, list_symbols, normalize_symbol, validate_symbol

matches = list_symbols("btc")
assert is_supported_symbol("EUR/USD")
symbol = validate_symbol("EUR/USD")  # "eurusd"
```

`normalize_symbol()` lowercases, trims surrounding whitespace, and removes `.`, `/`, and `-`; it does not prove the normalized id is supported. `validate_symbol()` returns the normalized catalogue id or raises `UnsupportedSymbolError` with suggestions. `is_supported_symbol()` returns `False` for invalid input instead of raising. The 0.1.0 catalogue contains 1,499 sorted ids, but availability and actual history can change.

CLI:

```bash
dataone symbols
dataone symbols btc
```

The filtered command exits 1 when no ids match. Invalid download symbols fail before filesystem or network work.

## Download or incrementally update M1 data

```python
from dataone import download_h5

result = download_h5(
    "eurusd",
    from_date="2024-01-01",
    to_date="2025-01-01",       # exclusive
    output="data/eurusd.h5",    # default: ./duka/eurusd.h5
    retries=5,
    batch_size=10,
    timeout=900,                 # seconds per BID or ASK backend call; None disables it
    progress=True,
)
```

Equivalent CLI:

```bash
dataone download eurusd --from 2024-01-01 --to 2025-01-01 -o data/eurusd.h5
```

Useful options are `--retries`, `--batch-size`, `--timeout`, and `--no-progress`. The default progress bar measures three completed stages per missing interval: validated BID download, validated ASK download, and HDF5 write. It does not show bytes or individual candles.

Incremental behavior:

- A new file downloads the requested range.
- An existing file with valid coverage metadata downloads only an uncovered prefix, suffix, or both.
- A fully covered request makes no backend request and shows no progress bar.
- New rows are appended. Reading later sorts timestamps and keeps the last duplicate timestamp per side.
- Coverage metadata is stored at `/m1` as `from_date`, `to_date`, `symbol`, `timeframe="m1"`, and `schema_version=1`.

Do not equate catalogue support or coverage metadata with guaranteed candles for every minute. Dukascopy can have a later real history than a catalogue placeholder suggests.

If Dukascopy presents its browser WAF challenge, run:

```bash
npx dukascopy-node@1.51.0 --install-browser
```

Then retry the same bounded request.

## Read M1 HDF5 data

```python
from dataone import read_h5

bid_m1, ask_m1 = read_h5("data/eurusd.h5")
```

Each returned pandas DataFrame has `timestamp`, `open`, `high`, `low`, `close`, and `volume`. Timestamps are timezone-aware UTC. OHLCV values are converted to `float32`; rows are sorted and duplicate timestamps retain the last occurrence.

## Build hourly data

```python
from dataone import H1Options, build_h1, export_h1

options = H1Options(
    market="auto",              # auto | fx | metals | crypto | generic
    clean=True,
    missing_percentage=80,
    excluded_holidays=(
        "New Year's Day",
        "Christmas Day",
        "Good Friday",
        "Boxing Day",
    ),
)

h1 = build_h1("data/eurusd.h5", options)
csv_path = export_h1("data/eurusd.h5", "data/eurusd_h1.csv", options)
```

`build_h1()` returns a pandas DataFrame indexed by timezone-aware UTC `utc_date`. `export_h1()` creates parent directories, writes the index as the first CSV column named `utc_date`, and returns the absolute output `Path`.

Equivalent CLI:

```bash
dataone h1 data/eurusd.h5 -o data/eurusd_h1.csv --market fx --missing-percentage 80
```

Use `--no-clean` to retain the joined calendar before whole-week cleanup.

Market behavior:

- `fx`: session-aware weekday calendar from Sydney open through New York close.
- `metals`: the FX-style calendar with the New York 17:00 local hour removed.
- `crypto`: continuous hourly range.
- `generic`: continuous hourly range; use for stocks, ETFs, bonds, commodities, and indices when DataOne has no exchange-specific calendar.
- `auto`: precious-metal prefixes `XAU`, `XAG`, `XPT`, `XPD` become `metals`; known crypto prefixes become `crypto`; other six-letter alphabetic symbols become `fx`; everything else becomes `generic`.

Because `generic` is continuous, it is not an exchange-hours calendar. Missing closed-market periods may therefore appear as absent data.

## Create plots and a report

```python
from dataone import H1Options, create_eda_report

report = create_eda_report(
    "data/eurusd.h5",
    "reports/eurusd",
    H1Options(market="fx", missing_percentage=80),
    holiday=None,                # latest configured holiday when available
    worst_hour_metric="total_missing",
    dpi=140,
    show=False,
)
print(report.summary)
```

CLI:

```bash
dataone eda data/eurusd.h5 -o reports/eurusd --market fx
```

Individual plot functions return normal matplotlib `Figure` objects and do not save automatically:

```python
from dataone import (
    plot_before_after,
    plot_holiday_sessions,
    plot_quality_eda,
    plot_worst_day,
    plot_worst_hour,
)

fig = plot_worst_hour("data/eurusd.h5", options, metric="max_stale_block_bid")
fig.savefig("worst-stale-hour.png")
```

`create_eda_report()` always computes both pre-clean and post-clean views. The incoming `H1Options.clean` value does not disable report cleaning; its market, threshold, and excluded holidays are preserved.

