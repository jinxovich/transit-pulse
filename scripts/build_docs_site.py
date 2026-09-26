"""Статический сайт документации для GitHub Pages.

Собирает в ``site/``:

* ``code/`` — Sphinx по коду (``docs/sphinx``);
* ``api/backend.html`` и ``api/ml.html`` — Swagger UI по OpenAPI backend и ML-сервиса,
  спецификации выгружаются из FastAPI-приложений без запуска сервисов;
* ``index.html`` — стартовая страница со ссылками на всё это и на документы в репозитории.

Пример::

    uv run python -m scripts.build_docs_site
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "site"
REPO_URL = "https://github.com/jinxovich/transit-pulse"
SWAGGER_CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5"

DOCS = (
    ("README", "README.md", "Запуск в Docker одной командой, карта модулей"),
    ("Для жюри", "docs/JURY_GUIDE.md", "Как подать поток, где прогнозы, алерты и метрики"),
    ("Производительность", "docs/PERFORMANCE.md", "Латентность, нагрузка, деградация, старт"),
    ("Возможности", "docs/FEATURES.md", "Доп. фичи ТЗ и сверх обязательного"),
    ("Аудит данных", "docs/DATA_AUDIT.md", "Найденная утечка и почему мы её не используем"),
    ("Горизонт на потоке", "docs/perf/stream_eval.md", "Сверка потоковых прогнозов с разметкой"),
    ("Сабмиты", "docs/SUBMISSIONS.md", "Журнал сабмитов и CV-метрики"),
    ("Интерфейсы модулей", "docs/INTERFACES.md", "Договор между backend, ML и replayer"),
)

SWAGGER_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{cdn}/swagger-ui.css">
</head>
<body>
<div id="swagger"></div>
<script src="{cdn}/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({{ url: "{spec}", dom_id: "#swagger", deepLinking: true,
  supportedSubmitMethods: [] }});
</script>
</body>
</html>
"""


def export_openapi(out: Path) -> dict[str, dict]:
    """Выгружает OpenAPI backend и ML-сервиса в ``out/api/*.json``."""
    from services.backend.app.main import app as backend
    from services.ml.app.serve import app as ml

    specs = {"backend": backend.openapi(), "ml": ml.openapi()}
    api = out / "api"
    api.mkdir(parents=True, exist_ok=True)
    for name, spec in specs.items():
        (api / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
        page = SWAGGER_PAGE.format(
            title=html.escape(spec["info"]["title"]), cdn=SWAGGER_CDN, spec=f"{name}.json"
        )
        (api / f"{name}.html").write_text(page, "utf-8")
    return specs


def build_sphinx(out: Path) -> None:
    """Собирает Sphinx по коду в ``out/code``."""
    subprocess.run(
        [sys.executable, "-m", "sphinx", "-q", "-b", "html", str(REPO_ROOT / "docs" / "sphinx"),
         str(out / "code")],
        check=True,
    )


def _card(title: str, href: str, note: str) -> str:
    return (
        f'<a class="card" href="{html.escape(href)}"><strong>{html.escape(title)}</strong>'
        f"<span>{html.escape(note)}</span></a>"
    )


def build_index(out: Path, specs: dict[str, dict]) -> None:
    """Стартовая страница сайта документации."""
    api_cards = [
        _card("Swagger backend", "api/backend.html",
              f"REST /api/v1, WebSocket-контракт — {len(specs['backend']['paths'])} путей"),
        _card("Swagger ML-сервиса", "api/ml.html",
              f"/predict с квантилями и вкладами — {len(specs['ml']['paths'])} пути"),
        _card("Документация по коду", "code/index.html", "Sphinx: transit_core, backend, replayer"),
    ]  # fmt: skip
    doc_cards = [_card(t, f"{REPO_URL}/blob/main/{p}", n) for t, p, n in DOCS]
    page = INDEX_TEMPLATE.format(repo=REPO_URL, api="".join(api_cards), docs="".join(doc_cards))
    (out / "index.html").write_text(page, "utf-8")
    (out / ".nojekyll").write_text("", "utf-8")


INDEX_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Pulse — документация</title>
<style>
:root {{ --bg: #0E1116; --panel: #161B22; --line: #262D36; --text: #E6EAF0; --muted: #8B93A1;
  --accent: #3E9BFF; --red: #E5484D; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.5 "IBM Plex Sans", system-ui, sans-serif; }}
main {{ max-width: 960px; margin: 0 auto; padding: 48px 16px 64px; }}
h1 {{ font-size: 40px; margin: 0 0 8px; letter-spacing: -0.02em; }}
h1 b {{ color: var(--red); }}
p.lead {{ color: var(--muted); margin: 0 0 40px; max-width: 640px; }}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted);
  margin: 40px 0 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }}
.card {{ display: flex; flex-direction: column; gap: 4px; padding: 16px; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; color: inherit; text-decoration: none;
  transition: border-color .15s, transform .15s; }}
.card:hover, .card:focus-visible {{ border-color: var(--accent); transform: translateY(-2px);
  outline: none; }}
.card span {{ color: var(--muted); font-size: 14px; }}
footer {{ margin-top: 48px; color: var(--muted); font-size: 14px; }}
footer a {{ color: var(--accent); }}
</style>
</head>
<body>
<main>
<h1>Transit <b>Pulse</b></h1>
<p class="lead">Система раннего предупреждения о задержках городского транспорта: поток NDTP →
сопоставление с ниткой графика → ML-прогноз задержки за 10–15 минут → алерт с причиной и
рекомендацией на дашборде диспетчера.</p>
<h2>API и код</h2>
<div class="grid">{api}</div>
<h2>Документы</h2>
<div class="grid">{docs}</div>
<footer>Исходный код и запуск: <a href="{repo}">{repo}</a></footer>
</main>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    shutil.rmtree(args.out, ignore_errors=True)
    args.out.mkdir(parents=True)
    specs = export_openapi(args.out)
    build_sphinx(args.out)
    build_index(args.out, specs)
    print(f"Готово: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
