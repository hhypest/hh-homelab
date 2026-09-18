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
на самой приставке, а не «кастом». Записан признак один раз — отдельным
сенсором binary_sensor.tv_watching, — и им пользуются оба потребителя:
сенсор простоя и ночная проверка «вы ещё смотрите?». Пока предикат был
записан дважды, они разошлись, и просмотр без Cast-сессии не выключался
вообще никогда (замечание Codex P2).

Cast закрывает не всё, и это измерено: клиент Jellyfin для Android TV
медиасессию ему не отдаёт. При идущем фильме Cast стоит в idle
с media_position: 0, хотя app_name у него Jellyfin, — а сущность
интеграции Jellyfin в тот же момент показывает playing и верное название.
Значит источников просмотра два, и оба обязательны:

    YouTube на приставке  → Cast: playing, Jellyfin: сессии нет
    Jellyfin на приставке → Cast: idle,    Jellyfin: playing

Сессия Jellyfin берётся ровно одна и именно этой приставки: обход всех
сессий интеграции считал просмотром Jellyfin на телефоне и в браузере,
и забытый телевизор горел до конца чужого сеанса (второе замечание).

Здесь эта логика закреплена таблицей истинности — по строке на каждый
случай, который уже случался или может.
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
JELLYFIN = "media_player.jellyfin_rocktek_gx1"
ЧУЖОЙ_JELLYFIN = "media_player.jellyfin_phone"
ПРОСМОТР = "binary_sensor.tv_watching"


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


def отрисовать(шаблон: str, состояния: dict[str, str], просмотр: str | None = None) -> str:
    окружение = jinja2.Environment()
    окружение.globals["states"] = lambda имя: состояния.get(имя, "unknown")
    окружение.globals["is_state"] = lambda имя, знач: (
        просмотр == знач if имя == ПРОСМОТР else состояния.get(имя) == знач
    )
    # Заглушка отдаёт все сессии Jellyfin, какие есть в состояниях: иначе
    # прежний обход integration_entities вёл бы себя в тесте безупречно
    # ровно потому, что ему нечего обходить.
    окружение.globals["integration_entities"] = lambda домен: (
        [и for и in состояния if и.startswith("media_player.jellyfin")]
        if домен == "jellyfin" else []
    )
    return окружение.from_string(шаблон).render().strip()


def булево(вывод: str) -> bool:
    assert вывод in ("True", "False"), f"сенсор вернул не булево: {вывод!r}"
    return вывод == "True"


def просмотр(тв: str, cast: str, jellyfin: str | None = None,
             чужой_jellyfin: str | None = None) -> bool:
    """binary_sensor.tv_watching — общий признак «на этом телевизоре смотрят»."""
    состояния = {ТВ: тв, CAST: cast, ПУЛЬТ: "on"}
    if jellyfin is not None:
        состояния[JELLYFIN] = jellyfin
    if чужой_jellyfin is not None:
        состояния[ЧУЖОЙ_JELLYFIN] = чужой_jellyfin
    return булево(отрисовать(шаблон_сенсора("tv_watching"), состояния))


def простой(тв: str, cast: str, jellyfin: str | None = None,
            чужой_jellyfin: str | None = None) -> bool:
    """binary_sensor.tv_idle — он же, но с оглядкой на состояние телевизора."""
    идёт = просмотр(тв, cast, jellyfin, чужой_jellyfin)
    return булево(отрисовать(
        шаблон_сенсора("tv_idle_20min"), {ТВ: тв}, просмотр="on" if идёт else "off",
    ))


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
    """
    Измеренный случай: Jellyfin на приставке идёт, а Cast в idle — он этой
    медиасессии не видит. Без этой строки самый частый сценарий дома
    считался бы простоем.
    """
    assert простой("on", "idle", jellyfin=состояние) is False


@pytest.mark.parametrize("состояние", ["paused", "idle", "off"])
def test_неиграющая_сессия_jellyfin_просмотром_не_считается(состояние: str) -> None:
    assert простой("on", "idle", jellyfin=состояние) is True


def test_признак_просмотра_не_спрашивает_пульт() -> None:
    """
    Регрессия: пульт не выдаёт playing никогда, и условие
    «states(пульт) != 'playing'» было истинным всегда — то есть
    третьим условием, которое ничего не проверяет.
    """
    шаблон = шаблон_сенсора("tv_watching")
    assert f"'{ПУЛЬТ}'" not in шаблон and f'"{ПУЛЬТ}"' not in шаблон, (
        "признак просмотра снова спрашивает пульт приставки"
    )
    assert CAST in шаблон, "признак не спрашивает Cast — просмотр определить нечем"


def test_простой_опирается_на_общий_признак() -> None:
    """Два места с одним предикатом однажды разошлись — пусть будет одно."""
    шаблон = шаблон_сенсора("tv_idle_20min")
    assert ПРОСМОТР in шаблон
    assert CAST not in шаблон, "предикат просмотра снова записан дважды"


def test_чужая_сессия_jellyfin_не_держит_телевизор() -> None:
    """
    Замечание Codex P2. Обход всех сессий интеграции считал просмотром
    Jellyfin на телефоне или в браузере: сенсор простоя не взводился,
    и забытый телевизор горел до конца чужого сеанса.
    """
    assert простой("on", "idle", чужой_jellyfin="playing") is True
    assert просмотр("on", "idle", чужой_jellyfin="playing") is False


