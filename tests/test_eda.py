from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from dataone import (
    H1Options,
    create_eda_report,
    plot_before_after,
    plot_holiday_sessions,
    plot_quality_eda,
    plot_worst_day,
    plot_worst_hour,
)

@pytest.mark.parametrize(
    "plotter",
    [
        plot_holiday_sessions,
        plot_worst_day,
        plot_worst_hour,
        plot_quality_eda,
        plot_before_after,
    ],
)
def test_public_plot_functions_return_figures(complete_fx_h5: Path, plotter) -> None:
    figure = plotter(complete_fx_h5, H1Options(market="fx"))
    assert figure.axes
    plt.close(figure)


def test_create_eda_report_writes_all_artifacts(
    complete_fx_h5: Path, tmp_path: Path
) -> None:
    report = create_eda_report(
        complete_fx_h5,
        tmp_path / "report",
        H1Options(market="fx"),
        dpi=60,
    )

    assert report.output_dir == (tmp_path / "report").resolve()
    assert all(path.is_file() and path.stat().st_size > 0 for path in report.figures)
    assert report.summary_json.is_file()
    saved = json.loads(report.summary_json.read_text())
    assert saved == report.summary
    assert saved["symbol"] == "EURUSD"
    assert saved["h1_rows_before"] >= saved["h1_rows_after"]


def test_report_rejects_unknown_holiday(complete_fx_h5: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not in this dataset"):
        create_eda_report(
            complete_fx_h5,
            tmp_path / "report",
            H1Options(market="fx"),
            holiday="Not A Holiday",
        )
