"""
Сообщения Home Assistant: то, что реально увидит человек в Пачке.

Тела сообщений были записаны свёрнутым скаляром «>-». В YAML такой скаляр
склеивает соседние строки одинакового отступа пробелом, и отдельной строкой
остаётся лишь то, что отделено пустой строкой или отступлено глубже.

Для сообщения с циклом это означало вот что: при двух упавших контейнерах
оба имени оказывались в одной строке, вместе со строкой «Простой за сутки»
и следующим пунктом. В ежедневной сводке слипался весь блок целиком.
Заметить это чтением файла нельзя — в исходнике всё выглядит списком.

Поэтому здесь тела не читаются глазами, а рендерятся Jinja и проверяются
построчно. Подстановки — заглушки: проверяется структура сообщения,
а не значения датчиков.
"""

from __future__ import annotations

import html
import re

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


def script_var(path: str, script: str, name: str) -> str:
    """
    Значение переменной внутри конкретного скрипта.

    Искать по всему файлу нельзя, и это выяснилось дорого: первая версия
    брала первое попавшееся значение с нужным текстом, и когда правка
    по ошибке положила сводку в pachca_notify — общий шаблон всех
    уведомлений, — тест нашёл её там и остался зелёным. Отсюда обращение
    по имени скрипта: где лежит шаблон, здесь такая же часть проверки,
    как и то, что он печатает.
    """
    steps = parse(path)["script"][script]["sequence"]
    for step in steps:
        variables = (step or {}).get("variables") or {}
        if name in variables:
            return variables[name]
    raise AssertionError(f"{path}: в скрипте {script} нет переменной {name}")


def automation_text(path: str, ident: str) -> str:
    """Текст сообщения из автоматизации с указанным id."""
    for entry in parse(path)["automation"]:
        if (entry or {}).get("id") != ident:
            continue
        for step in entry["actions"]:
            data = (step or {}).get("data") or {}
            if "text" in data:
                return data["text"]
    raise AssertionError(f"{path}: не нашлось сообщение автоматизации {ident}")


def render(source: str, **data) -> list[str]:
    # Имя параметра не «text»: вызывающие передают в шаблон переменную text.
    return jinja2.Environment().from_string(source).render(**data).splitlines()


# --- сводка за сутки -------------------------------------------------------

SUMMARY = {
    "verdict": "✅ Всё в порядке", "cpu": "12", "ram": "40",
    "nas_temp": 38.2, "vol_used": 62.4,
    "cont_running": 6, "cont_total": 8, "cont_down_min": 0,
    "top_cpu": "jellyfin", "top_ram": "jellyfin",
    "booted": "1 сентября, 10:00", "disks": [], "svc_total": 6, "svc_down_min": 0,
    "nas_ok": True,
    "states": lambda _: "0", "state_attr": lambda *_: "0",
}


def summary(**over) -> list[str]:
    data = {**SUMMARY, "cont_down": 0, "cont_detail": [], "svc_down": [], **over}
    return render(script_var("pachca.yaml", "pachca_report", "body"), **data)


def test_each_down_container_on_its_own_line() -> None:
    lines = summary(cont_down=2, cont_detail=["radarr — Exited (137)", "prowlarr — контейнера нет"])
    down = [s for s in lines if s.startswith("• 🔻")]
    assert len(down) == 2, f"контейнеры слиплись: {lines}"


def test_idle_does_not_stick_to_the_container_list() -> None:
    lines = summary(cont_down=1, cont_detail=["radarr — Exited (1)"])
    assert "• Простой за сутки: 0 мин" in lines


def test_sections_are_separated_by_a_blank_line() -> None:
    """
    В свёрнутом скаляре пустая строка превращалась в одиночный перенос,
    поэтому абзацев в сообщении не было вовсе. Пустая строка стоит перед
    заголовком раздела и не стоит после — список идёт сразу под ним.
    """
    lines = summary()
    for heading in ("**Железо**", "**Контейнеры** — 6 из 8", "**Сервисы** — 6 из 6 отвечают"):
        place = lines.index(heading)
        assert lines[place - 1] == "", f"перед «{heading}» нет пустой строки"
        assert lines[place + 1].startswith("•"), f"после «{heading}» лишняя пустая строка"


