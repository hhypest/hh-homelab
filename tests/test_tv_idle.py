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

PACKAGE = ROOT / "homeassistant" / "config" / "packages" / "media_tv.yaml"

TV = "media_player.lg_tv"
CAST = "media_player.rocktek_gx1_cast"
REMOTE = "media_player.rocktek_gx1"
JELLYFIN = "media_player.jellyfin_rocktek_gx1"
OTHER_JELLYFIN = "media_player.jellyfin_phone"
WATCHING = "binary_sensor.tv_watching"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def sensor_template(unique_id: str) -> str:
    """Шаблон состояния сенсора по его unique_id, а не по подстроке в файле."""
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    for block in data["template"]:
        for entry in (block or {}).get("binary_sensor") or []:
            if entry.get("unique_id") == unique_id:
                return entry["state"]
    raise AssertionError(f"в {PACKAGE.name} нет сенсора с unique_id={unique_id}")


def render(template: str, states: dict[str, str], watching: str | None = None) -> str:
    env = jinja2.Environment()
    env.globals["states"] = lambda name: states.get(name, "unknown")
    env.globals["is_state"] = lambda name, val: (
        watching == val if name == WATCHING else states.get(name) == val
    )
    # Заглушка отдаёт все сессии Jellyfin, какие есть в состояниях: иначе
    # прежний обход integration_entities вёл бы себя в тесте безупречно
    # ровно потому, что ему нечего обходить.
    env.globals["integration_entities"] = lambda domain: (
        [i for i in states if i.startswith("media_player.jellyfin")]
        if domain == "jellyfin" else []
    )
    return env.from_string(template).render().strip()


def as_bool(output: str) -> bool:
    assert output in ("True", "False"), f"сенсор вернул не булево: {output!r}"
    return output == "True"


def watching(tv: str, cast: str, jellyfin: str | None = None,
             other_jellyfin: str | None = None) -> bool:
    """binary_sensor.tv_watching — общий признак «на этом телевизоре смотрят»."""
    states = {TV: tv, CAST: cast, REMOTE: "on"}
    if jellyfin is not None:
        states[JELLYFIN] = jellyfin
    if other_jellyfin is not None:
        states[OTHER_JELLYFIN] = other_jellyfin
    return as_bool(render(sensor_template("tv_watching"), states))


def idle(tv: str, cast: str, jellyfin: str | None = None,
            other_jellyfin: str | None = None) -> bool:
    """binary_sensor.tv_idle — он же, но с оглядкой на состояние телевизора."""
    playing = watching(tv, cast, jellyfin, other_jellyfin)
    return as_bool(render(
        sensor_template("tv_idle_20min"), {TV: tv}, watching="on" if playing else "off",
    ))


