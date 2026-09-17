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
ПРЕДРЕЛИЗ = re.compile(
    r"(?:^|[-._])(?:nightly|develop|dev|beta|alpha|rc|pre|unstable|test|edge|"
    r"snapshot|canary|master|main|latest)(?:[-._]|\d|$)",
    re.I,
)
# Хвост вида 2026.9.0b2 — бета Home Assistant.
БЕТА_ХВОСТ = re.compile(r"\d+[ab]\d+$")
# Теги-дайджесты и теги по коммиту: реестр отдаёт их вперемешку с обычными,
# а числа в них к версии отношения не имеют.
ДАЙДЖЕСТ = re.compile(r"^sha\d*[-.]")

ТАЙМАУТ = 30


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


def разобрать(ссылка: str) -> tuple[str, str, str]:
    """`lscr.io/linuxserver/jellyfin:10.11.11ubu2604-ls47` → реестр, репозиторий, тег."""
    голова, _, хвост = ссылка.rpartition("/")
    if ":" not in хвост:
        raise ValueError(f"у образа {ссылка} нет тега")
    имя, тег = хвост.rsplit(":", 1)
    if "/" not in голова or "." not in голова.split("/", 1)[0]:
        raise ValueError(
            f"в ссылке {ссылка} не указан реестр — поддерживаются только полные ссылки"
        )
    реестр, _, путь = голова.partition("/")
    return реестр, f"{путь}/{имя}", тег


def форма(тег: str) -> str:
    """Все цепочки цифр заменяются на N: «5.2.3-ls474» → «N.N.N-lsN»."""
    return re.sub(r"\d+", "N", тег)


def числа(тег: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", тег))


def скелет(тег: str) -> str:
    """
    Форма без учёта того, сколько в версии числовых частей.

    Цифры выбрасываются совсем, а подряд идущие точки схлопываются в одну:
    `10.11.11ubu2604-ls47` и `12.1ubu2604-ls50` дают один и тот же «.ubu-ls».
    Это и есть признак «та же линейка, но переномеровали»: буквы и знаки
    вокруг чисел у сборки не меняются, меняется только их количество.

    Заодно отсекает похожее по числам, но чужое по природе:
    у `sha-8253831` скелет «sha-», у `3.4-pr-186` — «.-pr-».
    """
    return re.sub(r"\.{2,}", ".", re.sub(r"\d+", "", тег))


def релиз(тег: str) -> bool:
    """Похож ли тег на выпуск, а не на ночную сборку или дайджест."""
    if ДАЙДЖЕСТ.match(тег) or БЕТА_ХВОСТ.search(тег):
        return False
    return not ПРЕДРЕЛИЗ.search(тег)


def _токен(вызов: str) -> str | None:
    """Разбирает заголовок WWW-Authenticate и забирает анонимный токен."""
    if not вызов.lower().startswith("bearer "):
        return None
    поля = dict(re.findall(r'(\w+)="([^"]*)"', вызов))
    realm = поля.pop("realm", None)
    if not realm:
        return None
    адрес = f"{realm}?{urllib.parse.urlencode(поля)}" if поля else realm
    with urllib.request.urlopen(адрес, timeout=ТАЙМАУТ) as ответ:
        return json.load(ответ).get("token")


def теги(реестр: str, репозиторий: str) -> list[str]:
    """
    Все теги репозитория, со страницами.

    Реестр отдаёт не больше тысячи за раз и указывает следующую страницу
    заголовком Link. У Home Assistant тегов больше четырёх с половиной
    тысяч, и нужный лежит на последней странице — без обхода проверка
    честно отвечала бы «обновлений нет».
    """
    путь = f"/v2/{репозиторий}/tags/list?n=1000"
    токен: str | None = None
    собрано: list[str] = []

    while путь:
        запрос = urllib.request.Request(f"https://{реестр}{путь}")
        if токен:
            запрос.add_header("Authorization", f"Bearer {токен}")
        try:
            with urllib.request.urlopen(запрос, timeout=ТАЙМАУТ) as ответ:
                собрано += json.load(ответ).get("tags") or []
                следующая = ответ.headers.get("Link")
        except urllib.error.HTTPError as ошибка:
            if ошибка.code == 401 and токен is None:
                токен = _токен(ошибка.headers.get("WWW-Authenticate", ""))
                if токен:
                    continue
            raise
        путь = re.sub(r".*<([^>]*)>.*", r"\1", следующая) if следующая else ""
    return собрано


def сравнить(закреплён: str, доступные: list[str]) -> tuple[list[str], list[str]]:
    """
    Возвращает (новее той же формы, теги изменившейся формы).

    Вторая половина заполняется, только когда обновлений своей формы нет:
    иначе она была бы шумом на каждом образе.
    """
    наша = форма(закреплён)
    эталон = числа(закреплён)

    новее = sorted(
        (т for т in доступные if форма(т) == наша and числа(т) > эталон),
        key=числа,
    )
    if новее:
        return новее, []

    наш_скелет = скелет(закреплён)
    иные = sorted(
        {
            т for т in доступные
            if форма(т) != наша
            and скелет(т) == наш_скелет
            and релиз(т)
            and числа(т) > эталон
        },
        key=числа,
    )
    return [], иные


def main() -> int:
    разбор = argparse.ArgumentParser(description="Сверка версий образов с реестром")
    разбор.add_argument("--образ", help="проверить только образы, чьё имя содержит эту строку")
    доводы = разбор.parse_args()

    отстали: list[str] = []
    внимание: list[str] = []

    for файл in COMPOSE:
        for имя, ссылка in images(файл).items():
            if доводы.образ and доводы.образ.lower() not in ссылка.lower():
                continue
            try:
                реестр, репозиторий, закреплён = разобрать(ссылка)
                доступные = теги(реестр, репозиторий)
            except (urllib.error.URLError, ValueError, OSError) as ошибка:
                print(f"  ОШИБКА {имя}: {ошибка}")
                return 2

            новее, иные = сравнить(закреплён, доступные)
            if новее:
                отстали.append(f"{имя}: {закреплён} → {новее[-1]}")
                хвост = f"новее: {', '.join(новее[-3:])}"
                print(f"  ОТСТАЛ  {имя:<28} {закреплён:<26} {хвост}")
            elif иные:
                внимание.append(f"{имя}: форма тега изменилась")
                примеры = ", ".join(иные[-3:])
                print(f"  СМОТРЕТЬ {имя:<27} {закреплён:<26} другая форма: {примеры}")
            else:
                print(f"  свежий  {имя:<28} {закреплён}")

    print()
    if not отстали and not внимание:
        print(f"Все образы на свежих версиях. Проверено: "
              f"{sum(len(images(f)) for f in COMPOSE)}.")
        return 0

    if отстали:
        print(f"Отстали ({len(отстали)}):")
        for строка in отстали:
            print(f"  · {строка}")
    if внимание:
        print(f"\nТребуют человека ({len(внимание)}): форма тега изменилась, "
              f"машине ранжировать нельзя.")
        for строка in внимание:
            print(f"  · {строка}")
        print("  Схема нумерации могла поменяться — прочитайте release notes,")
        print("  прежде чем поднимать версию.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
