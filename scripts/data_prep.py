"""Подготовка датасета организаторов в ``data/raw``.

Порядок поиска: архив ``*.zip`` в ``./data`` (кириллический внешний архив
с Яндекс.Диска или сам ``dataset.zip``) → уже распакованная руками папка
где-то внутри ``./data`` → скачивание ``dataset.zip`` с публичного
Яндекс.Диска (выключается ``DATASET_AUTO_DOWNLOAD=0``). По умолчанию достаёт
только CSV и документацию; образ эмулятора (134 МБ) — по флагу ``--with-emulator``.

Пример::

    uv run python -m scripts.data_prep --src "~/Downloads/Предиктор задержек транспорта.zip"
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DEST = DEFAULT_DATA_DIR / "raw"
EMULATOR_TAR = "ndtp-telemetry-emulator.tar"

DEFAULT_PUBLIC_URL = "https://disk.yandex.ru/d/CA6tsj4aJJ4Aaw"
YADISK_DOWNLOAD_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
PUBLIC_ARCHIVE_PATH = "/dataset.zip"
DOWNLOAD_NAME = "dataset.zip"
HTTP_TIMEOUT_S = 60
CHUNK_BYTES = 1 << 20
PROGRESS_STEP_PCT = 10

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


class DownloadError(RuntimeError):
    """Датасет не удалось скачать с Яндекс.Диска."""


def _log(msg: str) -> None:
    print(msg, flush=True)


def find_archive(data_dir: Path = DEFAULT_DATA_DIR) -> Path | None:
    """Возвращает архив датасета в каталоге данных или ``None``.

    Сначала первый ``*.zip`` в самом каталоге, затем ``dataset.zip`` во вложенных
    папках (внешний архив Яндекс.Диска распаковали, а внутренний — нет).
    """
    archives = sorted(data_dir.glob("*.zip"))
    if archives:
        return archives[0]
    nested = sorted(data_dir.rglob(DOWNLOAD_NAME))
    return nested[0] if nested else None


def is_extracted(dest: Path) -> bool:
    """Проверяет, что все обязательные файлы датасета уже лежат в ``dest``."""
    return all((dest / name).is_file() for name in REQUIRED_FILES)


def find_extracted(data_dir: Path = DEFAULT_DATA_DIR, dest: Path = DEFAULT_DEST) -> Path | None:
    """Ищет распакованный руками датасет внутри ``data_dir`` (кроме самого ``dest``).

    Корень датасета — папка, где лежат все ``REQUIRED_FILES``; опознаётся по
    ``train/traffic.csv``.
    """
    if not data_dir.is_dir():
        return None
    dest_resolved = dest.resolve()
    marker = Path(REQUIRED_FILES[0])
    for hit in sorted(data_dir.rglob(marker.name)):
        if hit.parent.name != marker.parent.name:
            continue
        root = hit.parent.parent
        if root.resolve() != dest_resolved and is_extracted(root):
            return root
    return None


def copy_extracted(src: Path, dest: Path = DEFAULT_DEST, with_emulator: bool = False) -> list[Path]:
    """Копирует файлы распакованного датасета из ``src`` в ``dest``.

    Копия, а не симлинк: ``dest`` монтируется в другие контейнеры отдельно
    от остального ``./data``, и ссылка наружу там не разрешится.
    """
    wanted = [*REQUIRED_FILES, *(f for f in OPTIONAL_FILES if (src / f).is_file())]
    if with_emulator and (src / EMULATOR_TAR).is_file():
        wanted.append(EMULATOR_TAR)
    written = []
    for rel in wanted:
        source, target = src / rel, dest / rel
        if not (target.exists() and target.stat().st_size == source.stat().st_size):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        written.append(target)
    return written


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
                while chunk := src.read(CHUNK_BYTES):
                    out.write(chunk)
            written.append(target)
    return written


def auto_download_enabled() -> bool:
    """Скачивание включено, пока ``DATASET_AUTO_DOWNLOAD`` не выставлен в 0/false/no."""
    value = os.environ.get("DATASET_AUTO_DOWNLOAD", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def public_url() -> str:
    """Публичная ссылка на папку датасета (``DATASET_PUBLIC_URL`` или ссылка организаторов)."""
    return os.environ.get("DATASET_PUBLIC_URL", "").strip() or DEFAULT_PUBLIC_URL


def _resolve_href(public_key: str) -> str:
    """Спрашивает у API Яндекс.Диска прямую ссылку на ``dataset.zip``."""
    query = urllib.parse.urlencode({"public_key": public_key, "path": PUBLIC_ARCHIVE_PATH})
    with urllib.request.urlopen(f"{YADISK_DOWNLOAD_API}?{query}", timeout=HTTP_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    href = payload.get("href") if isinstance(payload, dict) else None
    if not href:
        raise DownloadError(f"API Яндекс.Диска не вернул ссылку на файл: {payload}")
    return href


def _stream_to_file(href: str, part: Path) -> int:
    """Качает ``href`` в ``part`` с прогрессом в лог; возвращает число байт."""
    with urllib.request.urlopen(href, timeout=HTTP_TIMEOUT_S) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, next_pct = 0, PROGRESS_STEP_PCT
        while chunk := resp.read(CHUNK_BYTES):
            out.write(chunk)
            done += len(chunk)
            if total and done * 100 >= next_pct * total:
                _log(f"  скачано {done * 100 // total}% ({done >> 20} из {total >> 20} МБ)")
                next_pct = (done * 100 // total // PROGRESS_STEP_PCT + 1) * PROGRESS_STEP_PCT
    if total and done != total:
        raise DownloadError(f"Загрузка оборвалась: получено {done} из {total} байт")
    return done


def download_dataset(data_dir: Path = DEFAULT_DATA_DIR, public_key: str | None = None) -> Path:
    """Скачивает ``dataset.zip`` с публичного Яндекс.Диска в ``data_dir``.

    Пишет во временный ``*.part`` и атомарно переименовывает после проверки,
    что это zip, — оборванная загрузка не оставит битый архив.

    Raises:
        DownloadError: сеть недоступна, API ответил ошибкой или файл битый.
    """
    public_key = public_key or public_url()
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / DOWNLOAD_NAME
    part = data_dir / f"{DOWNLOAD_NAME}.part"
    _log(f"Скачиваю датасет с {public_key} в {target} (~150 МБ)…")
    try:
        size = _stream_to_file(_resolve_href(public_key), part)
        if not zipfile.is_zipfile(part):
            raise DownloadError("скачанный файл — не zip-архив")
    except (OSError, ValueError, DownloadError) as exc:  # URLError/HTTPError/таймаут — OSError
        part.unlink(missing_ok=True)
        raise DownloadError(str(exc)) from exc
    os.replace(part, target)
    _log(f"Скачано {size >> 20} МБ: {target}")
    return target


def _missing_dataset_message(data_dir: Path, reason: str) -> str:
    return (
        f"Датасет не найден ({reason}). Положите архив датасета (*.zip) или распакованную "
        f"папку в ./data (сейчас ищем в {data_dir}); ссылка: {public_url()}"
    )


def _prepare_without_archive(data_dir: Path, dest: Path, with_emulator: bool) -> int:
    """Нет архива и ``dest`` не готов: распакованная папка → скачивание → ошибка."""
    extracted = find_extracted(data_dir, dest)
    if extracted is not None:
        files = copy_extracted(extracted, dest, with_emulator=with_emulator)
        _log(f"Готово: {len(files)} файлов из распакованной папки {extracted} в {dest}")
        return 0
    if not auto_download_enabled():
        reason = "автоскачивание выключено: DATASET_AUTO_DOWNLOAD=0"
        print(_missing_dataset_message(data_dir, reason), file=sys.stderr)
        return 1
    try:
        archive = download_dataset(data_dir)
    except DownloadError as exc:
        reason = f"скачать не удалось: {exc}"
        print(_missing_dataset_message(data_dir, reason), file=sys.stderr)
        return 1
    files = extract_dataset(archive, dest, with_emulator=with_emulator)
    _log(f"Готово: {len(files)} файлов в {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--src", type=Path, help="архив датасета (по умолчанию первый *.zip в каталоге данных)"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="каталог данных")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--with-emulator", action="store_true", help="достать и образ эмулятора")
    args = parser.parse_args(argv)

    archive = args.src.expanduser() if args.src else find_archive(args.data_dir)
    if archive is not None and archive.exists():
        files = extract_dataset(archive, args.dest, with_emulator=args.with_emulator)
        _log(f"Готово: {len(files)} файлов в {args.dest}")
        return 0
    if is_extracted(args.dest):
        _log(f"Датасет уже распакован в {args.dest}, архив не нужен")
        return 0
    if args.src:
        print(f"Архив {archive} не найден", file=sys.stderr)
        return 1
    return _prepare_without_archive(args.data_dir, args.dest, args.with_emulator)


if __name__ == "__main__":
    sys.exit(main())