@pytest.mark.parametrize(
    ("tv", "cast", "expected", "why"),
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
def test_idle_table(tv: str, cast: str, expected: bool, why: str) -> None:
    assert idle(tv, cast) is expected, why


@pytest.mark.parametrize("state", ["playing", "buffering"])
def test_jellyfin_session_counts_as_watching(state: str) -> None:
    """
    Измеренный случай: Jellyfin на приставке идёт, а Cast в idle — он этой
    медиасессии не видит. Без этой строки самый частый сценарий дома
    считался бы простоем.
    """
    assert idle("on", "idle", jellyfin=state) is False


@pytest.mark.parametrize("state", ["paused", "idle", "off"])
def test_non_playing_jellyfin_session_is_not_watching(state: str) -> None:
    assert idle("on", "idle", jellyfin=state) is True


def test_watching_flag_does_not_query_the_remote() -> None:
    """
    Регрессия: пульт не выдаёт playing никогда, и условие
    «states(пульт) != 'playing'» было истинным всегда — то есть
    третьим условием, которое ничего не проверяет.
    """
    template = sensor_template("tv_watching")
    assert f"'{REMOTE}'" not in template and f'"{REMOTE}"' not in template, (
        "признак просмотра снова спрашивает пульт приставки"
    )
    assert CAST in template, "признак не спрашивает Cast — просмотр определить нечем"


def test_idle_relies_on_the_shared_flag() -> None:
    """Два места с одним предикатом однажды разошлись — пусть будет одно."""
    template = sensor_template("tv_idle_20min")
    assert WATCHING in template
    assert CAST not in template, "предикат просмотра снова записан дважды"


def test_foreign_jellyfin_session_does_not_hold_the_tv() -> None:
    """
    Замечание Codex P2. Обход всех сессий интеграции считал просмотром
    Jellyfin на телефоне или в браузере: сенсор простоя не взводился,
    и забытый телевизор горел до конца чужого сеанса.
    """
    assert idle("on", "idle", other_jellyfin="playing") is True
    assert watching("on", "idle", other_jellyfin="playing") is False


def test_flag_does_not_scan_every_session() -> None:
    """Прямая проверка причины: сессия берётся одна и именно этой приставки."""
    template = sensor_template("tv_watching")
    assert "integration_entities" not in template, (
        "признак снова считает просмотром любую сессию Jellyfin в доме"
    )
    assert JELLYFIN in template


def test_night_check_knows_jellyfin_without_cast() -> None:
    """
    Замечание Codex P2. Условие 4.3 смотрело только на Cast, а сенсор
    простоя учитывал ещё и Jellyfin. Просмотр без Cast-сессии не выключался
    ни по простою (сенсор не взводился), ни ночной проверкой (условие
    не выполнялось) — уснувший зритель оставлял телевизор до утра.
    """
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    night = next(a for a in data["automation"] if a.get("id") == "tv_night_sleep_check")

    conditions = [u for u in night["conditions"] if u.get("entity_id") == WATCHING]
    assert conditions, "4.3 проверяет просмотр не тем же признаком, что сенсор простоя"
    assert conditions[0]["state"] == "on"

    expected = [tpl for tpl in night["actions"] if "wait_for_trigger" in tpl]
    assert expected, "пропало ожидание возврата к просмотру"
    trigger = expected[0]["wait_for_trigger"][0]
    assert trigger["entity_id"] == WATCHING and trigger["to"] == "on", (
        "ждём возврата не того признака, по которому решили, что просмотр шёл"
    )


def test_pause_is_sent_with_the_remote() -> None:
    """Кнопка работает в любом приложении — в отличие от команды Cast."""
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    night = next(a for a in data["automation"] if a.get("id") == "tv_night_sleep_check")
    pause = [tpl for tpl in night["actions"] if tpl.get("action") == "media_player.media_pause"]
    assert pause and pause[0]["target"]["entity_id"] == REMOTE


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

SERVICES_BY_HEADING = ("notify.lg", "notify.webos", "notify.tv")


def actions(ident: str) -> list[str]:
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    entry = next(a for a in data["automation"] if a.get("id") == ident)
    return [tpl["action"] for tpl in entry["actions"] if isinstance(tpl, dict) and "action" in tpl]


def test_night_check_calls_no_service_by_device_name() -> None:
    for svc in actions("tv_night_sleep_check"):
        assert not svc.startswith(SERVICES_BY_HEADING), (
            f"{svc}: имя этой службы зависит от заголовка записи интеграции, "
            f"а не от сущности — на чужой системе её не существует, и вся "
            f"автоматика оборвётся на этом шаге"
        )


def test_warning_goes_out_as_a_webos_toast() -> None:
    """Имя webostv.command фиксировано, а адресуется она сущностью."""
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    entry = next(a for a in data["automation"] if a.get("id") == "tv_night_sleep_check")
    toast = [tpl for tpl in entry["actions"] if tpl.get("action") == "webostv.command"]
    assert toast, "предупреждение на экране пропало"
    step_data = toast[0]["data"]
    assert step_data["entity_id"] == TV
    assert step_data["command"] == "system.notifications/createToast"
    assert "message" in step_data["payload"]
    assert toast[0].get("continue_on_error") is True, (
        "выключенный телевизор не должен ронять автоматику — эту ошибку "
        "continue_on_error как раз подавляет"
    )


def test_no_automation_calls_notify_by_device_name() -> None:
    """Та же ошибка в другом файле стоила бы столько же."""
    found = []
    for path_str in sorted(PACKAGE.parent.glob("*.yaml")):
        text = path_str.read_text(encoding="utf-8")
        for line in text.splitlines():
            bare = line.strip()
            if bare.startswith(("- action:", "action:", "- service:", "service:")):
                value = bare.split(":", 1)[1].strip()
                if value.startswith(SERVICES_BY_HEADING):
                    found.append(f"{path_str.name}: {value}")
    assert not found, found
