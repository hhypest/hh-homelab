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

Адреса сравниваются по ПЕРЕКРЫТИЮ, а не по равенству. Раньше здесь было
равенство и комментарий, что 127.0.0.1:2375 не спорит с 0.0.0.0:2375, —
это неверно: wildcard занимает порт на всех адресах своего семейства,
и второй bind получает EADDRINUSE независимо от порядка. Проверка молча
пропускала ровно тот случай, ради которого писалась.

Чего проверка НЕ умеет: сервисы в сети хоста занимают порты напрямую,
минуя публикацию, поэтому их конфликты отсюда не видны — они просто
перечисляются в конце.

Номера портов лежат в .env, которого в репозитории нет и не будет. Поэтому
при его отсутствии берётся .env.example — и проверка заодно приобретает
смысл: она гарантирует, что набор портов, который мы предлагаем чужому
человеку, сам с собой не конфликтует.

Использование:
    python3 scripts/check_ports.py media/compose.yaml homeassistant/compose.yaml
"""

from __future__ import annotations

import collections
import json
import pathlib
import subprocess
import sys

HOST_MODE: list[str] = []

# Пустая строка — это тоже «слушать везде»: так compose пишет порт без адреса.
IPV4_ANY = frozenset({"", "0.0.0.0"})
IPV6_ANY = frozenset({"::", "[::]"})


def family(host_ip: str) -> str:
    """Семейство адреса. Двоеточие бывает только в IPv6."""
    return "v6" if ":" in host_ip.strip("[]") else "v4"


def is_any(host_ip: str) -> bool:
    return host_ip in IPV4_ANY or host_ip in IPV6_ANY


def overlaps(a: str, b: str) -> bool:
    """
    Займут ли два bind-адреса один и тот же порт.

    Два конкретных адреса мешают друг другу только если совпадают.
    Wildcard перекрывает любой адрес своего семейства.

    Отдельный случай — `::` против IPv4. На Linux при штатном
    net.ipv6.bindv6only=0 сокет на `::` принимает и IPv4-соединения, то есть
    занимает порт в обоих семействах. Считаем это конфликтом: ложная тревога
    в предполётной проверке стоит минуты, пропущенный конфликт — вечера.
    """
    if a == b:
        return True

    a_any, b_any = is_any(a), is_any(b)
    if not a_any and not b_any:
        return False
    if a_any and b_any:
        return True

    wide, narrow = (a, b) if a_any else (b, a)
    return family(wide) == family(narrow) or family(wide) == "v6"


def find_conflicts(
    entries: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, tuple[str, str], tuple[str, str]]]:
    """Перекрывающиеся пары среди (порт, протокол, адрес, владелец)."""
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = collections.defaultdict(list)
    for port, proto, host_ip, owner in entries:
        grouped[(port, proto)].append((host_ip, owner))

    found = []
    for (port, proto), items in grouped.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if overlaps(items[i][0], items[j][0]):
                    found.append((port, proto, items[i], items[j]))
    return found


def where(host_ip: str) -> str:
    if host_ip in IPV4_ANY:
        return "везде"
    if host_ip in IPV6_ANY:
        return "везде (IPv6)"
    return host_ip


def env_file_for(compose_file: str) -> list[str]:
    """Аргументы --env-file: свой .env, иначе образец, иначе ничего."""
    project = pathlib.Path(compose_file).resolve().parent
    for name in (".env", ".env.example"):
        candidate = project / name
        if candidate.exists():
            return ["--env-file", str(candidate)]
    return []


def published(compose_file: str) -> list[tuple[str, str, str, str]]:
    command = ["docker", "compose", "-f", compose_file]
    command += env_file_for(compose_file)
    command += ["config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"{compose_file}: docker compose config не отработал.\n"
            f"{result.stderr.strip()}"
        )
    config = json.loads(result.stdout)

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

    for port, proto, host_ip, owner in sorted(everything, key=lambda e: (int(e[0]), e[1])):
        print(f"  {port:>6}/{proto:<3} {where(host_ip):<12} {owner}")

    conflicts = find_conflicts(everything)
    if conflicts:
        print(f"\nНайдено конфликтов: {len(conflicts)}\n")
        for port, proto, (ip_a, owner_a), (ip_b, owner_b) in conflicts:
            print(
                f"  ✗ {port}/{proto}: {where(ip_a)} ({owner_a}) "
                f"перекрывается с {where(ip_b)} ({owner_b})"
            )
        return 1

    print(f"\nПроверено опубликованных портов: {len(everything)}. Перекрытий нет.")
    if HOST_MODE:
        print("\nЭтой проверкой НЕ покрыты — занимают порты напрямую, минуя публикацию:")
        for owner in HOST_MODE:
            print(f"  · {owner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
