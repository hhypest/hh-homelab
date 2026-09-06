"""
Граница восстановления: из git приезжает конфигурация, состояние — нет.

Замечание R-01 независимого аудита закрыто решением владельца: репозиторий
отвечает за то, КАК всё настроено, а накопленное состояние (базы Radarr,
метаданные Jellyfin, пользователи Home Assistant) — за бэкапами NAS.

Решение записано в README и в разделе 11 руководства. Проблема утверждений
в документации в том, что они дешевеют: достаточно одного `git add -f`
или правки .gitignore, и написанное перестаёт быть правдой, не переставая
быть написанным. Поэтому граница проверяется, а не декларируется.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import ROOT

# Ровно то, что перечислено в таблице README. Пути состояния: они обязаны
# оставаться вне контроля версий.
STATE = [
    "homeassistant/config/.storage/",
    "homeassistant/config/home-assistant_v2.db",
    "homeassistant/config/custom_components/",
    "homeassistant/config/secrets.yaml",
    "media/config/",
    "media/data/",
    "media/.env",
    "homeassistant/.env",
]

# А это — конфигурация: она обязана быть в git, иначе восстанавливать нечего.
CONFIG = [
    "media/compose.yaml",
    "homeassistant/compose.yaml",
    "media/.env.example",
    "homeassistant/.env.example",
    "homeassistant/config/configuration.yaml",
]


def tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


@pytest.mark.parametrize("path", STATE)
def test_state_is_ignored(path: str) -> None:
    done = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True, text=True,
    )
    assert done.returncode == 0, (
        f"{path} не игнорируется. README обещает, что состояние в git не попадает, — "
        f"проверьте .gitignore, иначе обещание стало неправдой"
    )


@pytest.mark.parametrize("path", STATE)
def test_nothing_from_state_is_tracked(path: str) -> None:
    """
    Правило в .gitignore не действует на файл, который уже в индексе:
    один `git add -f` — и состояние едет в публичный репозиторий вместе
    с ключами API, которые в нём лежат.
    """
    prefix = path.rstrip("/")
    leaked = [name for name in tracked() if name == prefix or name.startswith(prefix + "/")]
    assert not leaked, (
        f"под {path} есть файлы под контролем версий: {', '.join(leaked[:5])}"
    )


@pytest.mark.parametrize("path", CONFIG)
def test_configuration_is_tracked(path: str) -> None:
    """Обратная половина обещания: конфигурация обязана восстанавливаться."""
    assert path in tracked(), (
        f"{path} нет в git — из чистого клона стек не поднимется, "
        f"а README обещает обратное"
    )


def test_readme_names_the_same_paths() -> None:
    """
    Таблица в README и этот список обязаны говорить об одном и том же.
    Разъедутся — проверка начнёт охранять не то, что обещано читателю.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Что репозиторий восстанавливает")[1].split("\n## ")[0]
    for path in ("config/.storage/", "home-assistant_v2.db", "media/config/", "media/data/"):
        assert path in section, f"README больше не упоминает {path}"
