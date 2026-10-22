# DataOne results and interpretation

## `DownloadResult`

`download_h5()` returns an immutable `DownloadResult` with:

- `path`: absolute HDF5 `Path`.
- `symbol`: normalized Dukascopy id.
- `from_date`, `to_date`: final total stored coverage, not necessarily only the requested interval.
- `rows_bid`, `rows_ask`: rows fetched during this call, not total rows already stored.
- `downloaded_ranges`: missing half-open date ranges requested during this call.

For a fully covered request, `downloaded_ranges == ()` and both row counters are zero. That means no new download was needed; it does not mean the HDF5 tables are empty.

## HDF5 layout

Keys:

```text
/m1/bid
/m1/ask
```

Each table has exactly the working columns `timestamp`, `open`, `high`, `low`, `close`, `volume`. Stored timestamps are Unix milliseconds for DataOne-created files. `read_h5()` also accepts parseable nonnumeric timestamp columns and normalizes either representation to UTC.

The BID table drives the minute-level join. ASK is left-joined on timestamp; missing ASK values remain absent and flow into hourly aggregates. The implementation does not create an outer union from ASK-only timestamps.

## H1 DataFrame and CSV schema

The index is `utc_date`, an hourly timezone-aware UTC timestamp in memory. These calendar columns precede metrics:

- `wid`: week id.
- `dsid`: day/session id.
- `hsid`: hour id.
- `utc_day_name`: English UTC weekday name.
- `is_sydney_session`, `is_tokyo_session`, `is_london_session`, `is_new_york_session`: local-market session flags adjusted by timezone rules.

Hourly market and quality columns:

- `open_bid`, `high_bid`, `low_bid`, `close_bid`: BID OHLC, aggregated first/max/min/last.
- `avg_spread`, `max_spread`: ASK close minus BID close, averaged or maximized within the hour.
- `bid_volume`, `ask_volume`: summed side-specific volume.
- `num_stales_bid`, `num_stales_ask`: count of stale minute candles.
- `max_stale_block_bid`, `max_stale_block_ask`: largest consecutive stale-block length touching the hour. A stale block is computed on the full minute series, so its reported length can exceed the count within one hour or span an hour boundary.
- `bad_volume_bid`, `bad_volume_ask`: count of minute rows with volume `<= 0`.
- `bad_logic_bid`, `bad_logic_ask`: count whose OHLC relationships are impossible, such as high below open/close/low or low above open/close/high.
- `missing_bid`, `missing_ask`: side-specific count flagged because of stale price, non-positive volume, or invalid OHLC.
- `total_bad_volume`: minutes for which either side has bad volume.
- `total_missing`: minutes for which either side is flagged missing/invalid.

When `clean=True`, two additional columns appear:

- `hour_missing`: `True` when `total_missing >= round(60 * missing_percentage / 100)`.
- `group`: floating-point id for each contiguous run of retained weeks.

When `clean=False`, neither `hour_missing` nor `group` is added by `build_h1()`.

NaN values are expected when the generated calendar has an hour without matching data. Pandas aggregate sums may be zero for an all-missing group while OHLC/spread fields remain NaN, so inspect both prices and quality fields when auditing gaps.

## What cleaning does

The default `missing_percentage=80` gives an H1 threshold of `round(60 * 0.8) = 48` bad minutes. A day is considered severely incomplete when its number of missing H1 rows is at least `round(24 * 0.8) = 19`.

Cleaning removes entire week ids when either condition holds:

- the week contains a day meeting the severe-incompleteness rule; or
- the week contains an excluded configured holiday.

The default excluded holidays are New Year's Day, Christmas Day, Good Friday, and Boxing Day. Easter Monday is labeled by the calendar but is not excluded by default. `group` labels the contiguous retained week segments separated by removed weeks. Cleaning is therefore a coverage/continuity policy, not minute repair, interpolation, or imputation.

For `crypto` and `generic`, generated days have no holidays, so cleanup is driven only by completeness.

## EDA report artifacts

`create_eda_report()` returns an immutable `EDAReport`. `output_dir` and all artifact fields are absolute paths; `figures` returns the five PNG paths in this order:

1. `holiday_session_validation.png`: BID H1 candles around the selected/latest holiday, plus session activity. If no configured holiday exists, it uses a representative poor-quality window.
2. `worst_day_h1.png`: worst day ranked first by missing H1 count and then bad-minute count.
3. `worst_hour_m1_bid_ask.png`: BID and ASK minute candles and volumes for the maximum chosen hourly metric.
4. `quality_eda.png`: missing-count distribution, stale blocks, UTC-hour quality, and session quality.
5. `before_after_cleaning.png`: daily retention, quality distribution, counts, and cleanup summary.

The sixth artifact is `summary.json`. Its object contains:

- `source`, `symbol`, `market`
- `h1_rows_before`, `h1_rows_after`, `h1_rows_removed`, `retained_percentage`
- `weeks_before`, `weeks_after`
- `missing_h1_before`, `missing_h1_after`
- `worst_hour_utc`, `worst_hour_bad_minutes`, `worst_day_utc`
- `missing_percentage`

The summary's worst hour is always based on `total_missing`, even if `worst_hour_metric` selects a different metric for the PNG. Mention this distinction when explaining a report generated with a custom worst-hour metric.

The report writes figures sequentially. If a later plot or JSON write fails, earlier PNGs can remain in the output directory; report creation is not transactional.

