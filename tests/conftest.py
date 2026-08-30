"""Общее для тестов: пути и загрузка скриптов, лежащих вне пакета."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = ROOT / "homeassistant" / "config" / "bin"


def load(path: pathlib.Path) -> types.ModuleType:
    """Импортирует одиночный .py по пути — у нас это не пакет, а скрипты для HA."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module