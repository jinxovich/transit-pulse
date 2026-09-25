"""Распаковка датасета организаторов в ``data/raw``.

Понимает внешний архив с Яндекс.Диска (кириллическое имя, внутри ``dataset.zip``)
и сам ``dataset.zip``. По умолчанию достаёт только CSV и документацию; образ
эмулятора (134 МБ) — по флагу ``--with-emulator``.

Пример::

    uv run python -m scripts.data_prep --src "~/Downloads/Предиктор задержек транспорта.zip"
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DEST = DEFAULT_DATA_DIR / "raw"
EMULATOR_TAR = "ndtp-telemetry-emulator.tar"

REQUIRED_FILES = (
    "train/traffic.csv",
    "train/schedule.csv",
    "test/traffic.csv",
    "test/schedule.csv",
    "labels/labels_train.csv",
    "labels/labels_test.csv",
    "validate/traffic.csv",
    "validate/schedule_plan.csv",
    "validate/points.csv",
    "sample_submission.csv",
)
OPTIONAL_FILES = ("README.md", "docs/Emulator-and-Telematic-Packets-Specification.md")


def find_archive(data_dir: Path = DEFAULT_DATA_DIR) -> Path | None:
    """Возвращает первый ``*.zip`` в каталоге данных или ``None``."""
    archives = sorted(data_dir.glob("*.zip"))
    return archives[0] if archives else None


def _open_dataset(archive: Path) -> zipfile.ZipFile:
    """Открывает ``dataset.zip``: напрямую или изнутри внешнего архива."""
    outer = zipfile.ZipFile(archive)
    names = outer.namelist()
    if any(n.endswith(REQUIRED_FILES[0]) for n in names):
        return outer
    nested = [n for n in names if n.endswith("dataset.zip")]
    if not nested:
        return outer
    # Вложенный zip читаем в память: ZipExtFile плохо переносит случайный доступ.
    return zipfile.ZipFile(io.BytesIO(outer.read(nested[0])))


def _member_index(z: zipfile.ZipFile) -> dict[str, str]:
    """Сопоставляет относительные пути файлов датасета с именами внутри архива."""
    index: dict[str, str] = {}
    for name in z.namelist():
        for wanted in (*REQUIRED_FILES, *OPTIONAL_FILES, EMULATOR_TAR):
            if name == wanted or name.endswith("/" + wanted):
                index[wanted] = name
    return index


def extract_dataset(
    archive: Path, dest: Path = DEFAULT_DEST, with_emulator: bool = False
) -> list[Path]:
    """Распаковывает нужные файлы датасета в ``dest`` и возвращает их пути.

    Raises:
        FileNotFoundError: в архиве нет обязательных файлов датасета.
    """
    with _open_dataset(archive) as z:
        index = _member_index(z)
        missing = [f for f in REQUIRED_FILES if f not in index]
        if missing:
            raise FileNotFoundError(f"В архиве {archive.name} нет файлов: {', '.join(missing)}")
        wanted = [*REQUIRED_FILES, *(f for f in OPTIONAL_FILES if f in index)]
        if with_emulator and EMULATOR_TAR in index:
            wanted.append(EMULATOR_TAR)
        written = []
        for rel in wanted:
            target = dest / rel
            info = z.getinfo(index[rel])
            if target.exists() and target.stat().st_size == info.file_size:
                written.append(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
            written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--src", type=Path, help="архив датасета (по умолчанию первый *.zip в ./data)"
    )
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--with-emulator", action="store_true", help="достать и образ эмулятора")
    args = parser.parse_args(argv)

    archive = args.src.expanduser() if args.src else find_archive()
    if archive is None or not archive.exists():
        print("Архив датасета не найден: положите его в ./data/ или укажите --src", file=sys.stderr)
        return 1
    files = extract_dataset(archive, args.dest, with_emulator=args.with_emulator)
    print(f"Готово: {len(files)} файлов в {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
