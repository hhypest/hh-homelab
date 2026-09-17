"""
Проверки версий образов в compose-файлах.

Плавающий тег — это отложенная поломка: `docker compose pull` однажды
принесёт мажорное обновление с изменённым форматом базы, и узнаете вы
об этом по нерабочему сервису вечером. Причём непонятно даже, какой
именно из восьми виноват, если обновились сразу несколько.

Поэтому версии закреплены, а обновления приходят пул-реквестом
от Dependabot. Здесь — то, что легко нарушить одной правкой: вернуть
:latest «на время» либо добавить сервис и забыть про тег.
"""

from __future__ import annotations

import re

import pytest
import yaml
from conftest import ROOT, load

# Разбор образов живёт в скрипте сверки версий и берётся оттуда, а не
# повторяется здесь: список образов, который проверяет тест, и список,
# который сверяется с реестром, обязаны быть одним и тем же. Раньше
# разбор был написан дважды, и разойтись они могли молча.
#
# Ссылки прячутся не только в ключе image: у linuxserver-образов есть
# DOCKER_MODS, и перечисленные там образы init скачивает при КАЖДОМ старте
# контейнера, а не при `compose pull`. Плавающий тег здесь опаснее обычного —
# версия меняется от простого перезапуска, и `pull` этого даже не покажет.
ci = load(ROOT / "scripts" / "check_image_updates.py")
images = ci.images
COMPOSE = ci.COMPOSE

# Теги, которые указывают не на версию, а на «то, что сейчас новее всего».
FLOATING = {"latest", "stable", "dev", "develop", "nightly", "edge", "main", "master"}


@pytest.mark.parametrize("path", COMPOSE)
def test_every_image_is_pinned(path: str) -> None:
    """У каждого образа есть тег, и этот тег — не плавающий."""
    assert images(path), f"{path}: не нашлось ни одного образа — проверка бесполезна"
    for name, image in images(path).items():
        # Отрезаем реестр с портом: двоеточие в нём — не разделитель тега.
        tail = image.rsplit("/", 1)[-1]
        assert ":" in tail, f"{path}: у {name} образ без тега — это тот же :latest"
        tag = tail.rsplit(":", 1)[1]
        when = "при следующем старте контейнера" if "·" in name else "в момент очередного pull"
        assert tag.lower() not in FLOATING, (
            f"{path}: {name} стоит на плавающем теге «{tag}». "
            f"Обновление придёт молча, {when}"
        )
        assert re.search(r"\d", tag), (
            f"{path}: тег «{tag}» у {name} не содержит ни одной цифры — "
            f"на версию это не похоже"
        )


def test_dependabot_watches_every_compose_directory() -> None:
    """
    Закрепление версий имеет смысл, только если кто-то приносит обновления.
    Забыть каталог в dependabot.yml — значит тихо остаться на версиях
    того дня, когда их закрепили.
    """
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    watched: set[str] = set()
    for update in config.get("updates") or []:
        if update.get("package-ecosystem") != "docker-compose":
            continue
        watched.update(update.get("directories") or [])
        if "directory" in update:
            watched.add(update["directory"])

    for path in COMPOSE:
        directory = "/" + path.rsplit("/", 1)[0]
        assert directory in watched, (
            f"{directory} не указан в docker-compose-разделе .github/dependabot.yml — "
            f"обновления образов оттуда приходить не будут"
        )
