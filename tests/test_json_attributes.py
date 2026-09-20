"""
Атрибут, который читают шаблоны, обязан быть объявлен в json_attributes.

Сенсоры command_line печатают JSON, но Home Assistant забирает из него
только то, что перечислено в `json_attributes`. Всё остальное молча
выбрасывается: `state_attr()` вернёт `None`, шаблон с `| int(0)` превратит
его в ноль, и признак никогда не станет истинным.

Ровно это и случилось. Скрипт docker_state.py печатал `restarting`,
пакет его не объявлял, и binary_sensor.docker_restarting не мог
сработать ни при каких обстоятельствах — вместе с автоматикой, которая
на нём висит. Заметить это по интерфейсу нельзя: сенсор жив, значение
ноль, и ноль выглядит как «перезапусков нет».

Проверка идёт с двух сторон, потому что расходиться они могут в обе:

1. Каждый `state_attr('sensor.X', 'attr')` из пакетов и дашборда — если
   sensor.X создаётся здесь же сенсором command_line, атрибут обязан быть
   в его json_attributes.
2. Каждый ключ, который печатает скрипт, — или объявлен, или сознательно
   не нужен. Второе допустимо: не всякое поле JSON кому-то требуется.
   Поэтому вторая проверка мягче первой и только перечисляет лишнее.
"""

from __future__ import annotations

import pathlib
import re

import yaml
from conftest import ROOT

PACKAGES = ROOT / "homeassistant" / "config" / "packages"
DASHBOARD = ROOT / "homeassistant" / "dashboard-infrastructure.yaml"
BIN = ROOT / "homeassistant" / "config" / "bin"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def slug(name: str) -> str:
    """Имя сенсора → entity_id, как это делает Home Assistant."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def command_line_sensors() -> dict[str, set[str]]:
    """entity_id → объявленные json_attributes."""
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGES.glob("*.yaml")):
        data = yaml.load(path.read_text(encoding="utf-8"), Loader) or {}
        for entry in data.get("command_line") or []:
            for kind in ("sensor", "binary_sensor"):
                block = (entry or {}).get(kind)
                if not isinstance(block, dict) or not block.get("name"):
                    continue
                found[f"{kind}.{slug(block['name'])}"] = set(
                    block.get("json_attributes") or []
                )
    return found


def read_attributes() -> set[tuple[str, str]]:
    """Все пары (сущность, атрибут) из state_attr() в конфигурации."""
    template = re.compile(r"state_attr\(\s*['\"]([\w.]+)['\"]\s*,\s*['\"]([\w]+)['\"]")
    pairs: set[tuple[str, str]] = set()
    paths = [*sorted(PACKAGES.glob("*.yaml")), DASHBOARD]
    for path in paths:
        pairs.update(template.findall(path.read_text(encoding="utf-8")))
    return pairs


def test_read_attributes_are_declared():
    """
    Главная проверка: шаблон читает атрибут, которого сенсор не забирает.
    Молчаливее ошибки не бывает — вместо значения приходит None.
    """
    sensors = command_line_sensors()
    assert sensors, "не нашли ни одного сенсора command_line — проверка стала пустой"
    pairs = read_attributes()
    assert pairs, "не нашли ни одного state_attr() — проверка стала пустой"

    checked = 0
    for entity, attribute in sorted(pairs):
        if entity not in sensors:
            continue  # сущность из интеграции, а не наша — судить не можем
        checked += 1
        assert attribute in sensors[entity], (
            f"шаблон читает {entity}.{attribute}, но в json_attributes его нет — "
            f"Home Assistant вернёт None. Объявлено: {sorted(sensors[entity])}"
        )
    assert checked >= 3, f"сверено всего {checked} пар — проверка почти пустая"


def test_script_prints_what_is_read():
    """
    Обратная сторона: ключ печатается, но не объявлен. Это не всегда
    ошибка — поле может быть никому не нужно, — поэтому здесь проверяется
    только то, что объявленного нет сверх напечатанного: объявить ключ,
    которого скрипт не печатает, значит ждать атрибут, который не придёт.
    """
    script = (BIN / "docker_state.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'"(\w+)":', script))
    assert keys, "в docker_state.py не нашлось ни одного ключа JSON"

    declared = command_line_sensors().get("sensor.docker_down")
    assert declared, "пропал сенсор sensor.docker_down"
    extra = declared - keys
    assert not extra, (
        f"в json_attributes объявлены ключи, которых docker_state.py "
        f"не печатает: {sorted(extra)}"
    )


def test_script_path_exists():
    """
    Команда сенсора указывает на файл в /config/bin — в репозитории это
    homeassistant/config/bin. Опечатка в пути даёт сенсор, который всегда
    отдаёт пустую строку.
    """
    for path in sorted(PACKAGES.glob("*.yaml")):
        data = yaml.load(path.read_text(encoding="utf-8"), Loader) or {}
        for entry in data.get("command_line") or []:
            for kind in ("sensor", "binary_sensor"):
                block = (entry or {}).get(kind)
                if not isinstance(block, dict):
                    continue
                for path_str in re.findall(r"/config/(bin/[\w.]+)", block.get("command", "")):
                    target = ROOT / "homeassistant" / "config" / pathlib.Path(path_str)
                    assert target.exists(), f"{path.name}: команда зовёт {path_str}, которого нет"
