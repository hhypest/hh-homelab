"""
Проверки Liquid-шаблонов Пачки.

render_pachca.py уже прогоняет шаблоны на примерах и ловит синтаксис. Здесь —
то, что скриптом не проверить: соблюдены ли договорённости, из-за нарушения
которых сообщение уйдёт неправильным, но не сломается. Такое молча живёт
месяцами.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from conftest import ROOT
from liquid import Environment

PACHCA = ROOT / "pachca"
SERVICES = ["radarr", "prowlarr", "jellyfin", "seerr"]
TEMPLATES = [PACHCA / f"{s}.liquid" for s in SERVICES] + [PACHCA / "media-router.liquid"]


def render(template: pathlib.Path, payload: dict) -> str:
    return Environment().from_string(template.read_text(encoding="utf-8")).render(**payload).strip()


def samples_for(service: str) -> list[pathlib.Path]:
    return sorted((PACHCA / "samples").glob(f"{service}-*.json"))


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_шаблон_компилируется(template: pathlib.Path) -> None:
    Environment().from_string(template.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", SERVICES)
def test_у_каждого_сервиса_есть_примеры(service: str) -> None:
    assert samples_for(service), f"нет ни одного samples/{service}-*.json"


@pytest.mark.parametrize("sample", sorted((PACHCA / "samples").glob("*.json")), ids=lambda p: p.name)
def test_пример_это_корректный_json(sample: pathlib.Path) -> None:
    assert isinstance(json.loads(sample.read_text(encoding="utf-8")), dict)


def _all_cases():
    for service in SERVICES:
        for sample in samples_for(service):
            yield service, sample


@pytest.mark.parametrize("service,sample", list(_all_cases()), ids=lambda x: getattr(x, "name", x))
def test_сообщение_непустое(service: str, sample: pathlib.Path) -> None:
    """Пустое сообщение Пачка не отправляет — уведомление просто исчезнет."""
    payload = json.loads(sample.read_text(encoding="utf-8"))
    assert render(PACHCA / f"{service}.liquid", payload)


@pytest.mark.parametrize("service,sample", list(_all_cases()), ids=lambda x: getattr(x, "name", x))
def test_нет_следов_неподставленных_полей(service: str, sample: pathlib.Path) -> None:
    """
    Ни nil, ни None, ни пустых скобок: если такое вылезло, значит обращение
    к полю не защищено фильтром default.
    """
    text = render(PACHCA / f"{service}.liquid", payload := json.loads(sample.read_text(encoding="utf-8")))
    lowered = text.lower()
    for junk in ("nil", "none", "{{", "}}", "liquid error", "()", "· ·"):
        assert junk not in lowered, f"в сообщении осталось «{junk}»: {text!r}"
    assert payload is not None


@pytest.mark.parametrize("service,sample", list(_all_cases()), ids=lambda x: getattr(x, "name", x))
def test_влезает_в_лимит_пачки(service: str, sample: pathlib.Path) -> None:
    payload = json.loads(sample.read_text(encoding="utf-8"))
    assert len(render(PACHCA / f"{service}.liquid", payload).encode("utf-8")) <= 40_000


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_пустой_payload_не_ломает_шаблон(template: pathlib.Path) -> None:
    """
    Сервис может прислать событие, о котором мы не думали. Шаблон обязан
    что-то сказать, а не упасть и не промолчать.
    """
    assert render(template, {})


def test_размер_считается_в_гигабайтах() -> None:
    """
    Классическая ловушка Liquid: divided_by с целым числом делит нацело,
    и любой фильм оказывается ровно «8 ГБ». Точка в 1073741824.0 — защита
    от этого, и она должна остаться.
    """
    payload = json.loads((PACHCA / "samples/radarr-download.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "radarr.liquid", payload)
    assert "8.4 ГБ" in text, f"дробная часть потерялась: {text!r}"


def test_обновление_отличается_от_нового_фильма() -> None:
    """
    Radarr шлёт eventType=Download и на импорт, и на обновление качества.
    Различает их только isUpgrade — если шаблон это потеряет, сообщения
    станут неотличимы.
    """
    payload = json.loads((PACHCA / "samples/radarr-download.json").read_text(encoding="utf-8"))
    fresh = render(PACHCA / "radarr.liquid", payload)
    upgraded = render(PACHCA / "radarr.liquid", {**payload, "isUpgrade": True})
    assert fresh != upgraded
    assert "библиотек" in fresh.lower()
    assert "качеств" in upgraded.lower()


def test_транскодирование_помечается_предупреждением() -> None:
    """
    На DS725+ нет аппаратного кодировщика: транскодирование занимает оба ядра.
    Это главное, ради чего вообще включено уведомление о начале просмотра.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    assert "транскод" in render(PACHCA / "jellyfin.liquid", payload).lower()

    direct = render(PACHCA / "jellyfin.liquid", {**payload, "playMethod": "DirectPlay"})
    assert "⚠️" not in direct


def test_маршрутизатор_узнаёт_всех_отправителей() -> None:
    """Один бот на всё — запасной вариант, но он должен работать."""
    router = PACHCA / "media-router.liquid"
    expected = {
        "radarr-download.json": "Radarr",
        "prowlarr-health.json": "Prowlarr",
        "jellyfin-transcode.json": "Jellyfin",
        "seerr-available.json": "Seerr",
    }
    for name, marker in expected.items():
        payload = json.loads((PACHCA / "samples" / name).read_text(encoding="utf-8"))
        assert marker in render(router, payload)


def test_маршрутизатор_честно_признаётся_если_не_узнал() -> None:
    text = render(PACHCA / "media-router.liquid", {"чужое": "поле"})
    assert "еопознанн" in text


def test_payload_seerr_остаётся_корректным_json() -> None:
    """
    Seerr подставляет значения прямо в этот JSON. Файл должен разбираться
    и до подстановки — иначе Seerr его не примет.
    """
    payload = json.loads((PACHCA / "payloads/seerr.json").read_text(encoding="utf-8"))
    assert payload["service"] == "seerr"
    assert payload["type"] == "{{notification_type}}"


def test_payload_jellyfin_даёт_корректный_json_после_подстановки() -> None:
    """
    Файл — шаблон Handlebars, до подстановки это не JSON. Проверяем, что
    после замены плейсхолдеров получается разбираемая структура и что
    её поля совпадают с теми, которые ждёт jellyfin.liquid.
    """
    import re

    raw = (PACHCA / "payloads/jellyfin.handlebars").read_text(encoding="utf-8")
    body = re.sub(r"\{\{!--.*?--\}\}", "", raw, flags=re.S)
    substituted = re.sub(r"\{\{\w+\}\}", "значение", body)
    data = json.loads(substituted)

    assert data["service"] == "jellyfin"
    liquid = (PACHCA / "jellyfin.liquid").read_text(encoding="utf-8")
    for field in ("event", "item", "user", "playMethod", "series"):
        assert field in data, f"поле {field} пропало из payload"
        assert field in liquid, f"поле {field} есть в payload, но не используется в шаблоне"