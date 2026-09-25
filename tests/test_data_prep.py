import io
import zipfile
from pathlib import Path

import pytest

from scripts.data_prep import REQUIRED_FILES, extract_dataset, find_archive


def _make_dataset_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in REQUIRED_FILES:
            z.writestr(name, "col\n1\n")
        z.writestr("ndtp-telemetry-emulator.tar", b"TAR")
    return buf.getvalue()


def _make_outer_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Предиктор задержек транспорта/dataset.zip", _make_dataset_zip())
    return path


def test_extracts_csv_from_nested_organizer_archive(tmp_path):
    archive = _make_outer_zip(tmp_path / "Предиктор задержек транспорта.zip")
    dest = tmp_path / "raw"

    extract_dataset(archive, dest)

    for name in REQUIRED_FILES:
        assert (dest / name).read_text() == "col\n1\n"
    assert not (dest / "ndtp-telemetry-emulator.tar").exists()


def test_extracts_plain_dataset_zip_and_emulator_on_request(tmp_path):
    archive = tmp_path / "dataset.zip"
    archive.write_bytes(_make_dataset_zip())
    dest = tmp_path / "raw"

    extract_dataset(archive, dest, with_emulator=True)

    assert (dest / "train" / "traffic.csv").exists()
    assert (dest / "ndtp-telemetry-emulator.tar").read_bytes() == b"TAR"


def test_rejects_archive_without_required_files(tmp_path):
    archive = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", "nothing here")

    with pytest.raises(FileNotFoundError, match="train/traffic.csv"):
        extract_dataset(archive, tmp_path / "raw")


def test_find_archive_picks_zip_in_data_dir(tmp_path):
    assert find_archive(tmp_path) is None
    archive = _make_outer_zip(tmp_path / "any-name.zip")
    assert find_archive(tmp_path) == archive
