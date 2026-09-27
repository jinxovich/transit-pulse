import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from scripts import data_prep
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


def test_find_archive_finds_dataset_zip_in_nested_folder(tmp_path):
    nested = tmp_path / "Предиктор задержек транспорта" / "dataset.zip"
    nested.parent.mkdir()
    nested.write_bytes(_make_dataset_zip())

    assert find_archive(tmp_path) == nested


def test_main_succeeds_without_archive_when_dataset_already_extracted(tmp_path):
    dest = _write_extracted(tmp_path / "raw")

    assert data_prep.main(["--src", str(tmp_path / "missing.zip"), "--dest", str(dest)]) == 0


def test_main_fails_without_archive_and_without_extracted_dataset(tmp_path):
    args = ["--src", str(tmp_path / "missing.zip"), "--dest", str(tmp_path / "raw")]
    assert data_prep.main(args) == 1


# ------------------------------------------------ архива нет: распакованная папка


def _write_extracted(root: Path, with_emulator: bool = False) -> Path:
    for name in REQUIRED_FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("col\n1\n")
    if with_emulator:
        (root / "ndtp-telemetry-emulator.tar").write_bytes(b"TAR")
    return root


def test_find_extracted_locates_manually_unpacked_folder(tmp_path):
    data_dir = tmp_path / "data"
    root = _write_extracted(data_dir / "Предиктор задержек транспорта" / "dataset")

    assert data_prep.find_extracted(data_dir, data_dir / "raw") == root


def test_find_extracted_ignores_dest_and_incomplete_folders(tmp_path):
    data_dir = tmp_path / "data"
    _write_extracted(data_dir / "raw")
    partial = data_dir / "partial"
    (partial / "train").mkdir(parents=True)
    (partial / "train" / "traffic.csv").write_text("col\n1\n")

    assert data_prep.find_extracted(data_dir, data_dir / "raw") is None


def test_main_copies_manually_unpacked_dataset_into_dest(tmp_path, monkeypatch):
    _offline(monkeypatch)
    data_dir = tmp_path / "data"
    _write_extracted(data_dir / "unpacked", with_emulator=True)
    dest = data_dir / "raw"

    code = data_prep.main(["--data-dir", str(data_dir), "--dest", str(dest), "--with-emulator"])

    assert code == 0
    assert data_prep.is_extracted(dest)
    assert (dest / "ndtp-telemetry-emulator.tar").read_bytes() == b"TAR"
    assert not (dest / "train" / "traffic.csv").is_symlink()


# ------------------------------------------------ архива нет: скачивание с Яндекс.Диска

HREF = "https://downloader.test/dataset.zip"


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_length: int | None = None):
        super().__init__(body)
        self.headers = {"Content-Length": str(content_length or len(body))}


def _fake_yadisk(monkeypatch, archive_body: bytes, calls: list[str], content_length=None):
    def fake_urlopen(url, timeout=None):
        calls.append(url)
        assert timeout is not None, "сетевые вызовы должны быть с таймаутом"
        if url.startswith(data_prep.YADISK_DOWNLOAD_API):
            return _FakeResponse(json.dumps({"href": HREF}).encode())
        assert url == HREF
        return _FakeResponse(archive_body, content_length)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _offline(monkeypatch) -> None:
    def fail(url, timeout=None):
        raise urllib.error.URLError("network is unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fail)


def test_main_downloads_and_extracts_dataset_when_nothing_local(tmp_path, monkeypatch, capsys):
    calls: list[str] = []
    _fake_yadisk(monkeypatch, _make_dataset_zip(), calls)
    monkeypatch.setenv("DATASET_PUBLIC_URL", "https://disk.yandex.ru/d/TEST")
    data_dir = tmp_path / "data"
    dest = data_dir / "raw"

    code = data_prep.main(["--data-dir", str(data_dir), "--dest", str(dest), "--with-emulator"])

    assert code == 0
    assert "public_key=https%3A%2F%2Fdisk.yandex.ru%2Fd%2FTEST" in calls[0]
    assert "path=%2Fdataset.zip" in calls[0]
    assert calls[1] == HREF
    assert (data_dir / "dataset.zip").is_file()
    assert not (data_dir / "dataset.zip.part").exists()
    assert data_prep.is_extracted(dest)
    assert (dest / "ndtp-telemetry-emulator.tar").read_bytes() == b"TAR"
    assert "100%" in capsys.readouterr().out


def test_download_rejects_non_zip_and_leaves_no_files(tmp_path, monkeypatch):
    _fake_yadisk(monkeypatch, b"<html>captcha</html>", [])

    with pytest.raises(data_prep.DownloadError, match="не zip"):
        data_prep.download_dataset(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_download_fails_on_truncated_body(tmp_path, monkeypatch):
    _fake_yadisk(monkeypatch, b"PK\x03\x04short", [], content_length=1000)

    with pytest.raises(data_prep.DownloadError, match="оборвалась"):
        data_prep.download_dataset(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_main_skips_download_when_disabled_by_env(tmp_path, monkeypatch, capsys):
    calls: list[str] = []
    _fake_yadisk(monkeypatch, _make_dataset_zip(), calls)
    monkeypatch.setenv("DATASET_AUTO_DOWNLOAD", "0")

    code = data_prep.main(["--data-dir", str(tmp_path), "--dest", str(tmp_path / "raw")])

    assert code == 1
    assert calls == []
    assert "DATASET_AUTO_DOWNLOAD=0" in capsys.readouterr().err


def test_main_explains_where_to_put_dataset_when_offline(tmp_path, monkeypatch, capsys):
    _offline(monkeypatch)
    monkeypatch.delenv("DATASET_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATASET_AUTO_DOWNLOAD", raising=False)

    code = data_prep.main(["--data-dir", str(tmp_path), "--dest", str(tmp_path / "raw")])

    assert code != 0
    err = capsys.readouterr().err
    assert "положите архив датасета" in err.lower()
    assert "./data" in err
    assert data_prep.DEFAULT_PUBLIC_URL in err
    assert list(tmp_path.iterdir()) == []
