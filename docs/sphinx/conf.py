"""Конфигурация Sphinx: документация по коду Transit Pulse (autodoc + napoleon)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in ("", "packages/transit_core", "services/backend", "services/ml", "services/replayer"):
    sys.path.insert(0, str(ROOT / extra))

project = "Transit Pulse"
author = "Команда Transit Pulse"
language = "ru"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosummary",
]
autosummary_generate = True
autodoc_default_options = {"members": True, "undoc-members": False, "show-inheritance": True}
autodoc_typehints = "description"
# Тяжёлые зависимости не нужны для сборки документации.
autodoc_mock_imports = ["catboost", "lightgbm", "onnxruntime", "torch", "sklearn", "prometheus_client"]
html_theme = "furo"
html_title = "Transit Pulse — документация по коду"
html_static_path = ["_static"]
suppress_warnings = ["autosummary.import_cycle"]
