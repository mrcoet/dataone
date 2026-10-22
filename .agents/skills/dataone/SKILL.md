---
name: dataone
description: Use the DataOne Python library and CLI to validate Dukascopy symbols, download or incrementally update BID/ASK M1 HDF5 data, build quality-checked session-aware H1 datasets, create EDA reports, and explain the resulting artifacts and quality metrics. Apply when a task names DataOne or works with its .h5, h1.csv, or report outputs; do not use for unrelated market-data libraries.
---

# DataOne

Use DataOne as a data-engineering library. Do not interpret its outputs as trading signals or make claims about market profitability.

## Route the task

- For installation, symbol validation, downloading, updating, H1 conversion, or CLI/API examples, read [references/usage.md](references/usage.md).
- When inspecting, validating, or explaining an HDF5 file, H1 DataFrame/CSV, `DownloadResult`, or EDA report, read [references/results.md](references/results.md).
- For errors, surprising row counts, calendar behavior, cleaning effects, or operational caveats, read [references/troubleshooting.md](references/troubleshooting.md).

Read more than one reference when the request spans those concerns.

## Working method

1. Establish whether the user wants to download data, transform an existing HDF5 file, inspect results, or generate plots.
2. Prefer the public imports from `dataone`; use the `dataone` CLI for shell workflows. Do not rely on private helpers from `dataone.pipeline`, `dataone.downloader`, or `dataone.eda`.
3. Validate the symbol before proposing a long download. Treat `to_date` as exclusive and keep timestamps in UTC.
4. Choose `market` deliberately when `auto` could misclassify an unconventional instrument. Use `generic` for stocks, ETFs, bonds, commodities, and indices unless a supported specialized calendar is intended.
5. Explain what an operation returns or writes, including whether counts describe newly downloaded rows or total stored rows and whether cleaning removes whole weeks.
6. Verify observable artifacts after execution: HDF5 keys and coverage, returned paths, DataFrame columns/index, CSV existence, or report files. For costly network downloads, do not silently broaden the requested interval.

## Core constraints

- Python 3.10+ is required. Downloading additionally requires Node.js 18+ and `npx`; transforming an existing HDF5 file does not.
- Plotting requires the optional `dataone[plots]` dependency.
- The pinned download backend is `dukascopy-node==1.51.0` unless the caller explicitly overrides it.
- Dates must be real `YYYY-MM-DD` strings or `datetime.date` values. A `datetime.datetime` is rejected.
- Download intervals are half-open: `from_date <= timestamp < to_date`.
- A DataOne HDF5 file must contain both `/m1/bid` and `/m1/ask` with `timestamp`, `open`, `high`, `low`, `close`, and `volume`.
- DataOne is licensed under PolyForm Noncommercial 1.0.0. Do not describe it as OSI open source or imply that commercial use is granted.

## Version awareness

This skill documents DataOne 0.1.0. When working in a checkout or with another installed version, inspect `dataone.__version__`, the public exports in `dataone.__init__`, and current command help before asserting signatures or schemas. Prefer repository code and tests over stale examples.

