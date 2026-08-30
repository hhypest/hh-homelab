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
TIMEOUT = int(os.environ.get("DOCKER_PROXY_TIMEOUT", "8"))

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
        "oom": False, "error": message,
    }, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    watched = sys.argv[1:] or WATCHED

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

    # Docker отдаёт имена со слэшем в начале: "/jellyfin"
    found = {}
    for container in containers:
        for raw_name in container.get("Names", []):
            found[raw_name.lstrip("/")] = container

    down_names, down_detail = [], []
    running = 0
    oom = False

    for name in watched:
        container = found.get(name)
        if container is None:
            down_names.append(name)
            down_detail.append(f"{name} — контейнера нет")
            continue

        state = (container.get("State") or "").lower()
        status = container.get("Status") or ""
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
        "error": "",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