def test_summary_matches_the_documentation_sample() -> None:
    """
    Шаг 5.6 чек-листа показывает, что придёт в чат. Пример писался от руки
    и показывал задуманное, а не то, что выходило на самом деле: блок
    контейнеров в жизни слипался в одну строку. Теперь это один и тот же
    текст, и разойтись они молча больше не могут.
    """
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    start = page.index("📊 <b>DS725+")
    sample = page[start:page.index("</code>", start)]
    sample = html.unescape(re.sub(r"</?b>", "**", sample)).rstrip()

    data = {
        **SUMMARY,
        "cont_down": 0, "cont_detail": [], "svc_down": [],
        "cpu": "11", "ram": "47", "nas_temp": 41.0, "vol_used": 63.2,
        "cont_running": 8, "cont_total": 8, "top_cpu": "Jellyfin", "top_ram": "Radarr",
        "booted": "2026-08-24T03:11:00",
        "states": lambda name: {
            "sensor.docker_top_cpu": "7.2", "sensor.docker_top_ram": "312",
        }.get(name, "0"),
    }
    result = render(script_var("pachca.yaml", "pachca_report", "body"), **data)
    assert result == sample.splitlines(), (
        "сводка разошлась с примером из шага 5.6:\n"
        + "\n".join(f"{'  ' if a == b else '≠ '}{a!r} | {b!r}"
                     for a, b in zip(result, sample.splitlines(), strict=False))
    )


def test_no_line_starts_with_a_space() -> None:
    """Раньше блоки сервисов и контейнеров приезжали с ведущим пробелом."""
    for line in summary(cont_down=1, cont_detail=["radarr"], svc_down=["Radarr"]):
        assert line == line.lstrip(), f"строка с ведущим пробелом: {line!r}"


def test_all_running_is_shown_in_one_line() -> None:
    lines = summary()
    assert "• Все запущены" in lines
    assert "• Все отвечают" in lines


def test_idle_is_printed_as_an_integer() -> None:
    """round(0) в Jinja возвращает дробное: в сводку уезжало «0.0 мин»."""
    text = (PACKAGES / "pachca.yaml").read_text(encoding="utf-8")
    for name in ("svc_down_min", "cont_down_min"):
        line = next(s for s in text.splitlines() if s.strip().startswith(f"{name}:"))
        assert "| round | int" in line, f"{name}: {line.strip()}"


def test_without_nas_data_the_summary_prints_no_zeros() -> None:
    """
    Приведение | float(0) превращало недоступные сенсоры в нули, и сводка
    докладывала «0 °C» и «Всё в порядке» ровно тогда, когда данных нет.
    Худший вид ошибки в мониторинге: уверенный отчёт о норме.
    """
    lines = summary(nas_ok=False)
    assert any("Метрики NAS недоступны" in s for s in lines)
    assert not any("°C" in s for s in lines), "напечатаны метрики, которых нет"
    assert not any("Том volume1" in s for s in lines)
    # Остальные разделы на месте: контейнеры и сервисы живут без DSM.
    assert "**Контейнеры** — 6 из 8" in lines
    assert any("Простой за сутки" in s for s in lines)


def test_nas_data_flag_is_computed_by_itself() -> None:
    """
    Замечание Codex P1. Шаблон nas_ok заканчивался лишней кавычкой —
    остатком от строки, с которой его копировали. В свёрнутом скаляре она
    попадала в значение, и признак принимал вид True" — ни одно из значений,
    которые проверяются дальше. Каждая сводка докладывала бы «нет данных»
    и прятала метрики при полностью исправном NAS.

    Остальные тесты этого не ловили: они подставляли nas_ok готовым булевым
    значением, то есть проверяли ветвление, а не сам признак.
    """
    template = script_var("pachca.yaml", "pachca_report", "nas_ok")
    live = {
        "sensor.ds725_temperature": "41.2",
        "sensor.ds725_cpu_utilization_total": "11",
        "sensor.ds725_memory_usage_real": "47",
        "sensor.ds725_volume_1_volume_used": "63",
    }
    def count(states: dict[str, str]) -> str:
        # Home Assistant обрезает пробелы у значения переменной, а свёрнутый
        # скаляр оставляет перенос после {% set %} — сравниваем по существу.
        return "\n".join(render(template, states=lambda name: states.get(name, "unknown"))).strip()

    assert count(live) == "True"

    for dropped in live:
        assert count({**live, dropped: "unavailable"}) == "False", (
            f"отвал {dropped} не замечен"
        )
        assert count({**live, dropped: "unknown"}) == "False", (
            f"{dropped} в unknown не замечен"
        )


