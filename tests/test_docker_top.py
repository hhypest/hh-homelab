"""
Кто больше всех ест: агрегат не должен побеждать сам себя.

В сводке за 20 сентября 2026 стояло «Больше всех процессора: top (3.0 %)».
Контейнера с именем top на NAS нет и не было. Победителем оказался сам
показометр: его entity_id — sensor.docker_top_cpu — подходит под шаблон
'^sensor\\.docker_.*_cpu$' ничем не хуже sensor.docker_jellyfin_cpu,
и сенсор попадал в собственную выборку.

На состоянии это незаметно: максимум множества, в котором лежит сам
максимум, — тот же максимум. Ломалось имя. А делало самозванца строго
больше всех округление: состояние агрегата — round(1) от максимума,
то есть 2,95 % настоящего контейнера превращались в его собственные
3,0 %, и сравнение «больше» проходило.

Показометр памяти ту же ловушку обошёл случайно: назван он
docker_top_ram, а ищет '_memory$' — разошёлся с шаблоном по имени,
а не по замыслу. Достаточно переименовать сенсор в docker_top_memory,
и повторилось бы. Поэтому проверка накрывает оба.

Цена ошибки не только в сводке: на sensor.docker_top_cpu висит
автоматизация docker_container_cpu_hog, и в тревогу «контейнер долго
держит процессор» уезжало бы то же самое имя — то есть сообщение
не называло бы виновника.
"""

from __future__ import annotations

import re
import types

import jinja2
import pytest
import yaml
from conftest import ROOT

PACKAGES = ROOT / "homeassistant" / "config" / "packages"


class Loader(yaml.SafeLoader):
    """!secret и прочие теги Home Assistant не мешают разбору."""


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def sensor(path: str, unique_id: str) -> dict:
    data = yaml.load((PACKAGES / path).read_text(encoding="utf-8"), Loader=Loader)
    for block in data["template"]:
        for entry in (block or {}).get("sensor") or []:
            if entry.get("unique_id") == unique_id:
                return entry
    raise AssertionError(f"{path}: нет сенсора {unique_id}")


def fake_states(rows: list[tuple[str, str, str]]):
    """Подобие states.sensor: entity_id, состояние, человеческое имя."""
    entries = [
        types.SimpleNamespace(entity_id=entity_id, state=state, name=name)
        for entity_id, state, name in rows
    ]
    return types.SimpleNamespace(sensor=entries)


def render(template: str, rows: list[tuple[str, str, str]]) -> str:
    env = jinja2.Environment()
    env.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    env.globals["states"] = fake_states(rows)
    return env.from_string(template).render().strip()


# Настоящий срез: максимум у jellyfin, и он же — из-за round(1) — делает
# состояние агрегата строго больше собственного источника.
CPU_ROWS = [
    ("sensor.docker_jellyfin_cpu", "2.95", "Docker jellyfin CPU"),
    ("sensor.docker_radarr_cpu", "0.4", "Docker radarr CPU"),
    ("sensor.docker_prowlarr_cpu", "0.2", "Docker prowlarr CPU"),
    ("sensor.docker_top_cpu", "3.0", "Docker top CPU"),
]

RAM_ROWS = [
    ("sensor.docker_jellyfin_memory", "637", "Docker jellyfin Memory"),
    ("sensor.docker_radarr_memory", "212", "Docker radarr Memory"),
    ("sensor.docker_top_ram", "637", "Docker top RAM"),
]


def test_winner_is_a_real_container_not_the_aggregate() -> None:
    """Ровно тот случай, что уехал в сводку 20 сентября."""
    template = sensor("docker.yaml", "docker_top_cpu")["attributes"]["container"]
    assert render(template, CPU_ROWS) == "jellyfin"


def test_aggregate_name_never_leaks_into_the_summary() -> None:
    """
    Отдельно от предыдущего: там проверено, кто победил, здесь — что
    в сообщение не попадёт слово, которое человек прочитает как контейнер.
    """
    template = sensor("docker.yaml", "docker_top_cpu")["attributes"]["container"]
    assert render(template, CPU_ROWS) not in ("top", "top CPU", "Docker top CPU", "—")


def test_state_counts_containers_only() -> None:
    template = sensor("docker.yaml", "docker_top_cpu")["state"]
    assert render(template, CPU_ROWS) == "3.0", "максимум берётся не у контейнеров"


def test_memory_aggregate_is_guarded_too() -> None:
    """
    Память сегодня спасает лишь расхождение имён: docker_top_ram против
    '_memory$'. Проверка держит замысел, а не совпадение — строка
    с явным исключением должна остаться и после любого переименования.
    """
    template = sensor("docker.yaml", "docker_top_ram")["attributes"]["container"]
    assert render(template, RAM_ROWS) == "jellyfin"
    assert "sensor.docker_top_ram" in template, (
        "исключение агрегата пропало — переименование сенсора вернёт ошибку CPU"
    )


@pytest.mark.parametrize("unique_id", ["docker_top_cpu", "docker_top_ram"])
def test_no_sensor_at_all_does_not_break_the_template(unique_id: str) -> None:
    """Пустой список — не повод уронить сводку: должно получиться «—»."""
    template = sensor("docker.yaml", unique_id)["attributes"]["container"]
    assert render(template, []) == "—"
