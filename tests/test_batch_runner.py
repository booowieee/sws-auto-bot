import pytest
from pathlib import Path
from src.batch_runner import BatchRunner


def test_batch_runner_load_plain_text(tmp_path: Path):
    file = tmp_path / "urls.txt"
    file.write_text(
        "QA-01: Moldovan: https://docs.google.com/forms/d/e/123/viewform\n"
        "https://docs.google.com/forms/d/e/456/viewform\n"
        "QA-03 Multi\n"
        "https://forms.gle/xyz789\n",
        encoding="utf-8",
    )

    items = BatchRunner.load_urls_from_file(file)
    assert len(items) == 3
    assert items[0][0] == "QA-01: Moldovan"
    assert items[0][1] == "https://docs.google.com/forms/d/e/123/viewform"
    assert items[1][1] == "https://docs.google.com/forms/d/e/456/viewform"
    assert items[2][0] == "QA-03 Multi"
    assert items[2][1] == "https://forms.gle/xyz789"


def test_batch_runner_load_csv_tsv(tmp_path: Path):
    file = tmp_path / "urls.csv"
    file.write_text(
        "2026-08-21\tQA-01 Test Form\thttps://docs.google.com/forms/d/e/123/viewform\thttps://docs.google.com/forms/d/123/edit\n"
        "2026-08-21\tQA-02 Test Form\thttps://docs.google.com/forms/d/e/456/viewform\thttps://docs.google.com/forms/d/456/edit\n",
        encoding="utf-8",
    )

    items = BatchRunner.load_urls_from_file(file)
    assert len(items) == 2
    assert items[0][0] == "QA-01 Test Form"
    assert items[0][1] == "https://docs.google.com/forms/d/e/123/viewform"
