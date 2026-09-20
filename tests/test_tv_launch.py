"""
Запуск Jellyfin на приставке: отправили — проверь, что открылось.

Сценарий «Смотреть Jellyfin» включал связку и отправлял приставке одну
команду запуска, после чего заканчивался успехом независимо от того, что
на приставке произошло. На живой системе это выглядело так: телевизор
и приставка включаются, Jellyfin не открывается, приложение приходится
выбирать пультом. YouTube в том же сценарии работал — и разница объясняет
причину.

YouTube запускается ссылкой, её разбирает сам YouTube. Jellyfin —
именем пакета, а библиотека androidtvremote2 превращает имя в ссылку
market://launch?id=..., которую разбирает Play Store:

    prefix = "" if urlparse(app_link_or_app_id).scheme else "market://launch?id="

Значит запуск Jellyfin зависит от готовности ещё одной программы. Между
«media_player перешёл в on» и «лаунчер поднялся» проходят секунды,
и команда, отправленная в эту щель, теряется без ошибки: у протокола
пульта нет ответа «не смог».

Отсюда три требования к сценарию, и здесь проверяется каждое: дождаться
готовности до отправки, проверить результат после и повторить, а не
считать отправку успехом.

Проверка и признак готовности — один и тот же атрибут app_id: приставка
называет приложение на переднем плане. Пока он пуст, передний план ещё
не собран; когда он равен пакету Jellyfin, приложение действительно
открыто.
"""

from __future__ import annotations

import jinja2
import pytest
import yaml
from conftest import ROOT

PACKAGE = ROOT / "homeassistant" / "config" / "packages" / "media_tv.yaml"

REMOTE = "media_player.rocktek_gx1"
JELLYFIN_PACKAGE = "org.jellyfin.androidtv"
LAUNCHER = "com.google.android.apps.tv.launcherx"


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def scenario(name: str) -> list[dict]:
    """Шаги сценария по его имени, а не по подстроке в файле."""
    data = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=Loader)
    scripts = data["script"]
    assert name in scripts, f"в {PACKAGE.name} нет сценария {name}"
    return scripts[name]["sequence"]


def render(template: str, app_id: str | None, index: int = 1) -> str:
    env = jinja2.Environment()
    env.globals["state_attr"] = lambda entity, attribute: (
        app_id if (entity, attribute) == (REMOTE, "app_id") else None
    )
    return env.from_string(template).render(
        repeat={"index": index}, jellyfin_app=JELLYFIN_PACKAGE,
    ).strip()


def as_bool(output: str) -> bool:
    assert output in ("True", "False"), f"шаблон вернул не булево: {output!r}"
    return output == "True"


def repeat_step() -> dict:
    for step in scenario("tv_jellyfin"):
        if "repeat" in step:
            return step["repeat"]
    raise AssertionError("в tv_jellyfin нет повторной отправки запуска")


def test_launch_is_sent_only_to_a_ready_box():
    """
    Ожидание готовности обязано стоять до первой отправки. Отправка,
    ушедшая раньше, пропадает молча — именно этим сценарий и был сломан.
    """
    steps = scenario("tv_jellyfin")
    expectations = [i for i, tpl in enumerate(steps)
                if "app_id" in str(tpl.get("wait_template", ""))]
    repeat = [i for i, tpl in enumerate(steps) if "repeat" in tpl]
    assert expectations, "перед запуском нет ожидания готовности приставки"
    assert repeat, "запуск не обёрнут в повтор"
    assert min(expectations) < min(repeat), (
        "ожидание готовности стоит после отправки запуска — толку от него нет"
    )


def test_launch_is_retried_not_sent_once():
    """Одна отправка и была прежним поведением: успех сценария без результата."""
    repeat = repeat_step()
    sends = [tpl for tpl in repeat["sequence"] if tpl.get("action") == "remote.turn_on"]
    assert sends, "в повторе нет самой команды запуска"
    assert repeat.get("until"), "у повтора нет условия выхода — он не проверяет результат"


@pytest.mark.parametrize(
    ("app_id", "attempt", "exit_code", "why"),
    [
        (JELLYFIN_PACKAGE, 1, True, "открылось с первой попытки — повторять нечего"),
        (LAUNCHER, 1, False, "приставка на домашнем экране — пробуем ещё раз"),
        (LAUNCHER, 2, False, "вторая попытка мимо — остаётся третья"),
        (JELLYFIN_PACKAGE, 2, True, "открылось со второй — выходим сразу"),
        (LAUNCHER, 3, True, "три попытки мимо — выходим и жалуемся"),
        (None, 1, False, "передний план ещё не собран — это не повод сдаваться"),
    ],
)
def test_repeat_exit_condition(app_id, attempt, exit_code, why):
    condition = repeat_step()["until"][0]["value_template"]
    assert as_bool(render(condition, app_id, attempt)) is exit_code, why


def test_failure_does_not_stay_silent():
    """
    Сценарий, закончившийся ничем, обязан сказать об этом: иначе снаружи
    он неотличим от сломанного — что и происходило.
    """
    tail = scenario("tv_jellyfin")[-1]
    assert "if" in tail, "после повтора нет проверки результата"
    condition = tail["if"][0]["value_template"]
    assert as_bool(render(condition, LAUNCHER)), "не жалуется, когда приложение не открылось"
    assert not as_bool(render(condition, JELLYFIN_PACKAGE)), "жалуется на успешный запуск"
    actions = [tpl.get("action") for tpl in tail["then"]]
    assert "persistent_notification.create" in actions


def test_resume_does_not_wait_for_a_missing_app_session():
    """
    «Продолжить просмотр» ждал сессию полторы минуты и заканчивал советом
    проверить учётную запись Jellyfin. Если приложение не открылось,
    совет уводит в сторону: проверять надо запуск.
    """
    steps = scenario("tv_jellyfin_resume")
    stops = [i for i, tpl in enumerate(steps) if "if" in tpl
                 and any("stop" in d for d in tpl.get("then", []))]
    expectations = [i for i, tpl in enumerate(steps) if "wait_template" in tpl]
    assert stops, "нет досрочного выхода, когда Jellyfin не открылся"
    assert expectations, "пропало ожидание сессии"
    assert min(stops) < min(expectations), "выход стоит после ожидания — оно всё равно отсидится"
    condition = steps[min(stops)]["if"][0]["value_template"]
    assert as_bool(render(condition, LAUNCHER)), "не останавливается без Jellyfin"
    assert not as_bool(render(condition, JELLYFIN_PACKAGE)), "останавливается при открытом Jellyfin"
