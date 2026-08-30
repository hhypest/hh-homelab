#!/usr/bin/env python3
"""
Проверка, что два стека не спорят за один порт хоста.

Совпадение проявляется невнятно: второй проект просто не поднимется, а в
журнале будет строка про «address already in use» среди сотни других.
Дешевле поймать это до запуска.

Источник данных — вывод `docker compose config`, а не сами файлы: он уже
раскрыл переменные, привёл короткие записи портов к полной форме и знает
про протокол. Порт 6881 в TCP и UDP — не конфликт, и по тексту файла это
не отличить.

Использование:
    python3 scripts/check_ports.py media/compose.yaml homeassistant/compose.yaml
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys

HOST_MODE: list[str] = []


def published(compose_file: str) -> list[tuple[str, str, str, str]]:
    raw = subprocess.run(
        ["docker", "compose", "-f", compose_file, "config", "--format", "json"],
        capture_output=True, text=True, check=True,
    ).stdout
    config = json.loads(raw)

    found = []
    for name, service in (config.get("services") or {}).items():
        # Сервисы в сети хоста портов не публикуют — они занимают их напрямую.
        # Такой конфликт этой проверкой не поймать, поэтому просто называем их.
        if service.get("network_mode") == "host":
            HOST_MODE.append(f"{compose_file}:{name}")
            continue
        for port in service.get("ports") or []:
            if not port.get("published"):
                continue
            found.append((
                str(port["published"]),
                port.get("protocol", "tcp"),
                port.get("host_ip", "0.0.0.0"),
                f"{compose_file}:{name}",
            ))
    return found


def main() -> int:
    files = sys.argv[1:]
    if not files:
        print("Укажите хотя бы один compose-файл")
        return 2

    everything: list[tuple[str, str, str, str]] = []
    for path in files:
        everything.extend(published(path))

    # Ключ — порт, протокол и адрес: 6881/tcp и 6881/udp живут мирно,
    # а 127.0.0.1:2375 не спорит с 0.0.0.0:2375 из другого проекта.
    seen = collections.defaultdict(list)
    for port, proto, host_ip, owner in everything:
        seen[(port, proto, host_ip)].append(owner)

    conflicts = {key: owners for key, owners in seen.items() if len(owners) > 1}

    for (port, proto, host_ip), owners in sorted(seen.items(), key=lambda kv: int(kv[0][0])):
        where = "везде" if host_ip in ("", "0.0.0.0") else host_ip
        print(f"  {port:>6}/{proto:<3} {where:<12} {owners[0]}")

    if conflicts:
        print(f"\nНайдено конфликтов: {len(conflicts)}\n")
        for (port, proto, host_ip), owners in conflicts.items():
            print(f"  ✗ {port}/{proto} на {host_ip or 'всех адресах'} занят дважды: {', '.join(owners)}")
        return 1

    print(f"\nПортов опубликовано: {len(everything)}. Пересечений нет.")
    if HOST_MODE:
        print("\nВ сети хоста (порты занимают напрямую, этой проверкой не покрыты):")
        for owner in HOST_MODE:
            print(f"  · {owner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
