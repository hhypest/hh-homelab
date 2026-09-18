#!/usr/bin/env python3
"""
Быстрая проверка состояния контейнеров через docker-socket-proxy.

Зачем отдельный скрипт рядом с Monitor Docker
---------------------------------------------
Monitor Docker снимает CPU и память — это дорогая операция (поток статистики
на каждый контейнер), поэтому он опрашивается раз в час. Но узнать о падении
контейнера через час — бессмысленно.

Здесь ровно один дешёвый HTTP-запрос: GET /containers/json?all=1 отдаёт список
контейнеров с их состоянием, без всякой статистики. Такой запрос можно делать
раз в минуту, не нагружая двухъядерный R1600.

Вывод — JSON в одну строку, его читает command_line-сенсор Home Assistant:
  {"down": 1, "running": 7, "total": 8,
   "down_names": ["radarr"],
   "down_detail": ["radarr — Exited (137) 2 minutes ago"],
   "oom": true, "error": ""}

Скрипт никогда не падает: при любой ошибке возвращает JSON с полем error,
чтобы сенсор не уходил в unavailable и об этом можно было уведомить отдельно.

Использование:
    python3 /config/bin/docker_state.py [имя1 имя2 ...]
Без аргументов берётся список WATCHED ниже.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# Адрес по умолчанию — тот, на котором dockerproxy опубликован в compose.yaml.
# Переопределяется переменной окружения: это нужно тестам, чтобы не занимать
# настоящий порт, и пригодится, если прокси однажды переедет.
PROXY = os.environ.get("DOCKER_PROXY_URL", "http://127.0.0.1:2375")


def read_timeout() -> tuple[int, str]:
    """
    Таймаут из окружения. Разбор стоял прямо в присвоении, то есть ДО всякого
    try и до первой строки main(): DOCKER_PROXY_TIMEOUT=8s ронял скрипт
    первым же исполняемым выражением, молча и с пустым выводом — при том,
    что весь смысл файла в обещании «никогда не падать».
    """
    raw = os.environ.get("DOCKER_PROXY_TIMEOUT", "8")
    try:
        return int(raw), ""
    except ValueError:
        return 8, f"DOCKER_PROXY_TIMEOUT={raw!r} — не число"


TIMEOUT, TIMEOUT_PROBLEM = read_timeout()

# Контейнеры, за которыми следим. Держите список в согласии с packages/docker.yaml.
WATCHED = [
    "qbittorrent",
    "prowlarr",
    "radarr",
    "flaresolverr",
    "jellyfin",
    "seerr",
    "homeassistant",
    "dockerproxy",
]


def fail(message: str) -> None:
    print(json.dumps({
        "down": 0, "running": 0, "total": 0,
        "down_names": [], "down_detail": [],
        "oom": False, "restarting": 0, "error": message,
    }, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    watched = sys.argv[1:] or WATCHED

    if TIMEOUT_PROBLEM:
        fail(TIMEOUT_PROBLEM)
        return

    request = urllib.request.Request(
        PROXY + "/containers/json?all=1",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            containers = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        # 403 означает, что в прокси не разрешён CONTAINERS=1
        fail(f"proxy HTTP {err.code}")
        return
    except Exception as err:  # таймаут, отказ соединения, битый JSON
        fail(type(err).__name__)
        return

    # Разбор JSON стоял внутри try, а всё, что с ним делается, — уже снаружи.
    # Прокси, ответивший 200 с объектом вместо списка (сообщение обратного
    # прокси, ошибка маршрутизации, смена версии API), уводил цикл по ключам
    # строкам: AttributeError, пустой stdout, сенсор в unknown — и молчание
    # автоматики «контейнер упал» ровно в тот момент, ради которого она
    # заведена. Отказ прокси при этом обработан образцово, поэтому проверка
    # должна вернуть отказ в то же русло, а не в трассировку.
    if not isinstance(containers, list) or not all(isinstance(c, dict) for c in containers):
        fail("proxy ответил не списком контейнеров")
        return

    # Docker отдаёт имена со слэшем в начале: "/jellyfin"
    found = {}
    for container in containers:
        for raw_name in container.get("Names", []):
            found[raw_name.lstrip("/")] = container

    down_names, down_detail = [], []
    running = 0
    restarting = 0
    oom = False

    for name in watched:
        container = found.get(name)
        if container is None:
            down_names.append(name)
            down_detail.append(f"{name} — контейнера нет")
            continue

        state = (container.get("State") or "").lower()
        status = container.get("Status") or ""
        # «restarting» — собственное состояние Docker: политика перезапуска
        # ждёт перед следующей попыткой. Пауза удваивается с каждым падением
        # и упирается в минуту, поэтому контейнер в цикле почти всегда
        # застаётся именно в нём, а здоровый перезапуск проскакивает это
        # состояние за миллисекунды и в минутный опрос не попадает.
        if state == "restarting":
            restarting += 1
        if state == "running":
            running += 1
        else:
            down_names.append(name)
            down_detail.append(f"{name} — {status or state}")
            # Код 137 = процесс убит сигналом SIGKILL, почти всегда это
            # нехватка памяти. Отдельный флаг, чтобы подсказать причину.
            if "(137)" in status:
                oom = True

    print(json.dumps({
        "down": len(down_names),
        "running": running,
        "total": len(watched),
        "down_names": down_names,
        "down_detail": down_detail,
        "oom": oom,
        "restarting": restarting,
        "error": "",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
