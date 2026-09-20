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

PACKAGES = ROOT / "homeassistant" / "config" / "packages"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def parse(path: str) -> dict:
    return yaml.load((PACKAGES / path).read_text(encoding="utf-8"), Loader=Loader)


def sensor(path: str, unique_id: str) -> dict:
    data = parse(path)
    for block in data["template"]:
        for entry in (block or {}).get("binary_sensor") or []:
            if entry.get("unique_id") == unique_id:
                return entry
    raise AssertionError(f"{path}: нет сенсора {unique_id}")


def automation(path: str, ident: str) -> dict:
    for entry in parse(path)["automation"]:
        if (entry or {}).get("id") == ident:
            return entry
    raise AssertionError(f"{path}: нет автоматизации {ident}")


def state(template: str, value: str) -> bool:
    env = jinja2.Environment()
    env.globals["states"] = lambda _: value
    output = env.from_string(template).render().strip()
    assert output in ("True", "False"), output
    return output == "True"


THRESHOLDS = [
    ("nas_hot", "mon_nas_temperature", 60),
    ("nas_cpu_busy", "mon_nas_cpu", 90),
    ("nas_volume_filling", "mon_volume_space", 85),
]


@pytest.mark.parametrize(("unique_id", "_ident", "threshold"), THRESHOLDS)
def test_threshold_fires_above_and_stays_silent_below(unique_id, _ident, threshold) -> None:
    template = sensor("monitoring.yaml", unique_id)["state"]
    assert state(template, str(threshold + 5)) is True
    assert state(template, str(threshold - 5)) is False


@pytest.mark.parametrize(("unique_id", "_ident", "_threshold"), THRESHOLDS)
@pytest.mark.parametrize("bad", ["unknown", "unavailable", ""])
def test_unavailable_sensor_raises_no_alarm(unique_id, _ident, _threshold, bad) -> None:
    """
    float(0) превратил бы недоступный сенсор в ноль. Для температуры это
    «холодно» — ложного спокойствия, для места «пусто». Значение по
    умолчанию -1 ниже любого порога, и тревоги не будет ни в ту, ни в другую
    сторону; отсутствие данных ловится отдельной веткой в сводке.
    """
    assert state(sensor("monitoring.yaml", unique_id)["state"], bad) is False


@pytest.mark.parametrize(("unique_id", "ident", "_threshold"), THRESHOLDS)
def test_automation_waits_for_a_sensor_transition_not_a_number(unique_id, ident, _threshold) -> None:
    entry = automation("monitoring.yaml", ident)
    kinds = {t.get("trigger") for t in entry["triggers"]}
    assert "numeric_state" not in kinds, (
        f"{ident}: numeric_state не взводится после перезапуска, если значение "
        f"уже за порогом"
    )
    entities = {t.get("entity_id") for t in entry["triggers"]}
    assert f"binary_sensor.{unique_id}" in entities


@pytest.mark.parametrize(("_uid", "ident", "_threshold"), THRESHOLDS)
def test_threshold_is_repeated_not_announced_once(_uid, ident, _threshold) -> None:
    """Заканчивающееся место само не рассасывается — сообщать раз в жизни мало."""
    entry = automation("monitoring.yaml", ident)
    assert any(t.get("trigger") == "time" for t in entry["triggers"]), (
        f"{ident}: нет повторного напоминания"
    )
    assert entry.get("conditions"), f"{ident}: напоминание сработает и когда порог уже снят"


def test_critical_volume_threshold_stays_separate() -> None:
    """Разница между «пора посмотреть» и «пора чистить» не должна пропасть."""
    template = sensor("monitoring.yaml", "nas_volume_critical")["state"]
    assert state(template, "95") is True
    assert state(template, "90") is False
    actions = automation("monitoring.yaml", "mon_volume_space")["actions"]
    level = actions[0]["data"]["level"]
    assert "nas_volume_critical" in level, "уровень сообщения больше не зависит от второго порога"


# --- падения контейнеров ---------------------------------------------------

def test_second_down_container_is_reported() -> None:
    """
    numeric_state above: 0 срабатывает на переходе через ноль. Упал Radarr —
    сообщение пришло; через час упал qBittorrent — счётчик идёт с 1 на 2,
    перехода нет, и о втором падении узнаёшь утром из сводки. Список имён
    меняется на каждое новое падение.
    """
    entry = automation("docker.yaml", "docker_container_down")
    triggers = entry["triggers"]
    assert all(t.get("trigger") != "numeric_state" for t in triggers)
    assert any(t.get("attribute") == "down_names" for t in triggers), (
        "падение ловится счётчиком, а не списком имён"
    )
    assert entry.get("conditions"), "без условия сообщение придёт и на восстановление"


def test_restart_loop_is_caught_separately() -> None:
    """
    Контейнер, который поднимается быстрее выдержки, счётчиком не ловится
    вообще: «упавших» почти всегда ноль.
    """
    entry = sensor("docker.yaml", "docker_restarting")
    assert entry["delay_on"] != "00:00:00", (
        "без выдержки одиночный перезапуск будет считаться циклом"
    )
    auto = automation("docker.yaml", "docker_container_restarting")
    assert auto["triggers"][0]["entity_id"] == "binary_sensor.docker_restarting"


def test_restart_loop_is_taken_from_the_minute_source() -> None:
    """
    Замечание Codex. Первая версия считала возраст контейнера
    по sensor.docker_*_uptime, а у Monitor Docker scan_interval 3600:
    признак «моложе пяти минут» на часовых данных живёт от силы пять минут
    и десятиминутной выдержки не набирает никогда. Проверка не могла
    сработать в принципе.
    """
    template = sensor("docker.yaml", "docker_restarting")["state"]
    assert "_uptime" not in template, "признак снова построен на часовых данных"
    assert "sensor.docker_down" in template, (
        "источник не command_line-сенсор — а обновляется раз в минуту только он"
    )

    # Тот сенсор действительно опрашивается раз в минуту.
    data = parse("docker.yaml")
    command = next(
        z["sensor"] for z in data["command_line"]
        if "docker_state.py" in (z.get("sensor") or {}).get("command", "")
    )
    assert command["scan_interval"] <= 60

    # А Monitor Docker — раз в час, и это осознанный размен, см. шапку файла.
    assert data["monitor_docker"][0]["scan_interval"] == 3600
