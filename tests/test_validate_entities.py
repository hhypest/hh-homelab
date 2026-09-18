"""
Что считать ссылкой на сущность, а что — именем службы.

Скрипт сверяет ссылки на сущности и существует ради одного класса ошибок:
опечатка в entity_id загружается молча, а автоматизация просто никогда
не срабатывает. Против этого же класса он и был слеп.

Ключи action и service целиком лежали в списке «здесь сущностей не бывает»,
потому что «action: input_boolean.turn_off» — это имя службы. Но в Home
Assistant 2024.8+ «action: script.имя» — основная форма вызова скрипта,
и в этом репозитории через неё проходят все семнадцать обращений. Опечатка
в любом из них проходила проверку с нулевым кодом возврата.

Отличать нужно не по ключу, а по второй части: в домене script служб всего
четыре — turn_on, turn_off, toggle, reload, — а любое другое имя за точкой
уже сущность. Здесь зафиксировано именно это правило, по одному тесту
на каждую сторону границы.
"""

from __future__ import annotations

import re

import pytest
import yaml
from conftest import ROOT, load

ve = load(ROOT / "scripts" / "validate_entities.py")

ПАКЕТЫ = sorted((ROOT / "homeassistant" / "config" / "packages").glob("*.yaml"))


@pytest.mark.parametrize(
    ("ключ", "значение"),
    [
        ("action", "script.pachca_notify"),
        ("action", "script.tv_off"),
        ("service", "script.tv_jellyfin"),
        ("entity_id", "sensor.docker_down"),
        ("state", "binary_sensor.tv_idle"),
    ],
)
def test_сущности_проходят(ключ: str, значение: str) -> None:
    assert ve.looks_like_entity(ключ, значение)


@pytest.mark.parametrize(
    ("ключ", "значение"),
    [
        # Службы: имя за точкой — действие, а не объект.
        ("action", "input_boolean.turn_off"),
        ("action", "media_player.media_pause"),
        ("action", "notify.lg_tv"),
        ("action", "script.turn_on"),
        ("action", "script.reload"),
        ("service", "homeassistant.update_entity"),
        # Не вызовы вовсе: платформа, тип триггера, класс устройства.
        ("platform", "template"),
        ("trigger", "state"),
        ("device_class", "problem"),
    ],
)
def test_службы_и_типы_не_проходят(ключ: str, значение: str) -> None:
    assert not ve.looks_like_entity(ключ, значение)


def test_вызов_скрипта_виден_обходу() -> None:
    """Та самая форма записи, которую обход выбрасывал целиком."""
    кусок = {
        "automation": [
            {
                "id": "demo",
                "actions": [
                    {"action": "script.pachca_notify", "data": {"title": "тест"}},
                    {"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.flag"}},
                ],
            }
        ]
    }
    найдено = set(ve.walk_strings(кусок))
    assert "script.pachca_notify" in найдено
    assert "input_boolean.turn_off" not in найдено
    assert "input_boolean.flag" in найдено


def test_каждый_вызванный_скрипт_определён() -> None:
    """
    Регрессия во весь репозиторий: имя скрипта, вызванного через action,
    обязано существовать. Проверка идёт по тексту файлов, мимо обхода,
    чтобы тест не повторял ту же ошибку, что и проверяемый код.
    """
    class Loader(yaml.SafeLoader):
        pass

    for тег in ("!secret", "!include", "!include_dir_named", "!include_dir_list",
                "!include_dir_merge_list", "!include_dir_merge_named", "!env_var"):
        Loader.add_constructor(тег, lambda loader, node: None)

    docs = {путь: yaml.load(путь.read_text(encoding="utf-8"), Loader=Loader) for путь in ПАКЕТЫ}
    определено, _ = ve.collect_defined(docs)

    вызвано: dict[str, str] = {}
    for путь in ПАКЕТЫ:
        for имя in re.findall(r"(?:action|service):\s*(script\.[a-z0-9_]+)",
                              путь.read_text(encoding="utf-8")):
            if имя.split(".", 1)[1] not in ve.SCRIPT_SERVICES:
                вызвано[имя] = путь.name

    assert вызвано, "в конфигурации не нашлось ни одного вызова скрипта — тест бесполезен"
    отсутствуют = {имя: файл for имя, файл in вызвано.items() if имя not in определено}
    assert not отсутствуют, f"вызываются несуществующие скрипты: {отсутствуют}"
