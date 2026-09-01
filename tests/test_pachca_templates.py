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
import re

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
def test_template_compiles(template: pathlib.Path) -> None:
    Environment().from_string(template.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_has_samples(service: str) -> None:
    assert samples_for(service), f"нет ни одного samples/{service}-*.json"


@pytest.mark.parametrize("sample", sorted((PACHCA / "samples").glob("*.json")), ids=lambda p: p.name)
def test_sample_is_valid_json(sample: pathlib.Path) -> None:
    assert isinstance(json.loads(sample.read_text(encoding="utf-8")), dict)


def _all_cases():
    for service in SERVICES:
        for sample in samples_for(service):
            yield service, sample


@pytest.mark.parametrize("service,sample", list(_all_cases()), ids=lambda x: getattr(x, "name", x))
def test_message_is_not_empty(service: str, sample: pathlib.Path) -> None:
    """Пустое сообщение Пачка не отправляет — уведомление просто исчезнет."""
    payload = json.loads(sample.read_text(encoding="utf-8"))
    assert render(PACHCA / f"{service}.liquid", payload)


@pytest.mark.parametrize("service,sample", list(_all_cases()), ids=lambda x: getattr(x, "name", x))
def test_no_unsubstituted_fields_left(service: str, sample: pathlib.Path) -> None:
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
def test_fits_pachca_size_limit(service: str, sample: pathlib.Path) -> None:
    payload = json.loads(sample.read_text(encoding="utf-8"))
    assert len(render(PACHCA / f"{service}.liquid", payload).encode("utf-8")) <= 40_000


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_empty_payload_does_not_break_template(template: pathlib.Path) -> None:
    """
    Сервис может прислать событие, о котором мы не думали. Шаблон обязан
    что-то сказать, а не упасть и не промолчать.
    """
    assert render(template, {})


def test_size_rendered_in_gigabytes() -> None:
    """
    Классическая ловушка Liquid: divided_by с целым числом делит нацело,
    и любой фильм оказывается ровно «8 ГБ». Точка в 1073741824.0 — защита
    от этого, и она должна остаться.
    """
    payload = json.loads((PACHCA / "samples/radarr-download.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "radarr.liquid", payload)
    assert "8.4 ГБ" in text, f"дробная часть потерялась: {text!r}"


def test_upgrade_differs_from_new_movie() -> None:
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


def test_transcoding_is_flagged() -> None:
    """
    На DS725+ нет аппаратного кодировщика: транскодирование занимает оба ядра.
    Это главное, ради чего вообще включено уведомление о начале просмотра.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    assert "транскод" in render(PACHCA / "jellyfin.liquid", payload).lower()

    direct = render(PACHCA / "jellyfin.liquid", {**payload, "playMethod": "DirectPlay"})
    assert "⚠️" not in direct


def test_resolution_uses_width_not_height() -> None:
    """
    Главная ловушка. Кинематографический кадр 2.39:1 в честном 1080p —
    это 1920×804. По высоте его пришлось бы назвать 720p, и уведомление
    врало бы на каждом втором фильме. Ширина у всех форматов одинакова,
    поэтому раскладка идёт по ней.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    assert payload["height"] == "804", "пример потерял широкий кадр — проверка стала бессмысленной"
    text = render(PACHCA / "jellyfin.liquid", payload)
    assert "1080p" in text, f"широкий кадр назван неверно: {text!r}"
    assert "720p" not in text


@pytest.mark.parametrize(
    "width,expected",
    [
        ("3840", "2160p"),
        ("2560", "1440p"),
        ("1920", "1080p"),
        ("1280", "720p"),
        ("1024", "576p"),
        ("720", "480p"),
        ("320", "SD"),
    ],
)
def test_width_maps_to_familiar_label(width: str, expected: str) -> None:
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "jellyfin.liquid", {**payload, "width": width, "height": "0"})
    assert expected in text, f"ширина {width} дала не «{expected}»: {text!r}"


def test_height_used_when_width_missing() -> None:
    """Поле может не приехать; тогда считаем по высоте, а не молчим."""
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "jellyfin.liquid", {**payload, "width": "", "height": "1080"})
    assert "1080p" in text


def test_event_without_file_shows_no_resolution() -> None:
    """
    У входа в систему и блокировки учётной записи видеодорожки нет вовсе.
    Пустые поля не должны превратиться ни в «0p», ни в висящий разделитель.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-authfail.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "jellyfin.liquid", payload)
    for junk in ("0p", "SD", " · \n", "· ·"):
        assert junk not in text, f"в сообщении осталось «{junk}»: {text!r}"


def test_codec_shown_with_human_name() -> None:
    """
    Jellyfin называет кодеки как ffmpeg: hevc, h264. На DS725+ нет
    аппаратного декодера, и по кодеку сразу видно, откуда взялось
    транскодирование — но только если он написан узнаваемо.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-transcode.json").read_text(encoding="utf-8"))
    assert "HEVC" in render(PACHCA / "jellyfin.liquid", payload)
    assert "H.264" in render(PACHCA / "jellyfin.liquid", {**payload, "videoCodec": "h264"})
    assert "AV1" in render(PACHCA / "jellyfin.liquid", {**payload, "videoCodec": "av1"})
    # Незнакомый кодек не должен исчезать — пусть будет хотя бы как есть.
    assert "PRORES" in render(PACHCA / "jellyfin.liquid", {**payload, "videoCodec": "prores"})


def test_resolution_shown_on_library_add() -> None:
    """
    Radarr умеет притащить не то качество. Разрешение в уведомлении
    «появилось в Jellyfin» — самый ранний момент, когда это заметно.
    """
    payload = json.loads((PACHCA / "samples/jellyfin-itemadded.json").read_text(encoding="utf-8"))
    assert "1080p" in render(PACHCA / "jellyfin.liquid", payload)


def test_resolution_fields_in_payload_and_template() -> None:
    """Два файла правятся вместе; проверка на то, что про второй не забыли."""
    payload = (PACHCA / "payloads/jellyfin.handlebars").read_text(encoding="utf-8")
    liquid = (PACHCA / "jellyfin.liquid").read_text(encoding="utf-8")
    for source, target in (("Video_0_Width", "width"), ("Video_0_Height", "height"),
                           ("Video_0_Codec", "videoCodec")):
        assert source in payload, f"{source} пропал из payloads/jellyfin.handlebars"
        assert target in liquid, f"поле {target} не используется в jellyfin.liquid"


SERVARR = ["radarr", "prowlarr"]


@pytest.mark.parametrize("service", SERVARR)
def test_health_fields_read_from_payload_root(service: str) -> None:
    """
    У Radarr и Prowlarr события Health и HealthRestored устроены не как
    остальные: level, message, type и wikiUrl лежат в КОРНЕ payload,
    а не во вложенном объекте. Вложенности health.* не существует —
    в WebhookHealthPayload эти поля объявлены прямо в классе.

    Обращение к health.message синтаксически безупречно, Liquid молча
    отдаёт nil, срабатывает default, и в чат месяцами уходит заголовок
    «проблема со здоровьем» без единой полезной строки. Именно так
    и было, пока не пришло настоящее уведомление про отвалившийся
    индексатор. Тест закрывает возврат к этому.
    """
    text = (PACHCA / f"{service}.liquid").read_text(encoding="utf-8")
    # Комментарии как раз и рассказывают про эти грабли — из проверки их вон.
    code = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", "", text, flags=re.S)
    assert "health." not in code, (
        f"{service}.liquid снова читает health.* — этих полей в payload нет"
    )


@pytest.mark.parametrize("service", SERVARR)
def test_health_message_reaches_the_chat(service: str) -> None:
    """Мало не обращаться к health.* — текст события должен дойти до чата."""
    sample = PACHCA / "samples" / f"{service}-health.json"
    payload = json.loads(sample.read_text(encoding="utf-8"))
    text = render(PACHCA / f"{service}.liquid", payload)
    assert payload["message"] in text, "текст проблемы не попал в сообщение"
    assert payload["type"] in text, "тип проверки не попал в сообщение"
    assert "без описания" not in text


def test_health_restored_says_what_was_wrong() -> None:
    """
    HealthRestored несёт описание той проверки, которая починилась.
    Без него сообщение «снова в порядке» не говорит, что именно чинилось.
    """
    payload = json.loads((PACHCA / "samples/radarr-healthrestored.json").read_text(encoding="utf-8"))
    text = render(PACHCA / "radarr.liquid", payload)
    assert payload["message"] in text


def test_router_recognises_every_sender() -> None:
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


def test_router_admits_unknown_sender() -> None:
    text = render(PACHCA / "media-router.liquid", {"чужое": "поле"})
    assert "еопознанн" in text


def test_seerr_payload_stays_valid_json() -> None:
    """
    Seerr подставляет значения прямо в этот JSON. Файл должен разбираться
    и до подстановки — иначе Seerr его не примет.
    """
    payload = json.loads((PACHCA / "payloads/seerr.json").read_text(encoding="utf-8"))
    assert payload["service"] == "seerr"
    assert payload["type"] == "{{notification_type}}"


def test_jellyfin_payload_valid_json_after_substitution() -> None:
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
