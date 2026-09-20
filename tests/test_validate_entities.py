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

PACKAGES = sorted((ROOT / "homeassistant" / "config" / "packages").glob("*.yaml"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("action", "script.pachca_notify"),
        ("action", "script.tv_off"),
        ("service", "script.tv_jellyfin"),
        ("entity_id", "sensor.docker_down"),
        ("state", "binary_sensor.tv_idle"),
    ],
)
def test_entities_pass(key: str, value: str) -> None:
    assert ve.looks_like_entity(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
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
def test_services_and_types_do_not_pass(key: str, value: str) -> None:
    assert not ve.looks_like_entity(key, value)


def test_script_call_is_visible_to_the_walk() -> None:
    """Та самая форма записи, которую обход выбрасывал целиком."""
    chunk = {
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
    found = set(ve.walk_strings(chunk))
    assert "script.pachca_notify" in found
    assert "input_boolean.turn_off" not in found
    assert "input_boolean.flag" in found


def test_every_called_script_is_defined() -> None:
    """
    Регрессия во весь репозиторий: имя скрипта, вызванного через action,
    обязано существовать. Проверка идёт по тексту файлов, мимо обхода,
    чтобы тест не повторял ту же ошибку, что и проверяемый код.
    """
    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!secret", "!include", "!include_dir_named", "!include_dir_list",
                "!include_dir_merge_list", "!include_dir_merge_named", "!env_var"):
        Loader.add_constructor(tag, lambda loader, node: None)

    docs = {path_str: yaml.load(path_str.read_text(encoding="utf-8"), Loader=Loader) for path_str in PACKAGES}
    defined, _ = ve.collect_defined(docs)

    called: dict[str, str] = {}
    for path_str in PACKAGES:
        for name in re.findall(r"(?:action|service):\s*(script\.[a-z0-9_]+)",
                              path_str.read_text(encoding="utf-8")):
            if name.split(".", 1)[1] not in ve.SCRIPT_SERVICES:
                called[name] = path_str.name

    assert called, "в конфигурации не нашлось ни одного вызова скрипта — тест бесполезен"
    missing = {name: path for name, path in called.items() if name not in defined}
    assert not missing, f"вызываются несуществующие скрипты: {missing}"