def test_признак_не_обходит_все_сессии_подряд() -> None:
    """Прямая проверка причины: сессия берётся одна и именно этой приставки."""
    шаблон = шаблон_сенсора("tv_watching")
    assert "integration_entities" not in шаблон, (
        "признак снова считает просмотром любую сессию Jellyfin в доме"
    )
    assert JELLYFIN in шаблон


def test_ночная_проверка_знает_про_jellyfin_без_cast() -> None:
    """
    Замечание Codex P2. Условие 4.3 смотрело только на Cast, а сенсор
    простоя учитывал ещё и Jellyfin. Просмотр без Cast-сессии не выключался
    ни по простою (сенсор не взводился), ни ночной проверкой (условие
    не выполнялось) — уснувший зритель оставлял телевизор до утра.
    """
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    ночная = next(a for a in данные["automation"] if a.get("id") == "tv_night_sleep_check")

    условия = [у for у in ночная["conditions"] if у.get("entity_id") == ПРОСМОТР]
    assert условия, "4.3 проверяет просмотр не тем же признаком, что сенсор простоя"
    assert условия[0]["state"] == "on"

    ожидание = [ш for ш in ночная["actions"] if "wait_for_trigger" in ш]
    assert ожидание, "пропало ожидание возврата к просмотру"
    триггер = ожидание[0]["wait_for_trigger"][0]
    assert триггер["entity_id"] == ПРОСМОТР and триггер["to"] == "on", (
        "ждём возврата не того признака, по которому решили, что просмотр шёл"
    )


def test_пауза_отправляется_пультом() -> None:
    """Кнопка работает в любом приложении — в отличие от команды Cast."""
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    ночная = next(a for a in данные["automation"] if a.get("id") == "tv_night_sleep_check")
    пауза = [ш for ш in ночная["actions"] if ш.get("action") == "media_player.media_pause"]
    assert пауза and пауза[0]["target"]["entity_id"] == ПУЛЬТ


# ---------------------------------------------------------------------------
#  Служба, которой нет
# ---------------------------------------------------------------------------
#  Ночная проверка начиналась с «action: notify.lg_tv» и комментария «если
#  у вас служба называется иначе, шаг просто будет пропущен». Неверно и то,
#  и другое. Имя службе даёт заголовок записи интеграции (webostv/__init__.py
#  передаёт в платформу CONF_NAME: entry.title), по умолчанию это что-то вроде
#  notify.lg_webos_tv_32lk540bpla. А ServiceNotFound стоит в списке исключений,
#  которые continue_on_error НЕ подавляет:
#
#      homeassistant/helpers/script.py, _handle_exception
#      # These are incorrect scripts, and not runtime errors ...
#      if isinstance(exception, (..., exceptions.ServiceNotFound, ...)):
#          raise exception
#
#  То есть автоматика обрывалась на первом шаге: ни предупреждения, ни паузы,
#  ни выключения. Home Assistant сообщал об этом как о «неизвестном действии».

СЛУЖБЫ_ПО_ЗАГОЛОВКУ = ("notify.lg", "notify.webos", "notify.tv")


def действия(ident: str) -> list[str]:
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    запись = next(a for a in данные["automation"] if a.get("id") == ident)
    return [ш["action"] for ш in запись["actions"] if isinstance(ш, dict) and "action" in ш]


def test_ночная_проверка_не_зовёт_службу_по_имени_устройства() -> None:
    for служба in действия("tv_night_sleep_check"):
        assert not служба.startswith(СЛУЖБЫ_ПО_ЗАГОЛОВКУ), (
            f"{служба}: имя этой службы зависит от заголовка записи интеграции, "
            f"а не от сущности — на чужой системе её не существует, и вся "
            f"автоматика оборвётся на этом шаге"
        )


def test_предупреждение_уходит_тостом_webos() -> None:
    """Имя webostv.command фиксировано, а адресуется она сущностью."""
    данные = yaml.load(ПАКЕТ.read_text(encoding="utf-8"), Loader=Loader)
    запись = next(a for a in данные["automation"] if a.get("id") == "tv_night_sleep_check")
    тост = [ш for ш in запись["actions"] if ш.get("action") == "webostv.command"]
    assert тост, "предупреждение на экране пропало"
    данные_шага = тост[0]["data"]
    assert данные_шага["entity_id"] == ТВ
    assert данные_шага["command"] == "system.notifications/createToast"
    assert "message" in данные_шага["payload"]
    assert тост[0].get("continue_on_error") is True, (
        "выключенный телевизор не должен ронять автоматику — эту ошибку "
        "continue_on_error как раз подавляет"
    )


def test_ни_одна_автоматика_не_зовёт_notify_по_имени_устройства() -> None:
    """Та же ошибка в другом файле стоила бы столько же."""
    найдено = []
    for путь in sorted(ПАКЕТ.parent.glob("*.yaml")):
        текст = путь.read_text(encoding="utf-8")
        for строка in текст.splitlines():
            голое = строка.strip()
            if голое.startswith(("- action:", "action:", "- service:", "service:")):
                значение = голое.split(":", 1)[1].strip()
                if значение.startswith(СЛУЖБЫ_ПО_ЗАГОЛОВКУ):
                    найдено.append(f"{путь.name}: {значение}")
    assert not найдено, найдено
