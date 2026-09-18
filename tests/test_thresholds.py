"""
Пороговые уведомления: перегрев, загрузка, место на томе, падения.

Все они срабатывали на триггере numeric_state, а он ловит ПЕРЕХОД
«вне диапазона → внутрь». При настройке Home Assistant взводит только те
сущности, которые на этот момент находятся вне диапазона:

    homeassistant/components/homeassistant/triggers/numeric_state.py
    # Each entity that starts outside the range is already armed.
    for entity_id in entity_ids:
        if not check_numeric_state(entity_id, None, entity_id):
            armed_entities.add(entity_id)

Сущность, уже находящаяся за порогом, должна сначала выйти из диапазона
и войти обратно. Для заканчивающегося места это означает «никогда»: том
занят на 90 %, вы перезагрузили NAS — предупреждение не придёт, сколько бы
ни было занято. Перегрев усугубляет: Home Assistant чаще всего
перезапускают как раз под нагрузкой.

Здесь закреплено лечение: пороги вынесены в шаблонные сенсоры (они после
перезапуска переходят из unknown в on — это настоящий переход), а сообщение
о падении ловит список имён, а не счётчик.
"""

from __future__ import annotations

import jinja2
import pytest
import yaml
from conftest import ROOT

ПАКЕТЫ = ROOT / "homeassistant" / "config" / "packages"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def разобрать(файл: str) -> dict:
    return yaml.load((ПАКЕТЫ / файл).read_text(encoding="utf-8"), Loader=Loader)


def сенсор(файл: str, unique_id: str) -> dict:
    данные = разобрать(файл)
    for блок in данные["template"]:
        for запись in (блок or {}).get("binary_sensor") or []:
            if запись.get("unique_id") == unique_id:
                return запись
    raise AssertionError(f"{файл}: нет сенсора {unique_id}")


def автоматика(файл: str, ident: str) -> dict:
    for запись in разобрать(файл)["automation"]:
        if (запись or {}).get("id") == ident:
            return запись
    raise AssertionError(f"{файл}: нет автоматизации {ident}")


def состояние(шаблон: str, значение: str) -> bool:
    окружение = jinja2.Environment()
    окружение.globals["states"] = lambda _: значение
    вывод = окружение.from_string(шаблон).render().strip()
    assert вывод in ("True", "False"), вывод
    return вывод == "True"


ПОРОГИ = [
    ("nas_hot", "mon_nas_temperature", 60),
    ("nas_cpu_busy", "mon_nas_cpu", 90),
    ("nas_volume_filling", "mon_volume_space", 85),
]


@pytest.mark.parametrize(("unique_id", "_ident", "порог"), ПОРОГИ)
def test_порог_срабатывает_выше_и_молчит_ниже(unique_id, _ident, порог) -> None:
    шаблон = сенсор("monitoring.yaml", unique_id)["state"]
    assert состояние(шаблон, str(порог + 5)) is True
    assert состояние(шаблон, str(порог - 5)) is False


@pytest.mark.parametrize(("unique_id", "_ident", "_порог"), ПОРОГИ)
@pytest.mark.parametrize("плохое", ["unknown", "unavailable", ""])
def test_недоступный_сенсор_не_поднимает_тревогу(unique_id, _ident, _порог, плохое) -> None:
    """
    float(0) превратил бы недоступный сенсор в ноль. Для температуры это
    «холодно» — ложного спокойствия, для места «пусто». Значение по
    умолчанию -1 ниже любого порога, и тревоги не будет ни в ту, ни в другую
    сторону; отсутствие данных ловится отдельной веткой в сводке.
    """
    assert состояние(сенсор("monitoring.yaml", unique_id)["state"], плохое) is False


@pytest.mark.parametrize(("unique_id", "ident", "_порог"), ПОРОГИ)
def test_автоматика_ждёт_перехода_сенсора_а_не_числа(unique_id, ident, _порог) -> None:
    запись = автоматика("monitoring.yaml", ident)
    виды = {т.get("trigger") for т in запись["triggers"]}
    assert "numeric_state" not in виды, (
        f"{ident}: numeric_state не взводится после перезапуска, если значение "
        f"уже за порогом"
    )
    сущности = {т.get("entity_id") for т in запись["triggers"]}
    assert f"binary_sensor.{unique_id}" in сущности


@pytest.mark.parametrize(("_uid", "ident", "_порог"), ПОРОГИ)
def test_о_пороге_напоминают_а_не_говорят_однажды(_uid, ident, _порог) -> None:
    """Заканчивающееся место само не рассасывается — сообщать раз в жизни мало."""
    запись = автоматика("monitoring.yaml", ident)
    assert any(т.get("trigger") == "time" for т in запись["triggers"]), (
        f"{ident}: нет повторного напоминания"
    )
    assert запись.get("conditions"), f"{ident}: напоминание сработает и когда порог уже снят"


def test_критический_порог_тома_остаётся_отдельным() -> None:
    """Разница между «пора посмотреть» и «пора чистить» не должна пропасть."""
    шаблон = сенсор("monitoring.yaml", "nas_volume_critical")["state"]
    assert состояние(шаблон, "95") is True
    assert состояние(шаблон, "90") is False
    действия = автоматика("monitoring.yaml", "mon_volume_space")["actions"]
    уровень = действия[0]["data"]["level"]
    assert "nas_volume_critical" in уровень, "уровень сообщения больше не зависит от второго порога"


# --- падения контейнеров ---------------------------------------------------

def test_о_втором_упавшем_контейнере_сообщат() -> None:
    """
    numeric_state above: 0 срабатывает на переходе через ноль. Упал Radarr —
    сообщение пришло; через час упал qBittorrent — счётчик идёт с 1 на 2,
    перехода нет, и о втором падении узнаёшь утром из сводки. Список имён
    меняется на каждое новое падение.
    """
    запись = автоматика("docker.yaml", "docker_container_down")
    триггеры = запись["triggers"]
    assert all(т.get("trigger") != "numeric_state" for т in триггеры)
    assert any(т.get("attribute") == "down_names" for т in триггеры), (
        "падение ловится счётчиком, а не списком имён"
    )
    assert запись.get("conditions"), "без условия сообщение придёт и на восстановление"


def test_цикл_перезапуска_ловится_отдельно() -> None:
    """
    Контейнер, который поднимается быстрее выдержки, счётчиком не ловится
    вообще: «упавших» почти всегда ноль.
    """
    запись = сенсор("docker.yaml", "docker_restarting")
    assert запись["delay_on"] == "00:10:00", (
        "без выдержки обычный docker compose up -d будет считаться циклом"
    )
    assert "_uptime$" in запись["state"], "свежесть старта берётся не из uptime"
    авто = автоматика("docker.yaml", "docker_container_restarting")
    assert авто["triggers"][0]["entity_id"] == "binary_sensor.docker_restarting"
