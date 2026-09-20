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

ПАКЕТЫ = ROOT / "homeassistant" / "config" / "packages"
ДАШБОРД = ROOT / "homeassistant" / "dashboard-infrastructure.yaml"
BIN = ROOT / "homeassistant" / "config" / "bin"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def слаг(имя: str) -> str:
    """Имя сенсора → entity_id, как это делает Home Assistant."""
    return re.sub(r"[^a-z0-9]+", "_", имя.lower()).strip("_")


def командные_сенсоры() -> dict[str, set[str]]:
    """entity_id → объявленные json_attributes."""
    найдено: dict[str, set[str]] = {}
    for файл in sorted(ПАКЕТЫ.glob("*.yaml")):
        данные = yaml.load(файл.read_text(encoding="utf-8"), Loader) or {}
        for запись in данные.get("command_line") or []:
            for вид in ("sensor", "binary_sensor"):
                блок = (запись or {}).get(вид)
                if not isinstance(блок, dict) or not блок.get("name"):
                    continue
                найдено[f"{вид}.{слаг(блок['name'])}"] = set(
                    блок.get("json_attributes") or []
                )
    return найдено


def прочитанные_атрибуты() -> set[tuple[str, str]]:
    """Все пары (сущность, атрибут) из state_attr() в конфигурации."""
    шаблон = re.compile(r"state_attr\(\s*['\"]([\w.]+)['\"]\s*,\s*['\"]([\w]+)['\"]")
    пары: set[tuple[str, str]] = set()
    файлы = [*sorted(ПАКЕТЫ.glob("*.yaml")), ДАШБОРД]
    for файл in файлы:
        пары.update(шаблон.findall(файл.read_text(encoding="utf-8")))
    return пары


def test_читаемые_атрибуты_объявлены():
    """
    Главная проверка: шаблон читает атрибут, которого сенсор не забирает.
    Молчаливее ошибки не бывает — вместо значения приходит None.
    """
    сенсоры = командные_сенсоры()
    assert сенсоры, "не нашли ни одного сенсора command_line — проверка стала пустой"
    пары = прочитанные_атрибуты()
    assert пары, "не нашли ни одного state_attr() — проверка стала пустой"

    сверено = 0
    for сущность, атрибут in sorted(пары):
        if сущность not in сенсоры:
            continue  # сущность из интеграции, а не наша — судить не можем
        сверено += 1
        assert атрибут in сенсоры[сущность], (
            f"шаблон читает {сущность}.{атрибут}, но в json_attributes его нет — "
            f"Home Assistant вернёт None. Объявлено: {sorted(сенсоры[сущность])}"
        )
    assert сверено >= 3, f"сверено всего {сверено} пар — проверка почти пустая"


def test_скрипт_печатает_то_что_забирают():
    """
    Обратная сторона: ключ печатается, но не объявлен. Это не всегда
    ошибка — поле может быть никому не нужно, — поэтому здесь проверяется
    только то, что объявленного нет сверх напечатанного: объявить ключ,
    которого скрипт не печатает, значит ждать атрибут, который не придёт.
    """
    скрипт = (BIN / "docker_state.py").read_text(encoding="utf-8")
    ключи = set(re.findall(r'"(\w+)":', скрипт))
    assert ключи, "в docker_state.py не нашлось ни одного ключа JSON"

    объявлено = командные_сенсоры().get("sensor.docker_down")
    assert объявлено, "пропал сенсор sensor.docker_down"
    лишние = объявлено - ключи
    assert not лишние, (
        f"в json_attributes объявлены ключи, которых docker_state.py "
        f"не печатает: {sorted(лишние)}"
    )


def test_путь_к_скрипту_существует():
    """
    Команда сенсора указывает на файл в /config/bin — в репозитории это
    homeassistant/config/bin. Опечатка в пути даёт сенсор, который всегда
    отдаёт пустую строку.
    """
    for файл in sorted(ПАКЕТЫ.glob("*.yaml")):
        данные = yaml.load(файл.read_text(encoding="utf-8"), Loader) or {}
        for запись in данные.get("command_line") or []:
            for вид in ("sensor", "binary_sensor"):
                блок = (запись or {}).get(вид)
                if not isinstance(блок, dict):
                    continue
                for путь in re.findall(r"/config/(bin/[\w.]+)", блок.get("command", "")):
                    цель = ROOT / "homeassistant" / "config" / pathlib.Path(путь)
                    assert цель.exists(), f"{файл.name}: команда зовёт {путь}, которого нет"
