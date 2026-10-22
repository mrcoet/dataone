from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from subprocess import CompletedProcess

import pandas as pd
import pytest

from dataone import downloader


def _frame(start: str) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=3, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps.as_unit("ms").asi8,
            "open": [1.0, 1.1, 1.2],
            "high": [1.1, 1.2, 1.3],
            "low": [0.9, 1.0, 1.1],
            "close": [1.05, 1.15, 1.25],
            "volume": [1.0, 2.0, 3.0],
        }
    )


def test_download_h5_uses_public_incremental_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    calls: list[tuple[str, str]] = []

    def fake_download(symbol, side, start, end, destination, **kwargs):
        calls.append((symbol, side))
        return _frame(start.isoformat())

    monkeypatch.setattr(downloader, "_download_csv", fake_download)
    target = tmp_path / "market" / "eurusd.h5"
    result = downloader.download_h5("EUR/USD", "2024-01-01", "2024-01-02", target)

    assert result.path == target.resolve()
    assert calls == [("eurusd", "bid"), ("eurusd", "ask")]
    assert pd.read_hdf(target, "m1/bid").shape == (3, 6)

    calls.clear()
    second = downloader.download_h5("eurusd", "2024-01-01", "2024-01-02", target)
    assert second.downloaded_ranges == ()
    assert calls == []


def test_default_path_is_duka_and_covered_data_is_not_downloaded(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    calls: list[tuple[str, str]] = []

    def fake_download(symbol, side, start, end, destination, **kwargs):
        calls.append((symbol, side))
        return _frame(start.isoformat())

    monkeypatch.setattr(downloader, "_download_csv", fake_download)

    first = downloader.download_h5("EURUSD", "2024-01-01", "2024-01-02")
    assert first.path == (tmp_path / "duka" / "eurusd.h5").resolve()
    assert first.path.exists()
    assert calls == [("eurusd", "bid"), ("eurusd", "ask")]

    calls.clear()
    monkeypatch.setattr(downloader.shutil, "which", lambda _: None)
    second = downloader.download_h5("EURUSD", "2024-01-01", "2024-01-02")
    assert second.downloaded_ranges == ()
    assert second.rows_bid == second.rows_ask == 0
    assert calls == []


@pytest.mark.parametrize(
    "value",
    [
        "2024/01/01",
        "01-01-2024",
        "20240101",
        "2024-1-1",
        "2024-01-01T00:00:00",
        "2024-02-30",
        datetime(2024, 1, 1),
        20240101,
        None,
    ],
)
def test_download_rejects_invalid_date_formats(monkeypatch, value) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    with pytest.raises((TypeError, ValueError)):
        downloader.download_h5("eurusd", value, "2024-03-01")


def test_download_accepts_date_objects_and_rejects_reversed_range(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(
        downloader,
        "_download_csv",
        lambda symbol, side, start, end, destination, **kwargs: _frame(
            start.isoformat()
        ),
    )
    result = downloader.download_h5(
        "eurusd",
        date(2024, 1, 1),
        date(2024, 1, 2),
        tmp_path / "eurusd.h5",
    )
    assert result.from_date == date(2024, 1, 1)

    with pytest.raises(ValueError, match="earlier than"):
        downloader.download_h5("eurusd", "2024-01-02", "2024-01-01")


def test_unsupported_symbol_fails_before_filesystem_or_network(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        downloader.shutil,
        "which",
        lambda _: pytest.fail("npx must not be inspected for an invalid symbol"),
    )
    with pytest.raises(ValueError, match="does not support"):
        downloader.download_h5("not-a-real-pair", "2024-01-01", "2024-01-02")
    assert not (tmp_path / "duka").exists()


@pytest.mark.parametrize("progress", [True, False])
def test_download_progress_and_covered_file_output(
    monkeypatch, tmp_path: Path, capsys, progress: bool
) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(
        downloader,
        "_download_csv",
        lambda symbol, side, start, end, destination, **kwargs: _frame(
            start.isoformat()
        ),
    )
    path = tmp_path / "eurusd.h5"
    downloader.download_h5(
        "eurusd", "2024-01-01", "2024-01-02", path, progress=progress
    )
    output = capsys.readouterr()
    assert not output.out
    if progress:
        assert "EURUSD download" in output.err
        assert "100%" in output.err
        assert "3/3" in output.err
    else:
        assert not output.err

    downloader.download_h5("eurusd", "2024-01-01", "2024-01-02", path)
    assert not capsys.readouterr().err


def test_failed_download_does_not_display_complete_progress(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")

    def fail(*args, **kwargs):
        raise downloader.DownloadError("backend failed")

    monkeypatch.setattr(downloader, "_download_csv", fail)
    with pytest.raises(downloader.DownloadError, match="backend failed"):
        downloader.download_h5(
            "eurusd", "2024-01-01", "2024-01-02", tmp_path / "eurusd.h5"
        )
    assert "100%" not in capsys.readouterr().err


def test_download_csv_tolerates_empty_backend_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    command: list[str] = []

    def empty_download(args, **kwargs):
        command.extend(args)
        output_name = args[args.index("--file-name") + 1]
        (tmp_path / f"{output_name}.csv").touch()
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(downloader.subprocess, "run", empty_download)
    frame = downloader._download_csv(
        "gbpcad",
        "bid",
        date(2010, 1, 2),
        date(2010, 1, 3),
        tmp_path,
        npx="npx",
        package_version="1.51.0",
        retries=0,
        batch_size=10,
        timeout=30,
    )

    assert "--retry-on-empty" in command
    assert "--no-fail-after-retries" in command
    assert frame.empty
    assert tuple(frame.columns) == downloader.REQUIRED_COLUMNS


def test_empty_range_creates_readable_h5_and_can_later_be_extended(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(
        downloader,
        "_download_csv",
        lambda symbol, side, start, end, destination, **kwargs: pd.DataFrame(
            columns=downloader.REQUIRED_COLUMNS
        ),
    )
    target = tmp_path / "gbpcad.h5"

    empty = downloader.download_h5(
        "gbpcad", "2010-01-02", "2010-01-03", target, progress=False
    )
    bid, ask = pd.read_hdf(target, "m1/bid"), pd.read_hdf(target, "m1/ask")
    assert empty.rows_bid == empty.rows_ask == 0
    assert bid.empty and ask.empty

    monkeypatch.setattr(
        downloader,
        "_download_csv",
        lambda symbol, side, start, end, destination, **kwargs: _frame(
            start.isoformat()
        ),
    )
    extended = downloader.download_h5(
        "gbpcad", "2010-01-02", "2010-01-04", target, progress=False
    )
    assert extended.rows_bid == extended.rows_ask == 3
    assert pd.read_hdf(target, "m1/bid").shape == (3, 6)


@pytest.mark.parametrize(
    "extra_args, expected", [([], True), (["--no-progress"], False)]
)
def test_cli_passes_progress_option(monkeypatch, extra_args, expected) -> None:
    from dataone import cli

    def fake_download(*args, **kwargs):
        assert kwargs["progress"] is expected
        return downloader.DownloadResult(
            Path("eurusd.h5"), "eurusd", date(2024, 1, 1), date(2024, 1, 2), 3, 3, ()
        )

    monkeypatch.setattr(cli, "download_h5", fake_download)
    assert (
        cli.main(
            [
                "download",
                "eurusd",
                "--from",
                "2024-01-01",
                "--to",
                "2024-01-02",
                *extra_args,
            ]
        )
        == 0
    )