@pytest.mark.parametrize(
    ("nas_ok", "expected"),
    [(True, "✅ Всё в порядке"), (False, "⚠️ Нет данных от NAS")],
)
def test_verdict_knows_about_integration_loss(nas_ok: bool, expected: str) -> None:
    """Вердикт считался из нулей и потому был бодрым при отсутствии данных."""
    template = script_var("pachca.yaml", "pachca_report", "verdict")
    total = "\n".join(render(template, cont_down=0, svc_down=[], disks=[],
                                svc_down_min=0, cont_down_min=0,
                                vol_used=42.0, nas_temp=38.0, nas_ok=nas_ok))
    assert expected in total


# --- уведомление об упавшем контейнере -------------------------------------

def outage(details: list[str], oom: bool = False) -> list[str]:
    return render(
        automation_text("docker.yaml", "docker_container_down"),
        running=8 - len(details), total=8,
        state_attr=lambda e, a: {"down_detail": details, "oom": oom}.get(a),
    )


def test_two_down_containers_on_two_lines() -> None:
    lines = outage(["radarr — Exited (137)", "prowlarr — контейнера нет"])
    assert len([s for s in lines if s.startswith("🔻")]) == 2, lines


def test_counter_and_hint_are_separate_paragraphs() -> None:
    lines = outage(["radarr — Exited (1)"])
    assert "Работает 7 из 8." in lines
    assert any(s.startswith("Поднять:") for s in lines)
    assert "" in lines, "абзацы слиплись"


def test_memory_warning_appears_only_on_oom() -> None:
    without = "\n".join(outage(["radarr — Exited (1)"], oom=False))
    with_it = "\n".join(outage(["radarr — Exited (137)"], oom=True))
    assert "137" not in without
    assert "нехватки памяти" in with_it


@pytest.mark.parametrize("path,template", [
    ("pachca.yaml", "📊 **DS725+ — сводка за сутки**"),
    ("docker.yaml", "🔻 **{{ line }}**"),
])
def test_multiline_bodies_use_a_literal_scalar(path, template) -> None:
    """
    Прямая проверка причины: свёрнутый скаляр для сообщения с циклом
    неверен всегда, сколько бы правильно ни выглядел исходник.
    """
    lines = (PACKAGES / path).read_text(encoding="utf-8").splitlines()
    place = next(i for i, s in enumerate(lines) if template in s)
    declaration = next(s for s in reversed(lines[:place]) if s.rstrip().endswith(("|-", ">-", '"')))
    assert declaration.rstrip().endswith("|-"), f"{path}: {declaration.strip()}"


# --- общий шаблон уведомлений ----------------------------------------------
#  Через pachca_notify идут почти все сообщения, и вызывающие передают
#  только title, text и level. Замечание Codex P1: правка по ошибке положила
#  сюда сводку, которая обращается к переменным соседнего скрипта. В Home
#  Assistant это означало бы отказ рендеринга в каждом уведомлении —
#  от «упал контейнер» до сообщения о перезапуске.

NOTIFY_FIELDS = {"icon", "title", "text", "level", "now"}


def test_shared_template_knows_no_foreign_variables() -> None:
    body = script_var("pachca.yaml", "pachca_notify", "body")
    names = set(re.findall(r"\{\{\s*([a-z_]+)", body))
    foreign = names - NOTIFY_FIELDS
    assert not foreign, (
        f"pachca_notify обращается к {sorted(foreign)} — вызывающие передают "
        f"только {sorted(NOTIFY_FIELDS - {'icon', 'now'})}, и рендеринг откажет"
    )


def test_shared_template_joins_title_and_text() -> None:
    body = script_var("pachca.yaml", "pachca_notify", "body")
    lines = render(
        body, icon="🔴", title="Упал контейнер", text="🔻 **radarr**",
        now=lambda: __import__("datetime").datetime(2026, 9, 18, 9, 0),
    )
    assert lines[0] == "🔴 **Упал контейнер**"
    assert "🔻 **radarr**" in lines
