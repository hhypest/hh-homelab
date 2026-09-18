"""
Признак «телевизор включён, но никто не смотрит».

На нём висит ночное автоотключение, и он был неверен у обоих источников
сразу. Пульт приставки (androidtv_remote) состояния playing не выдаёт
вообще — в его media_player.py всего две ветки, ON и OFF. Телевизор
на внешнем входе не знает, что показывает: в ответе webOS media_state
пустой список, и интеграция оставляет состояние on. Проверено
на диагностике живого телевизора при идущем фильме:

    current_app_id: com.webos.app.hdmi2
    media_state: []

Значит при активном просмотре все условия сенсора были истинны, простой
копился двадцать минут, и ночью автоматика выключала телевизор посреди
фильма. Единственная находка разбора, которую замечают телом, а не логом.

Теперь просмотр спрашивается у Cast-сущности приставки: она видит
медиасессию Android TV и отдаёт playing даже когда приложение запущено
на самой приставке, а не «кастом». Здесь эта логика закреплена таблицей
истинности — по строке на каждый случай, который уже случался или может.
"""

from __future__ import annotations

import jinja2
import pytest
import yaml
from conftest import ROOT

ПАКЕТ = ROOT / "homeassistant" / "config" / "packages" / "media_tv.yaml"

ТВ = "media_player.lg_tv"
CAST = "media_player.rocktek_gx1_cast"
ПУЛЬТ = "media_player.rocktek_gx1"
JELLYFIN = "media_player.jellyfin_tv"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def шаблон_сенсора(unique_id: str) -> str:
    """Шаблон состояния сенсора по его unique_id, а не по подстроке в файле."""
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    for блок in данные["template"]:
        for запись in (блок or {}).get("binary_sensor") or []:
            if запись.get("unique_id") == unique_id:
                return запись["state"]
    raise AssertionError(f"в {ПАКЕТ.name} нет сенсора с unique_id={unique_id}")


def простой(тв: str, cast: str, jellyfin: str | None = None) -> bool:
    состояния = {ТВ: тв, CAST: cast, ПУЛЬТ: "on"}
    if jellyfin is not None:
        состояния[JELLYFIN] = jellyfin
    окружение = jinja2.Environment()
    окружение.globals["states"] = lambda имя: состояния.get(имя, "unknown")
    окружение.globals["integration_entities"] = lambda домен: (
        [JELLYFIN] if домен == "jellyfin" and jellyfin is not None else []
    )
    вывод = окружение.from_string(шаблон_сенсора("tv_idle_20min")).render().strip()
    assert вывод in ("True", "False"), f"сенсор вернул не булево: {вывод!r}"
    return вывод == "True"


@pytest.mark.parametrize(
    ("тв", "cast", "ожидание", "почему"),
    [
        ("on", "playing", False, "фильм через приставку — тот самый случай, ради которого всё"),
        ("on", "buffering", False, "буферизация — это тоже просмотр"),
        ("on", "paused", True, "двадцать минут на паузе — вышел и забыл"),
        ("on", "idle", True, "приставка на домашнем экране"),
        ("on", "off", True, "приставка выключена, телевизор горит"),
        ("on", "unavailable", True, "приставки нет в сети — телевизор всё равно горит зря"),
        ("playing", "off", False, "телевизор играет сам, из своего приложения"),
        ("off", "off", False, "телевизор выключен — простаивать нечему"),
        ("standby", "off", False, "дежурный режим"),
        ("unavailable", "playing", False, "о телевизоре ничего не известно"),
        ("unknown", "off", False, "состояние ещё не пришло"),
    ],
)
def test_таблица_простоя(тв: str, cast: str, ожидание: bool, почему: str) -> None:
    assert простой(тв, cast) is ожидание, почему


@pytest.mark.parametrize("состояние", ["playing", "buffering"])
def test_сессия_jellyfin_считается_просмотром(состояние: str) -> None:
    """Приложение может не отдавать медиасессию — тогда о просмотре знает Jellyfin."""
    assert простой("on", "idle", jellyfin=состояние) is False


@pytest.mark.parametrize("состояние", ["paused", "idle", "off"])
def test_неиграющая_сессия_jellyfin_просмотром_не_считается(состояние: str) -> None:
    assert простой("on", "idle", jellyfin=состояние) is True


def test_сенсор_не_спрашивает_просмотр_у_пульта() -> None:
    """
    Регрессия: пульт не выдаёт playing никогда, и условие
    «states(пульт) != 'playing'» было истинным всегда — то есть
    третьим условием, которое ничего не проверяет.
    """
    шаблон = шаблон_сенсора("tv_idle_20min")
    assert f"'{ПУЛЬТ}'" not in шаблон and f'"{ПУЛЬТ}"' not in шаблон, (
        "сенсор снова спрашивает о просмотре пульт приставки"
    )
    assert CAST in шаблон, "сенсор не спрашивает Cast — просмотр определить нечем"


def test_ночные_автоматики_смотрят_на_cast() -> None:
    """4.3 ждала возврата в playing от пульта — не дождалась бы никогда."""
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    ночная = next(a for a in данные["automation"] if a.get("id") == "tv_night_sleep_check")
    текст = yaml.safe_dump(ночная, allow_unicode=True)
    assert CAST in текст, "автоматика «вы ещё смотрите?» не знает про Cast"
    ожидание = [ш for ш in ночная["actions"] if "wait_for_trigger" in ш]
    assert ожидание, "пропало ожидание возврата в playing"
    assert ожидание[0]["wait_for_trigger"][0]["entity_id"] == CAST
