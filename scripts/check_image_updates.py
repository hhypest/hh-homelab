#!/usr/bin/env python3
"""
Сверка закреплённых версий образов с тем, что лежит в реестре.

Зачем отдельная проверка, если есть Dependabot
---------------------------------------------
Затем, что закрепление версий держится на том, кто приносит обновления,
а этот кто-то может молча перестать приходить. Экосистема docker-compose
была заведена 2 сентября 2026 года и за две недели не завела ни одного
пул-реквеста, хотя вперёд ушли пять образов. Причину установить не
удалось: имя экосистемы, регулярное выражение имён файлов, ключ
directories, доступность реестра и наличие свежих тегов — всё проверено
и всё верно. Проверка ниже не зависит от Dependabot вовсе.

Второе: образы из DOCKER_MODS Dependabot не увидит ни при каком исходе —
он разбирает ключ image, а мод спрятан в переменной окружения. Здесь
разбирается и то, и другое, потому что список образов берётся из общей
функции images(), которой пользуется и tests/test_compose_images.py.

Как решается, что тег новее
---------------------------
Теги здесь непохожи на semver: `5.2.3_v2.0.14-ls474`, `10.11.11ubu2604-ls47`,
`2026.9.0`. Сравнивать их как строки бессмысленно, а угадывать структуру
опасно. Поэтому правило простое: у тега берётся ФОРМА — все цифры
заменяются на N, — и сравниваются только теги одинаковой формы, по кортежу
чисел в них.

    5.2.3_v2.0.14-ls474  →  N.N.N_vN.N.N-lsN  →  (5, 2, 3, 2, 0, 14, 474)
    5.2.3_v2.0.14-ls476  →  та же форма       →  (5, 2, 3, 2, 0, 14, 476)  новее

Побочная польза: предрелизы отсеиваются сами. `nightly-2.6.5.5620-ls16`,
`develop-…`, `libtorrentv1-…`, `2026.9.0b2`, `2026.9.0.dev20260901` — всё
это другие формы, и в сравнение они не попадают.

Форма меняется не только у мусора. Jellyfin выкинул ведущую «10.» из схемы
версий, и `10.11.11ubu2604-ls47` превратился в `12.1ubu2604-ls50`. Ранжировать
такое машине нельзя — но и промолчать нельзя, потому что это самое важное
обновление из всех. Поэтому теги изменившейся формы попадают в отдельный
раздел «форма тега изменилась», без вердикта: смотрит человек.

Использование:
    python3 scripts/check_image_updates.py
    python3 scripts/check_image_updates.py --образ jellyfin

Код возврата: 0 — всё свежее, 1 — что-то отстало или требует внимания,
2 — не удалось опросить реестр.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent

COMPOSE = ["media/compose.yaml", "homeassistant/compose.yaml"]

# Ссылки на образы прячутся не только в ключе image: у linuxserver-образов
# есть DOCKER_MODS, и эти образы init скачивает при каждом старте контейнера.
IMAGE_ENV = ("DOCKER_MODS", "UNIVERSAL_MODS")

# Слова, по которым тег опознаётся как не-релиз. Форма отсеивает почти всё,
# но в разделе «форма изменилась» фильтровать приходится явно.
PRERELEASE = re.compile(
    r"(?:^|[-._])(?:nightly|develop|dev|beta|alpha|rc|pre|unstable|test|edge|"
    r"snapshot|canary|master|main|latest)(?:[-._]|\d|$)",
    re.I,
)
# Хвост вида 2026.9.0b2 — бета Home Assistant.
BETA_SUFFIX = re.compile(r"\d+[ab]\d+$")
# Теги-дайджесты и теги по коммиту: реестр отдаёт их вперемешку с обычными,
# а числа в них к версии отношения не имеют.
DIGEST = re.compile(r"^sha\d*[-.]")

TIMEOUT = 30


def images(path: str) -> dict[str, str]:
    """
    Все ссылки на образы файла: и сам image, и образы модов из окружения.

    Единственное место, где это знание записано: тот же разбор использует
    tests/test_compose_images.py, чтобы список образов не разъехался
    между проверкой и тестом.
    """
    data = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for name, service in (data.get("services") or {}).items():
        if "image" in service:
            found[name] = service["image"]
        for entry in service.get("environment") or []:
            if not isinstance(entry, str) or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            if key.strip() not in IMAGE_ENV:
                continue
            for index, image in enumerate(v for v in value.split("|") if v.strip()):
                found[f"{name} · {key.strip()}[{index}]"] = image.strip()
    return found


def parse(link: str) -> tuple[str, str, str]:
    """`lscr.io/linuxserver/jellyfin:10.11.11ubu2604-ls47` → реестр, репозиторий, тег."""
    head, _, tail = link.rpartition("/")
    if ":" not in tail:
        raise ValueError(f"у образа {link} нет тега")
    name, tag = tail.rsplit(":", 1)
    if "/" not in head or "." not in head.split("/", 1)[0]:
        raise ValueError(
            f"в ссылке {link} не указан реестр — поддерживаются только полные ссылки"
        )
    registry, _, path_str = head.partition("/")
    return registry, f"{path_str}/{name}", tag


def shape(tag: str) -> str:
    """Все цепочки цифр заменяются на N: «5.2.3-ls474» → «N.N.N-lsN»."""
    return re.sub(r"\d+", "N", tag)


def numbers(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def skeleton(tag: str) -> str:
    """
    Форма без учёта того, сколько в версии числовых частей.

    Цифры выбрасываются совсем, а подряд идущие точки схлопываются в одну:
    `10.11.11ubu2604-ls47` и `12.1ubu2604-ls50` дают один и тот же «.ubu-ls».
    Это и есть признак «та же линейка, но переномеровали»: буквы и знаки
    вокруг чисел у сборки не меняются, меняется только их количество.

    Заодно отсекает похожее по числам, но чужое по природе:
    у `sha-8253831` скелет «sha-», у `3.4-pr-186` — «.-pr-».
    """
    return re.sub(r"\.{2,}", ".", re.sub(r"\d+", "", tag))


def release(tag: str) -> bool:
    """Похож ли тег на выпуск, а не на ночную сборку или дайджест."""
    if DIGEST.match(tag) or BETA_SUFFIX.search(tag):
        return False
    return not PRERELEASE.search(tag)


def _token(call: str) -> str | None:
    """Разбирает заголовок WWW-Authenticate и забирает анонимный токен."""
    if not call.lower().startswith("bearer "):
        return None
    fields = dict(re.findall(r'(\w+)="([^"]*)"', call))
    realm = fields.pop("realm", None)
    if not realm:
        return None
    address = f"{realm}?{urllib.parse.urlencode(fields)}" if fields else realm
    with urllib.request.urlopen(address, timeout=TIMEOUT) as answer:
        return json.load(answer).get("token")


def tags(registry: str, repo: str) -> list[str]:
    """
    Все теги репозитория, со страницами.

    Реестр отдаёт не больше тысячи за раз и указывает следующую страницу
    заголовком Link. У Home Assistant тегов больше четырёх с половиной
    тысяч, и нужный лежит на последней странице — без обхода проверка
    честно отвечала бы «обновлений нет».
    """
    path_str = f"/v2/{repo}/tags/list?n=1000"
    token: str | None = None
    collected: list[str] = []

    while path_str:
        request = urllib.request.Request(f"https://{registry}{path_str}")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                collected += json.load(answer).get("tags") or []
                next_one = answer.headers.get("Link")
        except urllib.error.HTTPError as error:
            if error.code == 401 and token is None:
                token = _token(error.headers.get("WWW-Authenticate", ""))
                if token:
                    continue
            raise
        path_str = re.sub(r".*<([^>]*)>.*", r"\1", next_one) if next_one else ""
    return collected


def compare(pinned: str, available: list[str]) -> tuple[list[str], list[str]]:
    """
    Возвращает (новее той же формы, теги изменившейся формы).

    Вторая половина заполняется, только когда обновлений своей формы нет:
    иначе она была бы шумом на каждом образе.
    """
    ours = shape(pinned)
    reference = numbers(pinned)

    newer = sorted(
        (t for t in available if shape(t) == ours and numbers(t) > reference),
        key=numbers,
    )
    if newer:
        return newer, []

    our_skeleton = skeleton(pinned)
    others = sorted(
        {
            t for t in available
            if shape(t) != ours
            and skeleton(t) == our_skeleton
            and release(t)
            and numbers(t) > reference
        },
        key=numbers,
    )
    return [], others


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверка версий образов с реестром")
    # Ключ остаётся русским: это интерфейс, а не имя в коде. А вот dest
    # обязателен — иначе argparse выведет имя атрибута из самого ключа
    # и положит в namespace «образ», тогда как читается args.image.
    parser.add_argument(
        "--образ", dest="image",
        help="проверить только образы, чьё имя содержит эту строку",
    )
    args = parser.parse_args()

    behind: list[str] = []
    attention: list[str] = []

    for path in COMPOSE:
        for name, link in images(path).items():
            if args.image and args.image.lower() not in link.lower():
                continue
            try:
                registry, repo, pinned = parse(link)
                available = tags(registry, repo)
            except (urllib.error.URLError, ValueError, OSError) as error:
                print(f"  ОШИБКА {name}: {error}")
                return 2

            newer, others = compare(pinned, available)
            if newer:
                behind.append(f"{name}: {pinned} → {newer[-1]}")
                tail = f"новее: {', '.join(newer[-3:])}"
                print(f"  ОТСТАЛ  {name:<28} {pinned:<26} {tail}")
            elif others:
                attention.append(f"{name}: форма тега изменилась")
                samples = ", ".join(others[-3:])
                print(f"  СМОТРЕТЬ {name:<27} {pinned:<26} другая форма: {samples}")
            else:
                print(f"  свежий  {name:<28} {pinned}")

    print()
    if not behind and not attention:
        print(f"Все образы на свежих версиях. Проверено: "
              f"{sum(len(images(f)) for f in COMPOSE)}.")
        return 0

    if behind:
        print(f"Отстали ({len(behind)}):")
        for line in behind:
            print(f"  · {line}")
    if attention:
        print(f"\nТребуют человека ({len(attention)}): форма тега изменилась, "
              f"машине ранжировать нельзя.")
        for line in attention:
            print(f"  · {line}")
        print("  Схема нумерации могла поменяться — прочитайте release notes,")
        print("  прежде чем поднимать версию.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
